"""Takes in one Health Auto Export post: streams it to disk under a size cap, hashes it, keeps a gzipped copy, records a row."""

import gzip
import hashlib
import os
import secrets
import shutil

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.models import HealthRaw

KEPT_HEADERS = ("automation-name", "automation-id", "automation-aggregation", "automation-period", "session-id")


def _gzip_file(src: str, dest: str) -> None:
    with open(src, "rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, 1024 * 1024)


def _unlink(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _find(session: Session, owner_id: int, sha: str) -> HealthRaw | None:
    return session.scalar(select(HealthRaw).where(HealthRaw.owner_id == owner_id, HealthRaw.sha256 == sha))


async def store_body(request: Request, owner_id: int, session: Session) -> tuple[HealthRaw, bool]:
    """(row, duplicate). The body is never held whole in memory; more than HEALTH_MAX_BYTES is a 413 the moment it is seen."""
    limit = config.HEALTH_MAX_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:            # a declared size over the cap is refused before reading anything
        raise HTTPException(413, "Too large")
    directory = config.HEALTH_RAW_DIR
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f"incoming-{secrets.token_hex(8)}.tmp"
    final = None
    try:
        digest, size, looks_json = hashlib.sha256(), 0, None
        with open(tmp, "wb") as out:
            async for chunk in request.stream():                # a chunked body has no declared length, so count as it arrives
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "Too large")
                if looks_json is None and chunk.strip():
                    looks_json = chunk.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"[")      # a UTF-8 byte-order mark is no reason to doubt it
                digest.update(chunk)
                out.write(chunk)
        sha = digest.hexdigest()
        existing = _find(session, owner_id, sha)
        if existing is not None:
            return existing, True
        final = directory / f"{sha}-{secrets.token_hex(4)}.json.gz"
        await run_in_threadpool(_gzip_file, str(tmp), str(final))
        headers = {k: request.headers[k][:200] for k in KEPT_HEADERS if k in request.headers}
        raw = HealthRaw(owner_id=owner_id, sha256=sha, size_bytes=size, file_name=final.name, status="stored" if looks_json else "flagged",
                        note=None if looks_json else "The body does not look like JSON", headers=headers or None,
                        automation_name=headers.get("automation-name", "")[:120] or None)
        session.add(raw)
        try:
            session.commit()
        except IntegrityError:                                  # a twin of this post won the race: its row stands
            session.rollback()
            existing = _find(session, owner_id, sha)
            if existing is None:
                raise
            return existing, True
        final = None                                            # kept: the row owns the file now
        return raw, False
    finally:
        if final is not None:
            _unlink(final)
        _unlink(tmp)

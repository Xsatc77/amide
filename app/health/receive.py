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
from app.health import parse
from app.models import HealthRaw, naive_utcnow

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


QUOTA_NOTE = "raw copy removed to stay within the storage limit"
NOT_JSON_NOTE = "The body does not look like JSON; it was not kept"


def _make_room(session: Session, owner_id: int, size: int, sha: str | None = None) -> bool:
    """Keeps the owner's stored raw copies (uncompressed sizes) within HEALTH_RAW_QUOTA_BYTES with `size` more to come, by dropping the
    oldest copies already read in full ("parsed"; their readings stay) and then the oldest that will not be read by themselves: flagged
    ones, and "stored" ones a crash left unread for over parse.STALE (a younger one may be being read right now). The copy of the body
    being stored (`sha`) is never one of them. False, with nothing removed, when even that would not be enough."""
    quota = config.HEALTH_RAW_QUOTA_BYTES
    kept = session.scalars(select(HealthRaw).where(HealthRaw.owner_id == owner_id, HealthRaw.file_name != "")
                           .order_by(HealthRaw.received_at, HealthRaw.id)).all()
    used = sum(r.size_bytes for r in kept)
    if used + size <= quota:
        return True
    stale = naive_utcnow() - parse.STALE
    spare = [r for r in kept if r.sha256 != sha and r.status == "parsed"]
    spare += [r for r in kept if r.sha256 != sha and (r.status == "flagged" or (r.status == "stored" and r.received_at < stale))]
    if used - sum(r.size_bytes for r in spare) + size > quota:
        return False
    for raw in spare:
        if used + size <= quota:
            break
        _unlink(config.HEALTH_RAW_DIR / raw.file_name)
        if raw.status != "parsed":                              # not read in full: say so on the Settings card
            raw.status, raw.note = "flagged", f"{QUOTA_NOTE} before it was read in full{f' ({raw.note})' if raw.note else ''}"[:300]
        else:
            raw.note = QUOTA_NOTE
        raw.file_name, used = "", used - raw.size_bytes
    session.commit()
    return True


async def store_body(request: Request, owner_id: int, session: Session) -> tuple[HealthRaw, bool]:
    """(row, duplicate). The body is never held whole in memory; more than HEALTH_MAX_BYTES is a 413 the moment it is seen, and a body
    the owner's raw-storage quota cannot take is a 507. A body that does not look like JSON gets a flagged row but no stored file."""
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
        existing = await run_in_threadpool(_find, session, owner_id, sha)      # database work off the event loop, like the writes
        if existing is not None:
            if existing.file_name and existing.status != "parsed" and not (directory / existing.file_name).exists():
                await run_in_threadpool(_gzip_file, str(tmp), str(directory / existing.file_name))     # the same bytes again: restore the lost copy
            return existing, True
        headers = {k: request.headers[k][:200] for k in KEPT_HEADERS if k in request.headers}
        raw = HealthRaw(owner_id=owner_id, sha256=sha, size_bytes=size, file_name="", status="stored" if looks_json else "flagged",
                        note=None if looks_json else NOT_JSON_NOTE, headers=headers or None,
                        automation_name=headers.get("automation-name", "")[:120] or None)
        if looks_json:
            if not await run_in_threadpool(_make_room, session, owner_id, size, sha):
                raise HTTPException(507, "Over the storage limit for raw Apple Health copies")
            final = directory / f"{sha}-{secrets.token_hex(4)}.json.gz"
            await run_in_threadpool(_gzip_file, str(tmp), str(final))
            raw.file_name = final.name
        session.add(raw)
        try:
            await run_in_threadpool(session.commit)
        except IntegrityError:                                  # a twin of this post won the race: its row stands
            session.rollback()
            existing = await run_in_threadpool(_find, session, owner_id, sha)
            if existing is None:
                raise
            return existing, True
        final = None                                            # kept: the row owns the file now
        return raw, False
    finally:
        if final is not None:
            _unlink(final)
        _unlink(tmp)

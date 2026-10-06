"""Receiving a message: store what is a price-list candidate, skip the rest, and never process the same file twice."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.ingest.detect import detect_kind
from app.models import IngestItem, IngestSource, naive_utcnow


def _group_key(session: Session, source: IngestSource, kind: str, message_id: str, album_id: str | None, digest: str, now: datetime) -> str:
    if kind != "image":
        return f"{source.id}:m:{message_id}:{digest[:8]}"
    if album_id:
        return f"{source.id}:a:{album_id}"
    since = now - timedelta(minutes=config.INGEST_CLUSTER_MINUTES)       # lone photos close together are one list
    recent = session.scalar(select(IngestItem).where(
        IngestItem.source_id == source.id, IngestItem.kind == "image", IngestItem.status == "received", IngestItem.album_id.is_(None),
        IngestItem.created_at >= since).order_by(IngestItem.created_at.desc()))
    return recent.group_key if recent is not None else f"{source.id}:t:{uuid.uuid4().hex[:12]}"


def _save(session: Session, item: IngestItem) -> bool:
    session.add(item)
    try:
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def ingest_message(session: Session, source: IngestSource, *, message_id: str, album_id: str | None, received_at: datetime,
                   text: str | None, files: list[tuple[str, bytes]]) -> list[dict]:
    results: list[dict] = []
    now = naive_utcnow()
    caption = (text or "").strip() or None
    for filename, data in files:
        if len(data) > config.INGEST_MAX_FILE_BYTES:
            results.append({"status": "rejected", "reason": "file too large", "item_id": None})
            continue
        kind = detect_kind(data)
        if kind is None:
            results.append({"status": "ignored", "reason": "not a price-list file type", "item_id": None})
            continue
        digest = hashlib.sha256(data).hexdigest()
        if session.scalar(select(IngestItem.id).where(IngestItem.source_id == source.id, IngestItem.message_id == message_id,
                                                      IngestItem.file_hash == digest)) is not None:
            results.append({"status": "duplicate", "reason": "already received", "item_id": None})
            continue
        config.ensure_dirs()
        stored = secrets.token_hex(16)
        (config.INGEST_DIR / stored).write_bytes(data)
        item = IngestItem(source_id=source.id, message_id=message_id, album_id=album_id, received_at=received_at,
                          filename=(filename or "")[:200] or None, kind=kind, file_hash=digest, stored_file=stored, caption=caption,
                          group_key=_group_key(session, source, kind, message_id, album_id, digest, now), status="received", created_at=now)
        if _save(session, item):
            results.append({"status": "received", "reason": None, "item_id": item.id})
        else:
            (config.INGEST_DIR / stored).unlink(missing_ok=True)
            results.append({"status": "duplicate", "reason": "already received", "item_id": None})
    if caption and not files:
        digest = hashlib.sha256(caption.encode("utf-8")).hexdigest()
        item = IngestItem(source_id=source.id, message_id=message_id, album_id=None, received_at=received_at, filename=None, kind="text",
                          file_hash=digest, stored_file=None, caption=caption, group_key=f"{source.id}:m:{message_id}:{digest[:8]}",
                          status="received", created_at=now)
        if _save(session, item):
            results.append({"status": "received", "reason": None, "item_id": item.id})
        else:
            results.append({"status": "duplicate", "reason": "already received", "item_id": None})
    return results

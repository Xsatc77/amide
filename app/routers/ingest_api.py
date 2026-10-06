"""The token-protected API the price-list watcher uses. It only stores what it is given; reading and deciding happen later."""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import config
from app.db import get_session
from app.ingest import store, tokens
from app.models import IngestSource, IngestToken, naive_utcnow

router = APIRouter(prefix="/api/ingest")


def require_token(request: Request, session: Session = Depends(get_session)) -> IngestToken:
    token = tokens.find_token(session, request.headers.get("authorization"))
    if token is None:
        raise HTTPException(401, "Unauthorized")
    if not tokens.limiter.allow(token.id, time.monotonic()):
        raise HTTPException(429, "Too many requests")
    token.last_used_at = naive_utcnow()
    session.commit()
    return token


def _source(session: Session, chat_id: str) -> IngestSource | None:
    return session.scalar(select(IngestSource).where(IngestSource.platform == "telegram", IngestSource.chat_id == chat_id))


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "JSON body required") from None
    return body if isinstance(body, dict) else {}


@router.put("/sources/{chat_id}")
async def register_source(chat_id: str, request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    title = str((await _json(request)).get("title") or "").strip()[:200]
    if not title:
        raise HTTPException(422, "title is required")
    source = _source(session, chat_id)
    if source is None:
        source = IngestSource(platform="telegram", chat_id=chat_id[:64], title=title)
        session.add(source)
    else:
        source.title = title
    session.commit()
    return {"chat_id": source.chat_id, "title": source.title, "enabled": source.enabled, "mapped": source.vendor_id is not None}


@router.get("/sources")
def list_sources(session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    rows = session.scalars(select(IngestSource).where(IngestSource.enabled.is_(True), IngestSource.vendor_id.is_not(None))
                           .order_by(IngestSource.id)).all()
    return [{"chat_id": s.chat_id, "title": s.title} for s in rows]


@router.post("/sources/{chat_id}/state")
async def report_state(chat_id: str, request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    body = await _json(request)
    state = body.get("state")
    if state not in ("active", "gone"):
        raise HTTPException(422, "state must be active or gone")
    source = _source(session, chat_id)
    if source is None:
        raise HTTPException(404, "unknown source")
    if source.state != state:
        source.state, source.state_changed_at = state, naive_utcnow()
    source.state_reason = (str(body.get("reason") or "")[:200] or None) if state == "gone" else None
    session.commit()
    return {"chat_id": source.chat_id, "state": source.state}


@router.post("/messages")
async def receive_message(request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    form = await request.form()
    source = _source(session, str(form.get("chat_id") or ""))
    if source is None:
        raise HTTPException(404, "unknown source")
    if not source.enabled or source.vendor_id is None:
        raise HTTPException(409, "source is disabled or has no vendor")
    message_id = str(form.get("message_id") or "").strip()[:64]
    try:
        received_at = datetime.fromisoformat(str(form.get("date") or "").replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "date must be ISO 8601") from None
    if received_at.tzinfo is not None:
        received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
    text = str(form.get("text") or "")
    uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
    if not message_id or len(uploads) > config.INGEST_MAX_FILES or len(text) > config.INGEST_MAX_TEXT:
        raise HTTPException(422, "message_id is required; at most 10 files and 8000 characters of text")
    files = [(f.filename or "", await f.read(config.INGEST_MAX_FILE_BYTES + 1)) for f in uploads]
    album = str(form.get("album_id") or "").strip()[:64] or None
    return {"results": store.ingest_message(session, source, message_id=message_id, album_id=album, received_at=received_at, text=text, files=files)}

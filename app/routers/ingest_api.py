"""The token-protected API the price-list watcher uses. It only stores what it is given; reading and deciding happen later."""

import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import config
from app.db import get_session
from app.ingest import process, skip, store, tokens
from app.models import IngestSource, IngestToken, IngestTopic, naive_utcnow

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


_TOPIC_ID = re.compile(r"[0-9]{1,32}")
MAX_TOPICS = 200


def _topics_from(body: dict) -> list[tuple[str, str]]:
    raw = body.get("topics")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_TOPICS:
        raise HTTPException(422, f"topics must be a list of at most {MAX_TOPICS}")
    out = []
    for row in raw:
        topic_id = str(row.get("id", "")).strip() if isinstance(row, dict) else ""
        title = str(row.get("title") or "").strip()[:200] if isinstance(row, dict) else ""
        if not _TOPIC_ID.fullmatch(topic_id) or not title:
            raise HTTPException(422, "each topic needs a numeric id and a title")
        out.append((topic_id, title))
    return out


def _starts_ticked(source: IngestSource, title: str) -> bool:
    """A new topic named like the group's follow words starts ticked, unless a skip word is in its name."""
    follows = skip.find_skip_word(skip.safe_words(source.follow_words), [title]) is not None
    return follows and skip.find_skip_word(skip.safe_words(source.skip_words), [title]) is None


def _remember_topic(session: Session, source: IngestSource, topic_id: str, title: str) -> tuple[IngestTopic, bool]:
    topic = session.scalar(select(IngestTopic).where(IngestTopic.source_id == source.id, IngestTopic.topic_id == topic_id))
    created = topic is None
    if topic is None:
        topic = IngestTopic(source_id=source.id, topic_id=topic_id, title=title, enabled=_starts_ticked(source, title))
        session.add(topic)
    elif title and topic.title != title:
        topic.title = title                                   # a rename refreshes the title only, never the tick
    return topic, created


@router.put("/sources/{chat_id}")
async def register_source(chat_id: str, request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    body = await _json(request)
    title = str(body.get("title") or "").strip()[:200]
    if not title:
        raise HTTPException(422, "title is required")
    topics = _topics_from(body)
    source = _source(session, chat_id)
    if source is None:
        source = IngestSource(platform="telegram", chat_id=chat_id[:64], title=title)
        session.add(source)
    else:
        source.title = title
    session.flush()
    for topic_id, topic_title in topics:
        _remember_topic(session, source, topic_id, topic_title)
    session.commit()
    return {"chat_id": source.chat_id, "title": source.title, "enabled": source.enabled, "mapped": source.vendor_id is not None}


@router.get("/sources")
def list_sources(session: Session = Depends(get_session), token: IngestToken = Depends(require_token)):
    rows = session.scalars(select(IngestSource).where(IngestSource.enabled.is_(True), IngestSource.vendor_id.is_not(None))
                           .order_by(IngestSource.id)).all()
    ticked: dict[int, list[str]] = {}
    for t in session.scalars(select(IngestTopic).where(IngestTopic.enabled.is_(True)).order_by(IngestTopic.id)):
        ticked.setdefault(t.source_id, []).append(t.topic_id)
    return [{"chat_id": s.chat_id, "title": s.title, "topics": ticked.get(s.id, []) if s.topics_only else None} for s in rows]


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
    topic_id = str(form.get("topic_id") or "").strip()[:32] or None
    topic_title = str(form.get("topic_title") or "").strip()[:200] or None
    if topic_id and not _TOPIC_ID.fullmatch(topic_id):
        raise HTTPException(422, "topic_id must be numeric")
    remembered, created = None, False
    if topic_id:
        remembered, created = _remember_topic(session, source, topic_id, topic_title or f"Topic {topic_id}")
        session.commit()
    if source.topics_only and (remembered is None or created or not remembered.enabled):      # a topic seen for the first time waits for the next message
        return {"results": [{"status": "ignored", "reason": "topic not followed", "item_id": None}]}
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

    def locked():                           # never while the worker is reading a list: a late photo must see its list as decided
        with process.lock:
            return store.ingest_message(session, source, message_id=message_id, album_id=album, received_at=received_at, text=text, files=files,
                                        topic_id=topic_id, topic_title=topic_title)
    return {"results": await run_in_threadpool(locked)}

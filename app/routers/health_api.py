"""The token-protected endpoint Health Auto Export posts to. It keeps the file raw first, then asks the parser to read it."""

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app import config
from app.db import get_session
from app.health import parse, receive
from app.ingest import tokens
from app.models import IngestToken, naive_utcnow

router = APIRouter(prefix="/api/ingest")
limiter = tokens.RateLimiter(config.HEALTH_RATE_PER_MINUTE, 60.0)       # its own counter, apart from the watcher's


def require_health_token(request: Request, session: Session = Depends(get_session)) -> IngestToken:
    token = tokens.find_token(session, request.headers.get("authorization"), scope="health")
    if token is None:
        raise HTTPException(401, "Unauthorized")
    if not limiter.allow(token.id, time.monotonic()):
        raise HTTPException(429, "Too many requests")
    token.last_used_at = naive_utcnow()
    session.commit()
    return token


_busy_owners: set[int] = set()          # owners with an import running right now: one at a time each, so a flood cannot pile up parsers


@router.post("/health")
async def receive_health(request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_health_token)):
    if token.owner_id in _busy_owners:
        raise HTTPException(429, "An import is already running; try again shortly", headers={"Retry-After": "30"})
    _busy_owners.add(token.owner_id)
    try:
        return await _receive(request, session, token)
    finally:
        _busy_owners.discard(token.owner_id)


async def _receive(request: Request, session: Session, token: IngestToken):
    raw, duplicate = await receive.store_body(request, token.owner_id, session)
    if (duplicate and raw.status == "parsed") or not raw.file_name:
        readings = raw.readings_count       # already read, or nothing kept to read (a body that was not JSON)
    else:                                   # new, or a repeat the parser has not finished with: (re)read it
        readings = await run_in_threadpool(parse.import_raw, session, raw)
        if parse.import_failed(raw):        # not bad content but a failure on our side: a 503 makes the phone send this batch again
            raise HTTPException(503, "The import failed; send it again later", headers={"Retry-After": "300"})
    return {"received": raw.size_bytes, "readings": readings, "duplicate": duplicate}

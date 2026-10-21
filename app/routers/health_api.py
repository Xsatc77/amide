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


@router.post("/health")
async def receive_health(request: Request, session: Session = Depends(get_session), token: IngestToken = Depends(require_health_token)):
    raw, duplicate = await receive.store_body(request, token.owner_id, session)
    if duplicate and raw.status == "parsed":
        readings = raw.readings_count
    else:                                   # new, or a repeat the parser has not finished with: (re)read it
        readings = await run_in_threadpool(parse.import_raw, session, raw)
    return {"received": raw.size_bytes, "readings": readings, "duplicate": duplicate}

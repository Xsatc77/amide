"""Apple Health: the person's own tokens for the Health Auto Export app (the receiver and the settings card come later)."""

import threading
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.deps import current_user_id
from app.db import get_session
from app.health import choices
from app.ingest import tokens
from app.models import HealthEvent, HealthRaw, HealthReading, IngestToken, naive_utcnow

router = APIRouter()
BACK = "/settings#integrations"

# A new token's secret is kept here for one look, per browser session: the redirect target reads it and it is gone.
# Assumes a single process (as Amide runs): with several workers a redirect could land on a worker that never saw the token.
# Entries expire after NEW_TOKEN_TTL seconds; stale ones are dropped on every write, so the store cannot grow without bound.
NEW_TOKEN_TTL = 300
_new_tokens: dict[str, tuple[str, str, float]] = {}      # session id -> (secret, label, stored at)
_lock = threading.Lock()


def _remember(session_id: str, secret: str, label: str) -> None:
    now = time.monotonic()
    with _lock:
        for sid in [k for k, v in _new_tokens.items() if now - v[2] > NEW_TOKEN_TTL]:
            del _new_tokens[sid]
        _new_tokens[session_id] = (secret, label, now)


def take_new_token(session_id: str | None) -> tuple[str, str] | None:
    """(secret, label) of the token this browser just created, once; None afterwards."""
    if not session_id:
        return None
    with _lock:
        entry = _new_tokens.pop(session_id, None)
    if entry is None or time.monotonic() - entry[2] > NEW_TOKEN_TTL:
        return None
    return entry[0], entry[1]


# (kind, one, many) for the Settings card's "Also kept" line: the listed kinds first, then the four that are stored only
EVENT_LABELS = (("workout", "workout", "workouts"), ("symptom", "symptom", "symptoms"), ("state_of_mind", "state of mind", "states of mind"),
                ("ecg", "ECG", "ECGs"), ("heart_rate_notification", "heart rate notification", "heart rate notifications"),
                ("cycle", "cycle tracking entry", "cycle tracking entries"), ("medication", "medication", "medications"))


def _ago(then, now) -> str:
    seconds = max(0, int((now - then).total_seconds()))
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'' if n == 1 else 's'} ago"
    return "just now"


def card_data(session: Session, owner_id: int, host_url: str, new_secret: str | None = None) -> dict:
    """Everything the Settings card shows: tokens, the address to type into the phone app, and how the receiving is going."""
    base = host_url.rstrip("/")
    active = session.scalars(select(IngestToken).where(IngestToken.owner_id == owner_id, IngestToken.scope == "health",
                                                       IngestToken.revoked_at.is_(None)).order_by(IngestToken.id)).all()
    first, last = session.execute(select(func.min(HealthRaw.received_at), func.max(HealthRaw.received_at))
                                  .where(HealthRaw.owner_id == owner_id)).one()
    readings = session.scalar(select(func.count()).select_from(HealthReading).where(HealthReading.owner_id == owner_id)) or 0
    recent = session.scalars(select(HealthRaw).where(HealthRaw.owner_id == owner_id)
                             .order_by(HealthRaw.received_at.desc(), HealthRaw.id.desc()).limit(10)).all()
    found = dict(session.execute(select(HealthEvent.kind, func.count()).where(HealthEvent.owner_id == owner_id).group_by(HealthEvent.kind)).all())
    events = [(found.pop(kind, 0), one, many) for kind, one, many in EVENT_LABELS]      # every kind, none yet included
    events += [(n, kind.replace("_", " "), kind.replace("_", " ")) for kind, n in sorted(found.items())]
    now = naive_utcnow()
    return {
        "tokens": active, "new_secret": new_secret, "ingest_url": f"{base}/api/ingest/health",
        "header_line": f"Authorization: Bearer {new_secret}" if new_secret else None,
        "last_received_at": last, "first_data_at": first, "reading_count": readings, "recent": recent, "event_counts": events, "event_total": sum(n for n, _, _ in events),
        "status_line": "Waiting for first data" if last is None else f"Last received {_ago(last, now)}, {readings:,} reading{'' if readings == 1 else 's'}",
        "max_mb": config.HEALTH_MAX_BYTES // (1024 * 1024),
        "max_tokens": config.HEALTH_MAX_TOKENS, "at_token_limit": len(active) >= config.HEALTH_MAX_TOKENS,
    }


@router.post("/settings/apple-health/token")
async def create_token(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    live = session.scalar(select(func.count()).select_from(IngestToken).where(IngestToken.owner_id == uid, IngestToken.scope == "health",
                                                                             IngestToken.revoked_at.is_(None)))
    if live >= config.HEALTH_MAX_TOKENS:            # the card says so and offers no form; this refuses a post sent anyway
        return RedirectResponse(BACK, status_code=303)
    token, secret = tokens.create_token(session, uid, str(form.get("label") or ""), scope="health")
    if request.state.session_id:
        _remember(request.state.session_id, secret, token.label)
    return RedirectResponse(BACK, status_code=303)


@router.post("/settings/apple-health/token/{token_id}/revoke")
def revoke_token(token_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    token = session.scalar(select(IngestToken).where(IngestToken.id == token_id, IngestToken.owner_id == uid, IngestToken.scope == "health"))
    if token is not None:
        tokens.revoke_token(session, token.id)      # someone else's token is left alone, and the answer is the same
    return RedirectResponse(BACK, status_code=303)


@router.post("/settings/apple-health/choices")
async def save_choices(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    choices.save_choices(session, uid, {str(v) for v in form.getlist("metric")}, {str(v) for v in form.getlist("offered")})
    return RedirectResponse(BACK, status_code=303)

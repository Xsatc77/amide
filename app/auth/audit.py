"""The security log: who signed in, who failed, who changed a password. Never raises, never stores a password, code or token."""

import logging

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import SecurityEvent, User, naive_utcnow

log = logging.getLogger("amide.security")
DETAIL_MAX = 200
NAME_MAX = 64


def _clean(text: str, limit: int) -> str:
    return "".join(ch if ch.isprintable() else " " for ch in str(text))[:limit]


def record(db: Session, event: str, request: Request | None = None, user: User | None = None, detail: str = "",
           username: str | None = None) -> None:
    """Add one line to the log inside the caller's transaction (it is saved by the caller's next commit). `username` is the name that
    was typed, for a failed sign-in with no account behind it. A problem here is logged and swallowed, never raised."""
    try:
        client = request.client.host if request is not None and request.client else None
        name = user.username if user is not None else username
        row = SecurityEvent(created_at=naive_utcnow(), user_id=user.id if user is not None else None,
                            username=_clean(name, NAME_MAX) if name else None, event=_clean(event, 40),
                            detail=_clean(detail, DETAIL_MAX), client=_clean(client, 64) if client else None)
        with db.begin_nested():
            db.add(row)
        log.info("%s user=%s client=%s %s", row.event, row.username or "-", row.client or "-", row.detail)
    except Exception:  # noqa: BLE001 -- the log must never break a sign-in
        log.warning("could not record security event %s", event, exc_info=True)


def recent(db: Session, limit: int = 50) -> list[SecurityEvent]:
    return list(db.query(SecurityEvent).order_by(SecurityEvent.id.desc()).limit(limit))

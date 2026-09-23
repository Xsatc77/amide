"""Browser sessions (server-side, idle timeout) and login lockout. All times are naive UTC."""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app import config
from app.models import LoginSession, User

COOKIE = "amide_session"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create(db: Session, now: datetime) -> LoginSession:
    row = LoginSession(id=secrets.token_urlsafe(32), created_at=now, last_seen=now)
    db.add(row)
    db.commit()
    return row


def get_live(db: Session, token: str | None, now: datetime) -> LoginSession | None:
    """The session for `token`, or None if unknown or idle too long (expired ones are deleted)."""
    if not token:
        return None
    row = db.get(LoginSession, token)
    if row is None:
        return None
    if now - row.last_seen > timedelta(minutes=config.SESSION_IDLE_MINUTES):
        db.delete(row)
        db.commit()
        return None
    return row


def touch(db: Session, row: LoginSession, now: datetime) -> None:
    row.last_seen = now
    db.commit()


def is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and now < user.locked_until


def record_failure(user: User, now: datetime) -> None:
    user.failed_attempts = (user.failed_attempts or 0) + 1
    if user.failed_attempts >= config.LOCKOUT_ATTEMPTS:
        user.locked_until = now + timedelta(minutes=config.LOCKOUT_MINUTES)
        user.failed_attempts = 0


def clear_failures(user: User) -> None:
    user.failed_attempts = 0
    user.locked_until = None

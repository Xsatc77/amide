"""Browser sessions (server-side, idle timeout) and login lockout. All times are naive UTC."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app import config
from app.auth import audit, devices
from app.models import LoginSession, Passkey, User

COOKIE = "amide_session"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_token(token: str) -> str:
    """What is stored for a cookie token: its SHA-256 (hex). A copy of the database holds no usable cookie value."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create(db: Session, now: datetime) -> LoginSession:
    """A new session. The row's id is the HASH of the cookie token; the raw token (what the cookie carries) is on `row.token`
    for this object only and is never stored."""
    token = secrets.token_urlsafe(32)
    row = LoginSession(id=hash_token(token), created_at=now, last_seen=now)
    db.add(row)
    db.commit()
    row.token = token
    return row


def get_live(db: Session, token: str | None, now: datetime) -> LoginSession | None:
    """The session for `token`, or None if unknown or idle too long (expired ones are deleted)."""
    if not token:
        return None
    row = db.get(LoginSession, hash_token(token))
    if row is None:
        return None
    if (now - row.last_seen > timedelta(minutes=config.SESSION_IDLE_MINUTES)
            or now - row.created_at > timedelta(hours=config.SESSION_MAX_HOURS)):      # idle too long, or too old even if kept alive
        db.delete(row)
        db.commit()
        return None
    return row


ANONYMOUS_HOURS = 1
PURGE_EVERY = timedelta(minutes=1)
_last_purge: datetime | None = None


def purge_stale(db: Session, now: datetime) -> int:
    """Delete sessions idle past the limit, older than the absolute lifetime, or never signed in and over an hour old. Returns how many."""
    result = db.execute(delete(LoginSession).where(or_(
        LoginSession.last_seen < now - timedelta(minutes=config.SESSION_IDLE_MINUTES),
        LoginSession.created_at < now - timedelta(hours=config.SESSION_MAX_HOURS),
        (LoginSession.user_id.is_(None)) & (LoginSession.created_at < now - timedelta(hours=ANONYMOUS_HOURS)))))
    db.commit()
    audit.purge(db, now)                  # the security log is trimmed on the same once-a-minute schedule
    return result.rowcount or 0


def purge_if_due(db: Session, now: datetime) -> None:
    """purge_stale, but at most once a minute (called from the gate on every request)."""
    global _last_purge
    if _last_purge is not None and now - _last_purge < PURGE_EVERY:
        return
    _last_purge = now
    purge_stale(db, now)


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


def end_other_sessions(db: Session, user_id: int, keep_id: str) -> None:
    """Sign this user out everywhere except the session at `keep_id`, after the person changed their OWN password or name.
    Remembered devices go too (they ask for the code again). Passkeys stay: the person just proved the current password in this
    very request, and deleting them would lock out anyone who signs in with a passkey. (An admin reset, an admin 2FA removal and
    the command line use end_all_sessions, which does delete them.)"""
    db.query(LoginSession).filter(LoginSession.user_id == user_id, LoginSession.id != keep_id).delete()
    devices.forget_all(db, user_id)
    db.commit()


def end_all_sessions(db: Session, user_id: int) -> None:
    """Sign this user out everywhere (e.g. an admin reset their password)."""
    db.query(LoginSession).filter(LoginSession.user_id == user_id).delete()
    devices.forget_all(db, user_id)
    db.execute(delete(Passkey).where(Passkey.user_id == user_id))
    db.commit()

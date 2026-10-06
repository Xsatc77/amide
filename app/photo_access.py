"""When the sharp version of a body photo may be shown: the 2FA setting and this browser session's unlock window."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import config
from app.auth import sessions
from app.models import LoginSession, User
from app.routers.auth import check_code


def is_unlocked(row: LoginSession | None, now: datetime) -> bool:
    return row is not None and row.photo_unlocked_until is not None and now < row.photo_unlocked_until


def seconds_left(row: LoginSession | None, now: datetime) -> int:
    return max(0, int((row.photo_unlocked_until - now).total_seconds())) if is_unlocked(row, now) else 0


def can_view_full(user: User, row: LoginSession | None, now: datetime) -> bool:
    return (not user.photo_2fa_required) or is_unlocked(row, now)


def _locked_message(user: User, now: datetime) -> str:
    minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
    return f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}."


def unlock(db: Session, user: User, row: LoginSession, code: str, now: datetime) -> str | None:
    """Open this session's photos for config.PHOTO_UNLOCK_MINUTES. None on success, otherwise the message to show.
    Uses the login code check, so a code just used to sign in cannot be reused, and wrong codes count toward lockout."""
    if sessions.is_locked(user, now):
        return _locked_message(user, now)
    if not user.totp_enabled:
        return "Turn on two-factor authentication for your account first."
    if problem := check_code(user, code, now):
        sessions.record_failure(user, now)
        db.commit()
        return _locked_message(user, now) if sessions.is_locked(user, now) else problem
    sessions.clear_failures(user)
    row.photo_unlocked_until = now + timedelta(minutes=config.PHOTO_UNLOCK_MINUTES)
    db.commit()
    return None


def lock(db: Session, row: LoginSession | None) -> None:
    if row is not None:
        row.photo_unlocked_until = None
        db.commit()

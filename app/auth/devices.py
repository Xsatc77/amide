"""Remembered devices: a browser that was told "Remember this device" skips the 2FA code for config.TRUSTED_DEVICE_DAYS.

The browser holds a random token in a cookie; only its hash is stored. A device is tied to one user, and is forgotten when that
person turns 2FA off, changes their password, or has it reset."""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import config
from app.models import TrustedDevice

COOKIE = "amide_device"
MAX_AGE = config.TRUSTED_DEVICE_DAYS * 24 * 3600


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def remember(db: Session, user_id: int, now: datetime) -> str:
    """Trust a new browser for this user; returns the token for its cookie."""
    token = secrets.token_urlsafe(32)
    db.execute(delete(TrustedDevice).where(TrustedDevice.expires_at < now))          # tidy up the expired ones as we go
    db.add(TrustedDevice(user_id=user_id, token_hash=_hash(token), created_at=now,
                         expires_at=now + timedelta(days=config.TRUSTED_DEVICE_DAYS)))
    return token


def is_trusted(db: Session, user_id: int, token: str | None, now: datetime) -> bool:
    if not token:
        return False
    row = db.scalar(select(TrustedDevice).where(TrustedDevice.token_hash == _hash(token)))
    return row is not None and row.user_id == user_id and row.expires_at > now


def forget_all(db: Session, user_id: int) -> None:
    db.execute(delete(TrustedDevice).where(TrustedDevice.user_id == user_id))

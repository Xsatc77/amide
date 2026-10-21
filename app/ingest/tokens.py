"""Tokens for the price-list watcher: created once, stored only as a hash, rate limited."""

import hashlib
import secrets
import threading
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import IngestToken, User, naive_utcnow

PREFIX = "amide_ing_"
HEALTH_PREFIX = "amide_hlt_"       # Apple Health tokens: any signed-in person may hold one, and they open only the health receiver
_MAX_SECRET = 200


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_token(session: Session, owner_id: int, label: str, scope: str = "watcher") -> tuple[IngestToken, str]:
    prefix = HEALTH_PREFIX if scope == "health" else PREFIX
    secret = prefix + secrets.token_urlsafe(32)
    token = IngestToken(owner_id=owner_id, label=(label or "").strip()[:60] or ("iPhone" if scope == "health" else "Watcher"),
                        prefix=secret[:len(prefix) + 6], token_hash=hash_secret(secret), scope=scope)
    session.add(token)
    session.commit()
    return token, secret


def find_token(session: Session, header: str | None, scope: str = "watcher") -> IngestToken | None:
    """The live token for an Authorization header of the form `Bearer <secret>`, or None.
    The watcher scope stays administrator-only; the health scope accepts any owner."""
    if not header or not header.startswith("Bearer "):
        return None
    secret = header[7:].strip()
    health = scope == "health"
    if not secret.startswith(HEALTH_PREFIX if health else PREFIX) or len(secret) > _MAX_SECRET:
        return None
    token = session.scalar(select(IngestToken).where(IngestToken.token_hash == hash_secret(secret), IngestToken.revoked_at.is_(None),
                                                      IngestToken.scope == scope))
    if token is None:
        return None
    owner = session.get(User, token.owner_id)
    return token if owner is not None and (health or owner.is_admin) else None


def revoke_token(session: Session, token_id: int) -> bool:
    token = session.get(IngestToken, token_id)
    if token is None or token.revoked_at is not None:
        return False
    token.revoked_at = naive_utcnow()
    session.commit()
    return True


class RateLimiter:
    """At most `limit` calls per `window` seconds for each key (a sliding window, in memory)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, now: float) -> bool:
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


limiter = RateLimiter(config.INGEST_RATE_PER_MINUTE, 60.0)

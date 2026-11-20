"""Per-client request throttles for the sign-in doors, kept in memory (a restart forgets them, which is fine for a rate limit).

The key is the client address as the server sees it (`request.client.host`). When Amide sits behind a reverse proxy, uvicorn only
swaps in the forwarded address if that proxy is trusted (FORWARDED_ALLOW_IPS); a client-supplied X-Forwarded-For is never read here,
so it cannot be used to dodge the limit."""

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from fastapi import Request

# group -> (calls allowed, per this many seconds)
LIMITS = {
    "login": (30, 600),          # /login, /login/2fa, /login/passkey*
    "register": (10, 3600),
    "notice": (60, 600),
    "anon_session": (20, 3600),  # brand-new anonymous sessions one address may create (see accept_notice)
}


class Throttle:
    def __init__(self):
        self._hits: dict = defaultdict(deque)
        self._lock = threading.Lock()
        self._calls = 0

    def check(self, group: str, client: str, now: float | None = None) -> int:
        """0 when the call is allowed (and counted); otherwise the whole seconds to wait."""
        limit, window = LIMITS[group]
        now = time.monotonic() if now is None else now
        with self._lock:
            self._calls += 1
            if self._calls % _SWEEP_EVERY == 0 or len(self._hits) > MAX_KEYS:
                self._sweep(now)
            hits = self._hits[(group, client)]
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, int(window - (now - hits[0])) + 1)
            hits.append(now)
            return 0

    def _sweep(self, now: float) -> None:
        """Caller holds the lock. Forget addresses that have gone quiet, then the oldest beyond MAX_KEYS."""
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= LIMITS[k[0]][1]]:
            del self._hits[key]
        if len(self._hits) > MAX_KEYS:
            for key in sorted(self._hits, key=lambda k: self._hits[k][-1])[:len(self._hits) - MAX_KEYS]:
                del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


throttle = Throttle()


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def group_for(request: Request) -> str | None:
    """Which throttle (if any) applies to this request."""
    if request.method != "POST":
        return None
    path = request.url.path
    if path == "/login" or path.startswith("/login/"):
        return "login"
    if path == "/register":
        return "register"
    if path == "/notice":
        return "notice"
    return None


WAIT_MESSAGE = "Too many attempts from this address. Please wait a little while and try again."


# Per-address + username backoff. The account lock (5 wrong tries -> 15 minutes) stays, but on its own it lets a stranger lock a
# person out just by guessing their name. So: (1) a client that has signed in as this name before (same address, last 30 days) may
# still try while the account is locked, and (2) every address that keeps failing against one name is slowed down exponentially
# (30 s, 1 min, 2 min ... up to 15 min) after its fifth failure. Tradeoff: someone behind the same shared address as the person
# (one household router) can still lock them out, and a restart forgets both lists. Both live in memory only.
BACKOFF_FREE = 5
KNOWN_DAYS = 30
MAX_KEYS = 5000                 # hard cap on each table below: the oldest entries go first, so memory cannot grow with addresses
_SWEEP_EVERY = 256              # every this many writes, entries past their window are dropped
_failures: dict[tuple[str, str], tuple[int, datetime]] = {}
_known: dict[tuple[str, str], datetime] = {}
_lock = threading.Lock()        # the threadpool (sync routes) and the event loop both reach these dicts
_writes = 0


def _evict(table: dict, newest, max_age: timedelta, now: datetime) -> None:
    """Caller holds _lock. Drop entries whose newest time is older than `max_age` (every _SWEEP_EVERY writes, or when over the cap),
    then the oldest entries beyond MAX_KEYS."""
    global _writes
    _writes += 1
    if _writes % _SWEEP_EVERY and len(table) <= MAX_KEYS:
        return
    for key in [k for k, v in list(table.items()) if now - newest(v) > max_age]:
        table.pop(key, None)
    if len(table) > MAX_KEYS:
        for key in sorted(table, key=lambda k: newest(table[k]))[:len(table) - MAX_KEYS]:
            table.pop(key, None)


def backoff_wait(client: str, name_key: str, now: datetime) -> int:
    """Seconds this address must still wait before trying this name again (0 = go ahead)."""
    with _lock:
        entry = _failures.get((client, name_key))
        if entry is None:
            return 0
        count, last = entry
        if now - last > timedelta(hours=1):
            _failures.pop((client, name_key), None)
            return 0
    if count < BACKOFF_FREE:
        return 0
    delay = min(900, 30 * 2 ** min(count - BACKOFF_FREE, 10))
    return max(0, int(delay - (now - last).total_seconds()) + 1)


def note_failure(client: str, name_key: str, now: datetime) -> None:
    with _lock:
        count, _ = _failures.get((client, name_key), (0, now))
        _failures[(client, name_key)] = (count + 1, now)
        _evict(_failures, lambda v: v[1], timedelta(hours=1), now)


def note_success(client: str, name_key: str, now: datetime) -> None:
    with _lock:
        _failures.pop((client, name_key), None)
        _known[(client, name_key)] = now
        _evict(_known, lambda v: v, timedelta(days=KNOWN_DAYS), now)


def is_known(client: str, name_key: str, now: datetime) -> bool:
    with _lock:
        seen = _known.get((client, name_key))
    return seen is not None and now - seen <= timedelta(days=KNOWN_DAYS)


def reset_all() -> None:
    throttle.reset()
    with _lock:
        _failures.clear()
        _known.clear()

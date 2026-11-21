"""The front door: every request passes through here.

Order of the steps a browser goes through:
  legal notice (/notice)  ->  welcome (/welcome: New User or Login)  ->  [2FA code, unless a passkey or a remembered device]  ->  the app
A session that has seen no request for config.SESSION_IDLE_MINUTES, or is older than config.SESSION_MAX_HOURS
even while in use, is gone, so the browser starts again at the notice. Pages redirect to the right step; /api/ paths and
/session/ping answer 401 instead. The token routes (TOKEN_PREFIXES) skip the session and check their own token.
"""

import ipaddress
import logging
import os
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm.exc import StaleDataError

from app.settings.rules import timezone_error
from app import clock, config
from app.auth import sessions, throttle
from app.db import SessionLocal
from app.models import User
from app.templating import templates

log = logging.getLogger("amide")

# Always reachable.
OPEN_PREFIXES = ("/static/", "/branding/")
# These routes authenticate themselves with an ingest token and never accept a session.
TOKEN_PREFIXES = ("/api/ingest/", "/calendar/feed/")
OPEN_PATHS = {"/healthz", "/notice", "/sw.js"}
# Reachable once the notice is accepted, signed in or not.
SIGN_IN_PATHS = {"/welcome", "/register", "/login", "/login/2fa", "/login/passkey", "/login/passkey/options", "/logout", "/session/ping"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _same_origin(request: Request) -> bool:
    """Reject form posts made by other websites. A modern browser says who is posting in Sec-Fetch-Site (it cannot be set by a web page):
    anything but same-origin (or none, a person typing or bookmarking) is refused, including `same-site`, which is ANOTHER app on a sibling
    sub-domain of the same site. Older clients fall back to Origin/Referer, and a request with none of these (a script, not a browser) is
    allowed because the SameSite cookie still protects browsers."""
    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return False
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source or source == "null":
        return True  # non-browser clients; the SameSite cookie still protects browsers
    return urlsplit(source).netloc == request.headers.get("host", request.url.netloc)


def _deny(request: Request, step: str) -> Response:
    if request.url.path.startswith("/api/") or request.url.path == "/session/ping":
        return JSONResponse({"detail": "Sign in required"}, status_code=401)
    return RedirectResponse(step, status_code=303)


def _device_zone(request: Request) -> str | None:
    """The time zone the browser reported (a cookie set by every page), when it is a real one; used for people who left Settings on automatic."""
    name = request.cookies.get("amide_tz", "")
    return name if name and timezone_error(name) is None else None


def _too_many(request: Request, wait: int) -> Response:
    """429 with Retry-After: JSON for the passkey calls (the page's script shows the message), a small page for everything else."""
    headers = {"Retry-After": str(wait)}
    if request.url.path.startswith("/login/passkey"):
        return JSONResponse({"error": throttle.WAIT_MESSAGE}, status_code=429, headers=headers)
    return templates.TemplateResponse(request, "auth/throttled.html", {"message": throttle.WAIT_MESSAGE, "wait": wait},
                                      status_code=429, headers=headers)


_proxy_checked = False


def _warn_if_behind_untrusted_proxy(request: Request) -> None:
    """Once per process, on the first POST: with an https public address, a private or loopback peer and no FORWARDED_ALLOW_IPS,
    every visitor probably reaches Amide through a proxy uvicorn does not trust, so they all share one throttle key."""
    global _proxy_checked
    if _proxy_checked:
        return
    _proxy_checked = True
    if not config.PUBLIC_URL.lower().startswith("https://") or os.environ.get("FORWARDED_ALLOW_IPS"):
        return
    try:
        peer = ipaddress.ip_address(request.client.host) if request.client else None
    except ValueError:
        return
    if peer is not None and (peer.is_private or peer.is_loopback):
        log.warning("The first request came from %s, a private address, while AMIDE_PUBLIC_URL is https and FORWARDED_ALLOW_IPS is not set: "
                    "behind a reverse proxy every visitor then shares one address, so the sign-in throttle counts them together. "
                    "Set FORWARDED_ALLOW_IPS to the proxy's address (see docs/DEPLOYING.md).", peer)


def install(app: FastAPI) -> None:
    if not config.PUBLIC_URL:
        log.warning("AMIDE_PUBLIC_URL is not set: passkeys trust the Host / X-Forwarded-Host header of each request. "
                    "Set AMIDE_PUBLIC_URL to the https address people use (see docs/DEPLOYING.md).")

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        request.state.user = None
        request.state.session_id = None
        clock.set_zone(None)
        if request.method == "POST":
            _warn_if_behind_untrusted_proxy(request)

        if request.method in UNSAFE_METHODS and not _same_origin(request):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        if group := throttle.group_for(request):
            wait = throttle.throttle.check(group, throttle.client_address(request))
            if wait:
                return _too_many(request, wait)
        if path.startswith(OPEN_PREFIXES + TOKEN_PREFIXES) or path in ("/healthz", "/sw.js"):
            return await call_next(request)

        now = sessions.now_utc()
        with SessionLocal() as db:
            sessions.purge_if_due(db, now)
            row = sessions.get_live(db, request.cookies.get(sessions.COOKIE), now)
            if row is not None:
                try:
                    sessions.touch(db, row, now)
                except StaleDataError:          # signed out everywhere (or purged) between the lookup and this write: the same as no session
                    db.rollback()
                    row = None
            if row is not None:
                request.state.session_id = row.id
                if row.user_id is not None:
                    user = db.get(User, row.user_id)
                    db.expunge(user)
                    request.state.user = user
                    clock.set_zone(user.timezone or _device_zone(request))          # "today" is the person's own day, not the server's
            accepted = row is not None and row.notice_accepted_at is not None
            signed_in = row is not None and row.user_id is not None
            pending = row is not None and row.twofa_pending

        if path == "/notice" or path.startswith("/legal/"):
            return await call_next(request)
        if not accepted:
            response = _deny(request, "/notice")
            response.delete_cookie(sessions.COOKIE)
            return response
        if path in SIGN_IN_PATHS:
            return await call_next(request)
        if not signed_in:
            return _deny(request, "/welcome")
        if pending:
            return _deny(request, "/login/2fa")
        return await call_next(request)

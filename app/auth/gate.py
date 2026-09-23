"""The front door: every request passes through here.

Order of the steps a browser goes through:
  legal notice (/notice)  ->  welcome (/welcome: New User or Login)  ->  [2FA code]  ->  the app
A session that has seen no request for config.SESSION_IDLE_MINUTES is gone, so the browser starts again
at the notice. Pages redirect to the right step; the JSON API answers 401 instead.
"""

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.auth import sessions
from app.db import SessionLocal
from app.models import User

# Always reachable.
OPEN_PREFIXES = ("/static/", "/branding/")
OPEN_PATHS = {"/healthz", "/notice"}
# Reachable once the notice is accepted, signed in or not.
SIGN_IN_PATHS = {"/welcome", "/register", "/login", "/login/2fa", "/logout", "/session/ping"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _same_origin(request: Request) -> bool:
    """Reject form posts made by other websites (the browser names the sending site in Origin/Referer)."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source or source == "null":
        return True  # non-browser clients; the SameSite cookie still protects browsers
    return urlsplit(source).netloc == request.headers.get("host", request.url.netloc)


def _deny(request: Request, step: str) -> Response:
    if request.url.path.startswith("/api/") or request.url.path == "/session/ping":
        return JSONResponse({"detail": "Sign in required"}, status_code=401)
    return RedirectResponse(step, status_code=303)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        request.state.user = None
        request.state.session_id = None

        if request.method in UNSAFE_METHODS and not _same_origin(request):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        if path.startswith(OPEN_PREFIXES) or path == "/healthz":
            return await call_next(request)

        now = sessions.now_utc()
        with SessionLocal() as db:
            row = sessions.get_live(db, request.cookies.get(sessions.COOKIE), now)
            if row is not None:
                sessions.touch(db, row, now)
                request.state.session_id = row.id
                if row.user_id is not None:
                    user = db.get(User, row.user_id)
                    db.expunge(user)
                    request.state.user = user
            accepted = row is not None and row.notice_accepted_at is not None
            signed_in = row is not None and row.user_id is not None
            pending = row is not None and row.twofa_pending

        if path == "/notice":
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

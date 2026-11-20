"""HTTP response hardening and request-size limits, as one pure ASGI middleware, plus the one redirect validator.

Every response (pages, static files, errors, redirects, answers the front door gives before a route runs) carries the security headers
below. Request bodies are capped by path before a route sees them.

Environment (read per request, so a change needs only a restart):
  AMIDE_CSP=report-only   send the Content-Security-Policy as report-only (nothing is blocked), for troubleshooting a broken page
  AMIDE_API_DOCS=1        keep FastAPI's /docs, /redoc and /openapi.json (off by default; read in app.main)
  AMIDE_MAX_REQUEST_MB    the biggest upload body accepted when no path-specific limit applies (default 64)
  AMIDE_PUBLIC_URL        when it starts with https, Strict-Transport-Security is sent even if a proxy hides the https scheme
"""

import os
import re
from urllib.parse import unquote, urlsplit

from app import config

# ---------------------------------------------------------------- the policy

# Scripts: this site only. No page has an inline script or an inline event handler (they live in /static/js), and the data blocks
# (<script type="application/json">) are not executed, so no 'unsafe-inline' and no nonce is needed for script-src.
# Styles: 'unsafe-inline' stays because pages use style="..." attributes and a few <style> blocks (label sheet CSS, chart colours).
# Images: data: and blob: for the camera/photo previews and inline QR codes. The camera (barcode scanner) needs no CSP source.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "media-src 'self' blob:",
    "connect-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

PERMISSIONS_POLICY = "camera=(self), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"

# Private files (COAs, lab reports, price lists, QR codes, photos): displayed inline, but sandboxed with no script, no network, no frames.
FILE_CSP = "sandbox; default-src 'none'"
FILE_HEADERS = {
    "Content-Security-Policy": FILE_CSP,
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
}
# The same without the cache rule, for files that are not private (the shipped peptide card images).
FILE_HEADERS_SHARED = {k: v for k, v in FILE_HEADERS.items() if k != "Cache-Control"}


def _csp_header_name() -> bytes:
    return b"content-security-policy-report-only" if os.environ.get("AMIDE_CSP", "").strip().lower() == "report-only" else b"content-security-policy"


def _is_https(scope) -> bool:
    if scope.get("scheme") == "https":
        return True
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-proto" and value.split(b",")[0].strip().lower() == b"https":
            return True
    return config.PUBLIC_URL.lower().startswith("https://")


def _security_headers(scope) -> list[tuple[bytes, bytes]]:
    headers = [
        (_csp_header_name(), CSP.encode()),
        (b"x-frame-options", b"DENY"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"same-origin"),
        (b"permissions-policy", PERMISSIONS_POLICY.encode()),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]
    if _is_https(scope):
        headers.append((b"strict-transport-security", b"max-age=31536000"))
    return headers


# ---------------------------------------------------------------- request size caps

_MB = 1024 * 1024


def request_cap(path: str, content_type: str = "") -> int:
    """The most bytes a request body may have for this path (ordinary forms and JSON: 2 MB)."""
    try:
        default_mb = int(os.environ.get("AMIDE_MAX_REQUEST_MB", "").strip())
    except ValueError:
        default_mb = 0
    default = (default_mb if default_mb > 0 else 64) * _MB
    if path.startswith("/backup/"):
        return config.MAX_BACKUP_BYTES + _MB
    if path == "/api/ingest/health":
        return config.HEALTH_MAX_BYTES + _MB
    if path.startswith("/api/ingest/") or content_type.lower().startswith("multipart/"):
        return default
    return 2 * _MB


class _TooLarge(Exception):
    pass


class SecurityMiddleware:
    """Adds the security headers to every response and answers 413 to a request body past its cap."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = _security_headers(scope)
        state = {"started": False, "over": False}

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                state["started"] = True
                present = {name.lower() for name, _ in message.get("headers", [])}
                message = {**message, "headers": list(message.get("headers", [])) + [(n, v) for n, v in extra if n not in present]}
            await send(message)

        headers = {name: value for name, value in scope.get("headers", [])}
        cap = request_cap(scope.get("path", ""), headers.get(b"content-type", b"").decode("latin-1"))
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > cap:
            await self._too_large(send_with_headers)
            return

        seen = 0

        async def counted_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cap:
                    state["over"] = True
                    raise _TooLarge()
            return message

        try:
            await self.app(scope, counted_receive, send_with_headers)
        except BaseException as error:                       # a body read can fail inside a task group (an ExceptionGroup), so look at the flag
            if state["over"] and not state["started"]:
                await self._too_large(send_with_headers)
            elif not state["over"]:
                raise error
        else:
            return

    @staticmethod
    async def _too_large(send):
        body = b'{"detail":"Request too large"}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------- redirects built from user input

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def safe_redirect(raw, fallback: str = "/", prefix: str | None = None) -> str:
    """A same-site path from user input, else `fallback`.

    Accepted: one leading '/', then anything but a second '/', a backslash, a control character (CR/LF too), a scheme or a host. A
    percent-encoded form of those is judged after decoding, so '%2f%2f' and '%5c' do not get through. With `prefix` (e.g. '/vendors') the
    path must also be that page or lie under it.
    """
    text = str(raw or "")
    for candidate in (text, unquote(text), unquote(unquote(text))):
        if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate or _CONTROL.search(candidate):
            return fallback
        parts = urlsplit(candidate)
        if parts.scheme or parts.netloc:
            return fallback
    if prefix is not None and not (text == prefix or text.startswith((prefix + "/", prefix + "?", prefix + "#"))):
        return fallback
    return text

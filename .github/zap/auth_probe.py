"""Signed-in probe of a throwaway Amide container: registers an account, signs in, crawls the main pages with the session cookie and
checks the response headers and the error pages. Standard library only. Exit status 1 when anything is missing.

Usage: python .github/zap/auth_probe.py http://localhost:18000

The account lives only in the throwaway container's volume (the workflow starts it with AMIDE_REGISTRATION=open); the password is
random per run and never printed.
"""
import http.cookiejar
import re
import secrets
import string
import sys
import urllib.error
import urllib.parse
import urllib.request

PAGES = ["/", "/dashboard", "/today", "/protocols", "/calendar", "/measurements", "/workouts", "/inventory", "/vendors",
         "/library", "/calculator", "/links", "/settings", "/learn", "/about"]
# What every response (pages, static files, errors) must carry, and the value each must contain.
REQUIRED = {
    "content-security-policy": ["default-src 'self'", "frame-ancestors 'none'", "object-src 'none'"],
    "x-frame-options": ["DENY"],
    "x-content-type-options": ["nosniff"],
    "referrer-policy": ["same-origin", "no-referrer", "strict-origin"],      # any one of these
    "cross-origin-opener-policy": ["same-origin"],
}
# Strings that must never appear in an error body or banner.
LEAKS = [r"Traceback \(most recent call last\)", r'File "[^"]+\.py"', r"\buvicorn\b", r"\bstarlette\b", r"\bfastapi\b",
         r"\bsqlalchemy\b", r"\bsqlite3?\b", r"/app/app/", r"site-packages"]
BANNER_HEADERS = ("server", "x-powered-by")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def build_opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(NoRedirect, urllib.request.HTTPCookieProcessor(jar)), jar


def fetch(opener, base, path, data=None, method=None, headers=None):
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    request = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, response.headers, response.read(300_000)
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read(300_000)


def main(base: str) -> int:
    base = base.rstrip("/")
    problems: list[str] = []
    checked = 0

    def check(path, status, headers, body, *, private=False, needs_headers=True):
        nonlocal checked
        checked += 1
        label = f"{path} ({status})"
        if needs_headers:
            for name, wanted in REQUIRED.items():
                value = headers.get(name)
                if not value:
                    problems.append(f"{label}: missing {name}")
                elif name == "referrer-policy":
                    if not any(w in value for w in wanted):
                        problems.append(f"{label}: weak referrer-policy {value!r}")
                elif not all(w in value for w in wanted):
                    problems.append(f"{label}: {name} lacks {[w for w in wanted if w not in value]}")
        for name in BANNER_HEADERS:
            if headers.get(name):
                problems.append(f"{label}: version banner {name}: {headers.get(name)}")
        text = body.decode("utf-8", "replace")
        for pattern in LEAKS if status >= 400 else LEAKS[:2]:     # pages may name their own stack; error bodies may not
            if re.search(pattern, text, re.I):
                problems.append(f"{label}: body leaks /{pattern}/")
        if private and "no-store" not in (headers.get("cache-control") or "").lower():
            problems.append(f"{label}: private page without Cache-Control: no-store (got {headers.get('cache-control')!r})")

    opener, _ = build_opener()
    # The legal notice must be accepted before anything else, then the account is created (sign-up is open in this container).
    status, headers, body = fetch(opener, base, "/notice")
    check("/notice", status, headers, body)
    status, headers, body = fetch(opener, base, "/notice", {"understand": "1"})
    if status not in (200, 303):
        print(f"could not accept the notice: {status}")
        return 1
    alphabet = string.ascii_letters + string.digits
    username = "probe" + "".join(secrets.choice(alphabet) for _ in range(8))
    password = "Aa1!" + "".join(secrets.choice(alphabet) for _ in range(20))
    status, headers, body = fetch(opener, base, "/register", {"username": username, "password": password, "confirm": password})
    if status != 303:
        print(f"registration failed: {status} (is the container running with AMIDE_REGISTRATION=open?)")
        return 1
    # Sign in again from a clean client, as a returning person would.
    opener, _ = build_opener()
    fetch(opener, base, "/notice", {"understand": "1"})
    status, headers, body = fetch(opener, base, "/login", {"username": username, "password": password})
    if status != 303 or "/login" in (headers.get("location") or ""):
        print(f"sign-in failed: {status}")
        return 1

    for path in PAGES:
        status, headers, body = fetch(opener, base, path)
        if path == "/" and status in (301, 302, 303, 307, 308):          # the front page only redirects to the dashboard
            check(path, status, headers, body)
            continue
        if status != 200:
            problems.append(f"{path}: expected 200 when signed in, got {status}")
            continue
        check(path, status, headers, body, private=True)
    # Shipped static files carry the headers too (cache rules are public here).
    status, headers, body = fetch(opener, base, "/static/manifest.webmanifest")
    check("/static/manifest.webmanifest", status, headers, body)

    # Error shapes, signed in and signed out: no stack trace, no version banner, still the security headers.
    anonymous, _ = build_opener()
    shapes = [("GET", "/this-page-does-not-exist", None), ("GET", "/%00", None), ("GET", "/protocols/99999999999999999999", None),
              ("GET", "/inventory/not-a-number", None), ("POST", "/dashboard", b""), ("DELETE", "/login", None),
              ("POST", "/login", b"\xff\xfe=not-form"), ("POST", "/api/ingest/price-list", b"{not json"),
              ("GET", "/static/../../etc/passwd", None), ("GET", "/" + "a" * 5000, None)]
    for who, client in (("signed in", opener), ("signed out", anonymous)):
        for method, path, data in shapes:
            try:
                status, headers, body = fetch(client, base, path, data, method, {"Content-Type": "application/x-www-form-urlencoded"} if data is not None else None)
            except (urllib.error.URLError, ValueError) as error:
                problems.append(f"{method} {path[:40]} ({who}): the request failed: {error}")
                continue
            check(f"{method} {path[:40]} {who}", status, headers, body)
    if fetch(opener, base, "/healthz")[0] != 200:
        problems.append("/healthz no longer answers 200 after the probe")

    print(f"checked {checked} responses")
    for line in problems:
        print("FAIL", line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:18000"))

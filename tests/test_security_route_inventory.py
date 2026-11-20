"""Every route is either behind the session gate or on a short, reviewed list of public routes with a reason each.

Why this catches a new public route: the list below is the ONLY place a route may be public. `audit(app, PUBLIC)` walks every route
the app declares (nested routers and mounts included), asks the real gate what an anonymous browser gets, and reports any route
that answers without a session and is not on the list. A developer who adds a route under an open prefix (/static/, /branding/,
/legal/, /api/ingest/, /calendar/feed/) or an open path, or who weakens the gate, gets a failure here until the list is edited on
purpose (a visible line in a reviewed file). The last test proves it on a throwaway app, never on the real one.
"""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import gate
from app.main import app
from tests.route_helpers import all_routes

# path template -> why it may be reached without a signed-in session
PUBLIC = {
    "/healthz": "container health check, returns only a short ok",
    "/sw.js": "service worker file, must be fetchable before sign-in",
    "/static": "mounted static assets (css, js, icons, fonts)",
    "/branding/banner": "the logo shown on the sign-in pages",
    "/notice": "legal notice, the first step for every browser",
    "/legal/disclaimer": "legal text linked from the notice",
    "/legal/terms": "legal text linked from the notice",
    "/legal/privacy": "legal text linked from the notice",
    "/welcome": "choice between sign in and new user",
    "/register": "sign-up form; closed after the first account unless an admin opens it",
    "/login": "password sign-in, throttled per address",
    "/login/2fa": "second step of sign-in, needs a half-signed-in session",
    "/login/passkey": "passkey sign-in, verifies a signature",
    "/login/passkey/options": "passkey challenge for sign-in",
    "/logout": "ends the session",
    "/session/ping": "keep-alive, answers 401 JSON when signed out",
    "/calendar/feed/{token}.ics": "calendar feed, authenticated by its own secret token in the path",
    "/api/ingest/sources/{chat_id}": "watcher API, authenticated by a bearer token",
    "/api/ingest/sources": "watcher API, authenticated by a bearer token",
    "/api/ingest/sources/{chat_id}/state": "watcher API, authenticated by a bearer token",
    "/api/ingest/messages": "watcher API, authenticated by a bearer token",
    "/api/ingest/health": "Apple Health receiver, authenticated by a bearer token",
}
TOKEN_PREFIXES = tuple(gate.TOKEN_PREFIXES)
ANONYMOUS_OK = {301, 302, 303, 307, 308, 401, 403, 404, 405}     # a redirect to the sign-in steps, or a refusal: never a page


def fill(path: str) -> str:
    """A concrete URL for a path template: 999999 for a name ending in id, 'x' for any other parameter."""
    def one(match):
        return "999999" if match.group(1).endswith("id") else "x"
    return re.sub(r"{(\w+)(?::\w+)?}", one, path)


def call(client: TestClient, method: str, url: str, **kw):
    return client.request(method, url, follow_redirects=False, **kw)


def gate_says_public(path: str) -> bool:
    """What the gate lets through with no session at all (its own constants, so a gate change shows up here)."""
    return ((path + "/").startswith(gate.OPEN_PREFIXES + gate.TOKEN_PREFIXES) or      # a mount like /static has no trailing slash
             path in gate.OPEN_PATHS
            or path in gate.SIGN_IN_PATHS or path.startswith("/legal/"))


def audit(the_app: FastAPI, public: dict) -> list[str]:
    """Problems found in `the_app`: routes that answer anonymously and are not in `public`, or stale list entries."""
    problems: list[str] = []
    routes = all_routes(the_app)
    paths = {r.path for r in routes}
    for r in routes:
        if gate_says_public(r.path) and r.path not in public:
            problems.append(f"{r.path} is open to anonymous browsers but not on the reviewed list")
    for listed in public:
        if listed not in paths and not any(p.startswith(listed + "/") for p in paths):
            problems.append(f"{listed} is on the list but no route has that path")
    fresh = TestClient(the_app, follow_redirects=False)                       # a browser that has never visited
    accepted = TestClient(the_app, follow_redirects=False)                    # a browser that accepted the notice, not signed in
    accepted.post("/notice", data={"understand": "1"})
    for r in routes:
        if r.path in public or r.mounted:
            continue
        for method in r.methods:
            for who, c in (("no cookie", fresh), ("notice only", accepted)):
                body = {"data": {}} if method in ("POST", "PUT", "PATCH", "DELETE") else {}
                resp = call(c, method, fill(r.path), **body)
                if resp.status_code not in ANONYMOUS_OK:
                    problems.append(f"{method} {r.path} gave {resp.status_code} to a browser with {who}")
    return problems


# ---------------------------------------------------------------- the real app

def test_the_inventory_sees_the_whole_app(client):
    routes = all_routes(app)
    assert len(routes) > 150, "route discovery found far fewer routes than the app has: a router shape went unseen"
    assert any(r.mounted and r.path == "/static" for r in routes)
    assert any(r.path == "/dashboard" for r in routes) and any(r.int_params for r in routes)


def test_every_route_is_gated_or_on_the_reviewed_public_list(client):
    assert audit(app, PUBLIC) == []


def test_the_public_list_matches_what_the_gate_opens(client):
    opened = {r.path for r in all_routes(app) if gate_says_public(r.path)}
    assert opened == set(PUBLIC), opened ^ set(PUBLIC)
    assert all(reason.strip() for reason in PUBLIC.values())


@pytest.mark.parametrize("prefix", TOKEN_PREFIXES)
def test_token_routes_refuse_no_token_and_a_wrong_token(client, prefix):
    routes = [r for r in all_routes(app) if r.path.startswith(prefix)]
    assert routes, f"no route under {prefix}"
    fresh = TestClient(app, follow_redirects=False)
    for r in routes:
        for method in r.methods:
            body = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
            for headers in ({}, {"Authorization": "Bearer not-a-real-token"}, {"Authorization": "Bearer amw_" + "z" * 43},
                            {"Authorization": "Basic eDp5"}):
                resp = call(fresh, method, fill(r.path), headers=headers, **body)
                assert resp.status_code in (401, 403, 404), f"{method} {r.path} {headers} gave {resp.status_code}"
                assert not 200 <= resp.status_code < 300


# ---------------------------------------------------------------- proof the check bites (a throwaway app, never the real one)

def _throwaway_app() -> FastAPI:
    fresh = FastAPI()
    gate.install(fresh)

    @fresh.get("/healthz")
    def ok():
        return "ok"

    @fresh.get("/private")
    def private():
        return "secret"

    @fresh.get("/legal/extra")                          # a new route under an open prefix: public by accident
    def extra():
        return "anyone can read this"
    return fresh


def test_a_new_public_route_makes_the_audit_fail_and_a_gated_one_does_not(client):
    listed = {"/healthz": "health check", "/legal/disclaimer": "stale entry for the proof"}
    found = audit(_throwaway_app(), listed)
    assert any("/legal/extra" in p and "not on the reviewed list" in p for p in found)
    assert any("/legal/disclaimer is on the list but no route" in p for p in found)       # a stale entry fails too
    assert not any("/private" in p for p in found)                                          # gated: anonymous gets a redirect
    assert audit(_throwaway_app(), {"/healthz": "h", "/legal/extra": "reviewed on purpose"}) == []

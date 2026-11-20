"""S-06 per-client throttle and the softened lockout, N-04 stale session purge, N-11 absolute session lifetime."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.auth import sessions, throttle
from app.db import SessionLocal
from app.main import app
from app.models import LoginSession

from tests.test_auth_flow import PW, accept, fresh, register, text, user  # noqa: F401


def browser(address: str) -> TestClient:
    return TestClient(app, follow_redirects=False, client=(address, 50000))


def test_login_posts_are_throttled_per_client_with_retry_after():
    c = browser("198.51.100.20")
    accept(c)
    codes = [c.post("/login", data={"username": f"nobody{i}", "password": "x"}).status_code for i in range(throttle.LIMITS["login"][0] + 3)]
    assert 429 not in codes[:throttle.LIMITS["login"][0]]
    r = c.post("/login", data={"username": "nobody", "password": "x"})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "Too many attempts" in text(r)


def test_a_forged_forwarded_for_header_does_not_dodge_the_throttle():
    c = browser("198.51.100.21")
    accept(c)
    for i in range(throttle.LIMITS["login"][0]):
        c.post("/login", data={"username": "nobody", "password": "x"}, headers={"X-Forwarded-For": f"10.0.0.{i}"})
    r = c.post("/login", data={"username": "nobody", "password": "x"}, headers={"X-Forwarded-For": "10.9.9.9"})
    assert r.status_code == 429


def test_another_client_is_not_slowed_by_the_first():
    a, b = browser("198.51.100.22"), browser("198.51.100.23")
    accept(a), accept(b)
    for _ in range(throttle.LIMITS["login"][0] + 1):
        a.post("/login", data={"username": "nobody", "password": "x"})
    assert b.post("/login", data={"username": "nobody", "password": "x"}).status_code == 422


def test_the_2fa_and_passkey_doors_share_the_login_throttle():
    c = browser("198.51.100.24")
    accept(c)
    for _ in range(throttle.LIMITS["login"][0]):
        c.post("/login/passkey/options")
    r = c.post("/login/passkey/options")
    assert r.status_code == 429 and r.json()["error"] and "retry-after" in r.headers
    assert c.post("/login/2fa", data={"code": "123456"}).status_code == 429


def test_register_posts_are_throttled():
    c = browser("198.51.100.25")
    accept(c)
    for _ in range(throttle.LIMITS["register"][0]):
        assert c.post("/register", data={"username": "x"}).status_code == 422
    r = c.post("/register", data={"username": "x"})
    assert r.status_code == 429 and "retry-after" in r.headers


def test_notice_posts_are_throttled_and_new_anonymous_sessions_are_capped_per_address():
    c = browser("198.51.100.26")
    cap = throttle.LIMITS["anon_session"][0]
    for _ in range(cap):
        c.cookies.clear()
        assert c.post("/notice", data={"understand": "1"}).status_code == 303
    c.cookies.clear()
    r = c.post("/notice", data={"understand": "1"})
    assert r.status_code == 429
    with SessionLocal() as s:
        assert s.query(LoginSession).filter(LoginSession.user_id.is_(None)).count() >= cap


# ---------------------------------------------------------------- lockout softening

def test_a_stranger_locking_the_account_does_not_lock_out_the_person_who_signs_in_from_their_usual_address():
    victim, stranger = browser("198.51.100.30"), browser("203.0.113.30")
    accept(victim), accept(stranger)
    register(victim, "Soft1")
    victim.post("/logout")
    assert victim.post("/login", data={"username": "soft1", "password": PW}).status_code == 303      # now a known client
    victim.post("/logout")
    for _ in range(config.LOCKOUT_ATTEMPTS):
        stranger.post("/login", data={"username": "soft1", "password": "wrong"})
    assert user("soft1").locked_until is not None
    assert "Too many attempts" in text(stranger.post("/login", data={"username": "soft1", "password": PW}))   # the stranger stays out
    assert victim.post("/login", data={"username": "soft1", "password": PW}).status_code == 303                # the person gets in
    assert user("soft1").locked_until is None


def test_an_address_that_keeps_failing_against_one_name_is_slowed_down():
    c = browser("198.51.100.31")
    accept(c)
    for _ in range(throttle.BACKOFF_FREE):
        assert c.post("/login", data={"username": "ghost", "password": "x"}).status_code == 422
    r = c.post("/login", data={"username": "ghost", "password": "x"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


# ---------------------------------------------------------------- N-04 purge

def _count(s, *where):
    return s.query(LoginSession).filter(*where).count()


def test_stale_sessions_are_deleted():
    now = sessions.now_utc()
    with SessionLocal() as s:
        s.add_all([
            LoginSession(id="stale-idle", created_at=now, last_seen=now - timedelta(minutes=config.SESSION_IDLE_MINUTES + 5)),
            LoginSession(id="stale-anon", created_at=now - timedelta(hours=2), last_seen=now),
            LoginSession(id="stale-old", created_at=now - timedelta(hours=config.SESSION_MAX_HOURS + 1), last_seen=now, user_id=None),
            LoginSession(id="fresh-anon", created_at=now - timedelta(minutes=10), last_seen=now),
        ])
        s.commit()
        sessions.purge_stale(s, now)
        ids = {row.id for row in s.query(LoginSession).filter(LoginSession.id.like("%-%"))}
        assert {"stale-idle", "stale-anon", "stale-old"}.isdisjoint(ids) and "fresh-anon" in ids
        s.query(LoginSession).filter(LoginSession.id == "fresh-anon").delete()
        s.commit()


def test_the_gate_purges_at_most_once_a_minute(fresh):
    now = sessions.now_utc()
    with SessionLocal() as s:
        s.add(LoginSession(id="stale-1", created_at=now - timedelta(hours=3), last_seen=now - timedelta(hours=3)))
        s.commit()
    sessions._last_purge = None
    fresh.get("/welcome")
    with SessionLocal() as s:
        assert s.get(LoginSession, "stale-1") is None
        s.add(LoginSession(id="stale-2", created_at=now - timedelta(hours=3), last_seen=now - timedelta(hours=3)))
        s.commit()
    fresh.get("/welcome")                      # less than a minute later: no second sweep
    with SessionLocal() as s:
        assert s.get(LoginSession, "stale-2") is not None
        sessions.purge_stale(s, sessions.now_utc())
        assert s.get(LoginSession, "stale-2") is None


# ---------------------------------------------------------------- N-11 absolute lifetime

def test_a_session_ends_after_the_absolute_lifetime_even_if_kept_alive(fresh):
    accept(fresh)
    register(fresh, "Lifetime1")
    assert fresh.post("/session/ping").status_code == 204
    with SessionLocal() as s:
        row = s.scalar(select(LoginSession).where(LoginSession.user_id == user("lifetime1").id))
        row.created_at = sessions.now_utc() - timedelta(hours=config.SESSION_MAX_HOURS, minutes=1)
        s.commit()
    assert fresh.post("/session/ping").status_code == 401
    assert fresh.get("/protocols").status_code == 303


def test_the_lifetime_is_configurable(monkeypatch):
    monkeypatch.setenv("AMIDE_SESSION_MAX_HOURS", "6")
    assert config._env_int("AMIDE_SESSION_MAX_HOURS", 24) == 6
    monkeypatch.setenv("AMIDE_SESSION_MAX_HOURS", "junk")
    assert config._env_int("AMIDE_SESSION_MAX_HOURS", 24) == 24

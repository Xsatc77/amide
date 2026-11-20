"""Red-team regression guards for sign-in, sessions, 2FA, registration, audit log and secrets at rest (attacks that were tried and held)."""

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.auth import sessions, throttle
from app.db import SessionLocal
from app.main import app
from app.models import LoginSession, SecurityEvent, User

from tests.test_auth_flow import PW, accept, next_code, register, setup_2fa, text, user  # noqa: F401
from tests.test_passkeys_devices import Phone, add_passkey, passkey_login


def browser(address: str) -> TestClient:
    return TestClient(app, follow_redirects=False, client=(address, 51000))


def member(name: str, address: str = "203.0.113.50", twofa: bool = False):
    """A signed-in browser for a brand-new account (and its 2FA secret when asked)."""
    c = browser(address)
    accept(c)
    assert register(c, name).status_code == 303
    return (c, setup_2fa(c)) if twofa else c


def login_page(c, name, password=PW):
    return c.post("/login", data={"username": name, "password": password})


# ---------------------------------------------------------------- registration is closed by default, through every door

@pytest.fixture
def closed(monkeypatch):
    monkeypatch.delenv("AMIDE_REGISTRATION", raising=False)


def test_closed_registration_holds_for_odd_paths_methods_and_headers(client, closed):
    c = browser("203.0.113.51")
    accept(c)
    form = {"username": "rt1_sneak", "password": PW, "confirm": PW}
    attempts = [
        c.post("/register", data=form),
        c.post("/register/", data=form),
        c.post("/register?x=1", data=form),
        c.post("/%72egister", data=form),
        c.post("//register", data=form),
        c.post("/register", data=form, headers={"X-HTTP-Method-Override": "PUT"}),
        c.post("/register", data={**form, "is_admin": "1", "registration_open": "1"}),
        c.request("PUT", "/register", data=form),
        c.request("HEAD", "/register"),
    ]
    assert all(r.status_code != 200 and not (r.status_code == 303 and r.headers["location"] == "/dashboard") for r in attempts), [r.status_code for r in attempts]
    assert user("rt1_sneak") is None


def test_a_normal_user_cannot_use_any_admin_door(client, monkeypatch):
    normal = member("rt1_normal", "203.0.113.52")
    monkeypatch.delenv("AMIDE_REGISTRATION", raising=False)
    admin_id = user("tester").id
    for path, data in [("/settings/admin/registration", {"allow": "1"}),
                       ("/settings/admin/users/new", {"username": "rt1_made", "password": PW, "confirm": PW}),
                       (f"/settings/admin/users/{admin_id}/reset-password", {"password": "Zz9!zzzz", "confirm": "Zz9!zzzz"}),
                       (f"/settings/admin/users/{admin_id}/remove-2fa", {}),
                       (f"/settings/admin/users/{admin_id}/delete", {"username": "Tester"})]:
        assert normal.post(path, data=data).status_code == 404, path
    assert user("rt1_made") is None and user("tester") is not None
    assert user("rt1_normal").is_admin is False
    other = browser("203.0.113.53")
    accept(other)
    assert other.get("/register").status_code == 403


def test_extra_form_fields_do_not_make_anyone_admin_or_change_another_account(client):
    c = member("rt1_mass", "203.0.113.54")
    me_id = user("rt1_mass").id
    extra = {"is_admin": "1", "owner_id": "1", "username_key": "tester", "id": "1", "totp_enabled": "1", "locked_until": "", "password_hash": "x"}
    for path, data in [("/settings/timezone", {"mode": "system"}), ("/settings/email", {"email": "a@example.test"}),
                       ("/settings/username", {"username": "rt1_mass2", "current_password": PW}),
                       ("/settings/password", {"current_password": PW, "new_password": PW + "x", "confirm": PW + "x"})]:
        c.post(path, data={**data, **extra})
    row = user("rt1_mass2")
    assert row is not None and row.id == me_id and row.is_admin is False and row.totp_enabled is False
    assert user("tester").is_admin is True and user("tester").username_key == "tester"


# ---------------------------------------------------------------- pending 2FA session reaches nothing

def test_a_session_waiting_for_its_2fa_code_reaches_no_page_or_api(client):
    owner, secret = member("rt1_pending", "203.0.113.55", twofa=True)
    thief = browser("203.0.113.56")
    accept(thief)
    assert login_page(thief, "rt1_pending").headers["location"] == "/login/2fa"
    for path in ("/dashboard", "/settings", "/account/2fa", "/account/passkeys", "/protocols", "/inventory", "/backup"):
        r = thief.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login/2fa", path
    assert thief.get("/api/protocols").status_code == 401
    assert thief.post("/account/passkeys/options").status_code == 303
    assert thief.post("/account/2fa/forget-devices").status_code == 303
    assert thief.post("/account/2fa", data={"action": "disable", "code": "000000"}).status_code == 303
    assert thief.post("/session/ping").status_code == 204 and thief.get("/dashboard").status_code == 303


def test_a_remembered_device_of_another_account_does_not_skip_the_code(client):
    a, secret_a = member("rt1_deva", "203.0.113.57", twofa=True)
    a.post("/logout")
    login_page(a, "rt1_deva")
    r = a.post("/login/2fa", data={"code": next_code(secret_a), "remember": "1"})
    device = r.cookies.get("amide_device") or a.cookies.get("amide_device")
    assert device
    b, _ = member("rt1_devb", "203.0.113.58", twofa=True)
    b.post("/logout")
    b.cookies.set("amide_device", device)
    assert login_page(b, "rt1_devb").headers["location"] == "/login/2fa"


def test_a_used_code_is_refused_again_and_wrong_codes_lock_the_account_on_every_door(client):
    c, secret = member("rt1_lock", "203.0.113.59", twofa=True)
    c.post("/logout")
    login_page(c, "rt1_lock")
    good = next_code(secret)
    assert c.post("/login/2fa", data={"code": good}).status_code == 303
    c.post("/logout")
    login_page(c, "rt1_lock")
    assert "already used" in text(c.post("/login/2fa", data={"code": good}))
    for _ in range(config.LOCKOUT_ATTEMPTS):
        c.post("/login/2fa", data={"code": "000000"})
    assert user("rt1_lock").locked_until is not None
    assert "Too many attempts" in text(c.post("/login/2fa", data={"code": next_code(secret)}))
    stranger = browser("203.0.113.60")
    accept(stranger)
    # A stranger is not told the name is locked (that would prove it exists); see tests/test_security_auth_followups.py.
    assert stranger.get("/dashboard").status_code == 303 and "Username or password is incorrect." in text(login_page(stranger, "RT1_LOCK"))


# ---------------------------------------------------------------- sessions

def test_expired_sessions_die_even_through_the_ping_door(client):
    c = member("rt1_exp", "203.0.113.61")
    assert c.post("/session/ping").status_code == 204
    token = c.cookies.get(sessions.COOKIE)
    with SessionLocal() as s:
        row = s.get(LoginSession, sessions.hash_token(token))
        row.created_at = sessions.now_utc() - timedelta(hours=config.SESSION_MAX_HOURS, minutes=1)
        s.commit()
    assert c.post("/session/ping").status_code == 401
    assert c.get("/dashboard").status_code == 303
    d = member("rt1_idle", "203.0.113.62")
    with SessionLocal() as s:
        row = s.get(LoginSession, sessions.hash_token(d.cookies.get(sessions.COOKIE)))
        row.last_seen = sessions.now_utc() - timedelta(minutes=config.SESSION_IDLE_MINUTES, seconds=5)
        s.commit()
    assert d.post("/session/ping").status_code == 401


def test_a_planted_pre_login_cookie_is_replaced_on_sign_in(client):
    victim = member("rt1_fix", "203.0.113.63")
    victim.post("/logout")
    attacker = browser("203.0.113.64")
    accept(attacker)
    planted = attacker.cookies.get(sessions.COOKIE)
    victim.cookies.clear()
    victim.cookies.set(sessions.COOKIE, planted, domain="testserver.local")
    login_page(victim, "rt1_fix")
    assert victim.cookies.get(sessions.COOKIE, domain="testserver.local") != planted
    assert attacker.get("/dashboard").status_code == 303            # the planted one is not signed in
    assert attacker.get("/settings").status_code == 303


def test_the_deleted_users_live_session_stops_working_without_an_error(client):
    c = member("rt1_gone", "203.0.113.65")
    victim_id = user("rt1_gone").id
    r = client.post(f"/settings/admin/users/{victim_id}/delete", data={"username": "rt1_gone"})
    assert r.status_code in (200, 303)
    assert c.get("/dashboard").status_code in (303, 401)


def test_the_database_holds_no_cookie_value_and_a_hash_is_not_a_cookie(client):
    c = member("rt1_hash", "203.0.113.66")
    token = c.cookies.get(sessions.COOKIE)
    with SessionLocal() as s:
        ids = [i for (i,) in s.execute(select(LoginSession.id))]
    assert token not in ids and sessions.hash_token(token) in ids
    thief = browser("203.0.113.67")
    thief.cookies.set(sessions.COOKIE, sessions.hash_token(token))
    assert thief.get("/dashboard").status_code == 303


# ---------------------------------------------------------------- throttle doors

def test_percent_encoded_and_odd_login_paths_share_the_login_throttle(client):
    c = browser("203.0.113.68")
    accept(c)
    limit = throttle.LIMITS["login"][0]
    for i in range(limit):
        c.post("/%6Cogin", data={"username": f"nobody{i}", "password": "x"}, headers={"X-Real-IP": f"10.1.1.{i}", "Forwarded": f"for=10.2.2.{i}"})
    assert c.post("/login", data={"username": "nobody", "password": "x"}).status_code == 429


def test_unknown_and_wrong_password_answers_look_the_same(client):
    c = member("rt1_enum", "203.0.113.69")
    c.post("/logout")
    probe = browser("203.0.113.70")
    accept(probe)
    known = login_page(probe, "rt1_enum", "wrong")
    unknown = login_page(probe, "rt1_nobody", "wrong")
    assert known.status_code == unknown.status_code == 422
    assert known.text.replace("rt1_enum", "NAME") == unknown.text.replace("rt1_nobody", "NAME")


# ---------------------------------------------------------------- security log and secrets

def test_hostile_names_typed_at_login_cannot_inject_html_into_the_security_log(client):
    c = browser("203.0.113.71")
    accept(c)
    login_page(c, "<script>alert(1)</script>" + "A" * 5000)
    login_page(c, '"><img src=x onerror=alert(2)>')
    page = client.get("/settings").text
    assert "<script>alert(1)" not in page and "<img src=x" not in page
    with SessionLocal() as s:
        longest = max(len(e.username or "") for e in s.query(SecurityEvent))
    assert longest <= 64


def test_other_users_cannot_read_the_security_log(client):
    c = member("rt1_logger", "203.0.113.72")
    login_page(browser("203.0.113.73"), "rt1_logger", "bad")
    page = c.get("/settings").text
    assert "Security Log" not in page and "login_failed" not in page


def test_the_saved_ai_key_and_the_2fa_seed_never_come_back_in_pages(client):
    c, secret = member("rt1_secret", "203.0.113.74", twofa=True)
    sentinel = "sk-redteam-SENTINEL-0123456789"
    r = c.post("/settings/ai", data={"ai_provider": "openai", "ai_model": "m", "ai_api_key": sentinel})
    assert r.status_code == 303
    for path in ("/settings", "/account/2fa", "/account/passkeys", "/dashboard"):
        body = c.get(path).text
        assert sentinel not in body, path
        assert secret not in body, path


def test_the_secret_does_not_reach_log_lines(client, caplog):
    caplog.set_level("DEBUG")
    c, secret = member("rt1_logsec", "203.0.113.75", twofa=True)
    c.post("/settings/ai", data={"ai_provider": "openai", "ai_model": "m", "ai_api_key": "sk-redteam-LOGSENTINEL"})
    c.post("/logout")
    login_page(c, "rt1_logsec", "wrong-" + PW)
    assert secret not in caplog.text and "sk-redteam-LOGSENTINEL" not in caplog.text and PW not in caplog.text


def test_tampered_truncated_and_foreign_sealed_values_never_open(tmp_path, monkeypatch):
    from app import secretbox
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.delenv(secretbox.ENV_KEY, raising=False)
    secretbox._file_keys.clear()
    sealed = secretbox.encrypt("hunter2-seed")
    flipped = sealed[:-6] + ("A" if sealed[-6] != "A" else "B") + sealed[-5:]
    for bad in (flipped, sealed[:-10], sealed[:len(secretbox.PREFIX) + 8], secretbox.PREFIX, secretbox.PREFIX + "!!!!", "enc1:éé", "hunter2-seed"):
        assert secretbox.decrypt(bad) is None
    monkeypatch.setenv(secretbox.ENV_KEY, "another key")
    assert secretbox.decrypt(sealed) is None
    secretbox._file_keys.clear()


# ---------------------------------------------------------------- passkeys

def test_a_challenge_from_another_browser_session_cannot_sign_in(client):
    owner = member("rt1_pk", "203.0.113.76")
    phone = Phone()
    assert add_passkey(owner, phone).status_code == 200
    owner.post("/logout")
    mine, theirs = browser("203.0.113.77"), browser("203.0.113.78")
    accept(mine), accept(theirs)
    options = theirs.post("/login/passkey/options").json()
    answer = phone.sign_in(options)
    assert mine.post("/login/passkey", json={"credential": answer}).status_code == 401
    assert mine.get("/dashboard").status_code == 303


def test_adding_a_passkey_without_the_password_is_refused_and_counts_as_a_failure(client):
    c = member("rt1_pkauth", "203.0.113.79")
    options = c.post("/account/passkeys/options").json()
    r = c.post("/account/passkeys", json={"name": "x", "credential": Phone().register(options)})
    assert r.status_code == 403
    assert (user("rt1_pkauth").failed_attempts or 0) >= 1

import html
import re
import time
from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.auth import sessions
from app.db import SessionLocal
from app.main import app
from app.models import User

PW = "aB3!"


@pytest.fixture
def fresh():
    """A brand-new browser (no cookies)."""
    return TestClient(app, follow_redirects=False)


def accept(c):
    r = c.post("/notice", data={"understand": "1"})
    assert r.status_code == 303, r.text
    return r


def register(c, username, password=PW, confirm=None, twofa=False):
    data = {"username": username, "password": password, "confirm": confirm if confirm is not None else password}
    if twofa:
        data["twofa"] = "1"
    return c.post("/register", data=data)


def user(name) -> User:
    with SessionLocal() as s:
        return s.scalar(select(User).where(User.username_key == name.lower()))


def text(r) -> str:
    return html.unescape(r.text)


@pytest.fixture
def clock(monkeypatch):
    """Controllable 'now' for sessions and lockout."""
    state = {"now": sessions.now_utc()}
    monkeypatch.setattr(sessions, "now_utc", lambda: state["now"])
    return state


# ---------------------------------------------------------------- notice + gate

def test_everything_starts_at_the_notice(fresh):
    for path in ("/", "/protocols", "/inventory", "/library", "/welcome", "/login"):
        r = fresh.get(path)
        assert r.status_code in (302, 303, 307) and r.headers["location"].endswith("/notice"), path
    assert fresh.get("/api/protocols").status_code == 401
    assert fresh.get("/healthz").status_code == 200
    assert fresh.get("/static/css/app.css").status_code == 200


def test_notice_page_content(fresh):
    t = text(fresh.get("/notice"))
    assert "LEGAL NOTICE" in t and "Consult a doctor before using any substance" in t
    assert "Regulatory status varies by country." in t
    assert "I Understand The Safety Statement Above" in t
    assert re.search(r'<button[^>]*id="notice-ok"[^>]*disabled', t)  # OK starts disabled
    assert 'class="banner' in t


def test_notice_requires_checkbox(fresh):
    r = fresh.post("/notice", data={})
    assert r.status_code == 422 and "tick the box" in text(r)
    assert fresh.get("/protocols").headers["location"].endswith("/notice")
    accept(fresh)
    r = fresh.get("/protocols")
    assert r.headers["location"].endswith("/welcome")
    t = text(fresh.get("/welcome"))
    assert "New User" in t and "Login" in t


# ---------------------------------------------------------------- register

def test_register_validation(fresh):
    accept(fresh)
    for kwargs, msg in [({"username": "ab"}, "3–32 characters"),
                        ({"username": "newbie1", "password": "abc", "confirm": "abc"}, "1 uppercase letter"),
                        ({"username": "newbie1", "password": PW, "confirm": "aB3?"}, "Passwords don't match")]:
        r = register(fresh, **kwargs)
        assert r.status_code == 422 and msg in text(r), kwargs
    assert user("newbie1") is None


def test_register_username_case_insensitive(fresh):
    accept(fresh)
    assert register(fresh, "CaseUser").status_code == 303
    other = TestClient(app, follow_redirects=False)
    accept(other)
    r = register(other, "caseuser")
    assert r.status_code == 422 and "already taken" in text(r)
    assert user("caseuser").username == "CaseUser"


def test_register_signs_in_and_shows_user_menu(fresh):
    accept(fresh)
    r = register(fresh, "Menu.User")
    assert r.headers["location"] == "/protocols"
    t = text(fresh.get("/protocols"))
    assert 'class="avatar"' in t and ">M<" in t and "Menu.User" in t and "Log out" in t


def test_register_form_lists_rules_and_eye_toggles(fresh):
    accept(fresh)
    t = text(fresh.get("/register"))
    for rule in ("At least 4 characters", "1 uppercase letter", "1 lowercase letter", "1 number",
                 "1 special character", "Passwords match"):
        assert rule in t
    assert t.count('class="eye"') == 2 and 'type="password"' in t
    assert "2-Factor Authentication?" in t


# ---------------------------------------------------------------- login + lockout

def test_login_and_wrong_password(fresh):
    accept(fresh)
    register(fresh, "LoginUser")
    fresh.post("/logout")
    assert fresh.get("/protocols").headers["location"].endswith("/welcome")
    t = text(fresh.get("/login"))
    assert "Add New User" in t and 'class="eye"' in t
    r = fresh.post("/login", data={"username": "loginuser", "password": "wrong"})
    assert r.status_code == 422 and "incorrect" in text(r)
    r = fresh.post("/login", data={"username": "LOGINUSER", "password": PW})  # username not case sensitive
    assert r.status_code == 303 and r.headers["location"] == "/protocols"
    assert fresh.get("/protocols").status_code == 200


def test_login_password_is_case_sensitive(fresh):
    accept(fresh)
    register(fresh, "CaseSens")
    fresh.post("/logout")
    assert fresh.post("/login", data={"username": "casesens", "password": PW.lower()}).status_code == 422


def test_lockout(fresh, clock):
    accept(fresh)
    register(fresh, "LockMe")
    fresh.post("/logout")
    for _ in range(5):
        fresh.post("/login", data={"username": "lockme", "password": "nope"})
    r = fresh.post("/login", data={"username": "lockme", "password": PW})
    assert r.status_code == 422 and "Too many attempts" in text(r)
    clock["now"] += timedelta(minutes=16)
    fresh.post("/notice", data={"understand": "1"})  # session idled out meanwhile
    assert fresh.post("/login", data={"username": "lockme", "password": PW}).status_code == 303


def test_unknown_user_gets_same_message(fresh):
    accept(fresh)
    r = fresh.post("/login", data={"username": "nobody-here", "password": PW})
    assert r.status_code == 422 and "Username or password is incorrect." in text(r)


# ---------------------------------------------------------------- 2FA

def next_code(secret):
    """The code the app will show next (a used code can't be reused)."""
    return pyotp.TOTP(secret).at(time.time() + 30)


def setup_2fa(c):
    t = text(c.get("/account/2fa"))
    assert "<svg" in t
    secret = re.search(r'data-secret="([A-Z2-7]+)"', t).group(1)
    code = pyotp.TOTP(secret).now()
    r = c.post("/account/2fa", data={"action": "enable", "code": code})
    assert r.status_code == 303
    return secret


def test_register_with_2fa_goes_to_setup_then_login_requires_code(fresh):
    accept(fresh)
    r = register(fresh, "TwoFA", twofa=True)
    assert r.headers["location"] == "/account/2fa"
    r = fresh.post("/account/2fa", data={"action": "enable", "code": "000000"})
    assert r.status_code == 422 and "didn't match" in text(r)
    secret = setup_2fa(fresh)
    assert user("twofa").totp_enabled

    fresh.post("/logout")
    r = fresh.post("/login", data={"username": "twofa", "password": PW})
    assert r.headers["location"] == "/login/2fa"
    assert fresh.get("/protocols").headers["location"].endswith("/login/2fa")  # gate holds you here
    assert fresh.post("/login/2fa", data={"code": "123456"}).status_code == 422
    r = fresh.post("/login/2fa", data={"code": next_code(secret)})
    assert r.headers["location"] == "/protocols"
    assert fresh.get("/protocols").status_code == 200


def test_disable_2fa_needs_current_code(fresh):
    accept(fresh)
    register(fresh, "Disabler")
    secret = setup_2fa(fresh)
    assert fresh.post("/account/2fa", data={"action": "disable", "code": "000000"}).status_code == 422
    fresh.post("/account/2fa", data={"action": "disable", "code": next_code(secret)})
    assert not user("disabler").totp_enabled


# ---------------------------------------------------------------- timeout, ping, logout

def test_session_expires_after_idle_limit(fresh, clock):
    accept(fresh)
    register(fresh, "Idler")
    clock["now"] += timedelta(minutes=9)
    assert fresh.post("/session/ping").status_code == 204
    clock["now"] += timedelta(minutes=9)  # 18 min since login, but only 9 since the ping
    assert fresh.get("/protocols").status_code == 200
    clock["now"] += timedelta(minutes=10, seconds=1)
    assert fresh.get("/protocols").headers["location"].endswith("/notice")
    assert fresh.post("/session/ping").status_code == 401


def test_logout_returns_to_welcome(fresh):
    accept(fresh)
    register(fresh, "Leaver")
    r = fresh.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/welcome"
    assert fresh.get("/protocols").headers["location"].endswith("/welcome")


# ---------------------------------------------------------------- CSRF, cookie, banner

def test_cross_origin_post_rejected(fresh):
    accept(fresh)
    register(fresh, "Origins")
    r = fresh.post("/inventory", data={"name": "X"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = fresh.post("/inventory", data={"name": "X"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 303


def test_session_cookie_flags(fresh):
    r = accept(fresh)
    cookie = r.headers["set-cookie"]
    assert "amide_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")


def test_banner_override(fresh):
    assert fresh.get("/branding/banner").status_code == 404
    assert 'src="/static/img/amide-banner.svg"' in fresh.get("/notice").text
    config.BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    custom = config.BRANDING_DIR / "banner.png"
    custom.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    try:
        r = fresh.get("/branding/banner")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert 'src="/branding/banner' in fresh.get("/notice").text
    finally:
        custom.unlink()


def test_first_user_is_admin(client):
    # The shared test client registered the first account (see conftest).
    with SessionLocal() as s:
        first = s.scalar(select(User).order_by(User.id))
    assert first.is_admin


def test_code_screen_enter_submits_okay_not_cancel(fresh):
    """Pressing Enter submits a form with its first submit button: that must be Okay, never Cancel."""
    accept(fresh)
    register(fresh, "EnterKey")
    secret = setup_2fa(fresh)
    fresh.post("/logout")
    fresh.post("/login", data={"username": "enterkey", "password": PW})
    t = fresh.get("/login/2fa").text
    form = re.search(r'<form method="post" action="/login/2fa".*?</form>', t, re.S).group(0)
    first_button = re.search(r"<button[^>]*>", form).group(0)
    assert "formaction" not in first_button and 'type="submit"' in first_button
    assert "Cancel" not in form  # Cancel lives in its own form


def test_a_code_cannot_be_used_twice(fresh):
    accept(fresh)
    register(fresh, "Replay")
    secret = setup_2fa(fresh)  # uses the current code once
    fresh.post("/logout")
    fresh.post("/login", data={"username": "replay", "password": PW})
    r = fresh.post("/login/2fa", data={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 422 and "already used" in text(r)


def test_static_links_change_when_files_change(fresh):
    """CSS/JS links carry the file's modification time, so browsers fetch updates without a hard refresh."""
    t = fresh.get("/notice").text
    css = re.search(r'href="(/static/css/app\.css\?v=\d+)"', t)
    js = re.search(r'src="(/static/js/auth\.js\?v=\d+)"', t)
    assert css and js
    assert fresh.get(css.group(1)).status_code == 200

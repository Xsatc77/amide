import html

import pytest
from sqlalchemy import select

from app.auth import passwords
from app.db import SessionLocal
from app.models import LoginSession, User

PW = "Test1!"


def text(r) -> str:
    # Extracts and unescapes body text regardless of status: callers check status separately, and
    # several tests inspect the body of a 422 error re-render, not just a 200 page.
    return html.unescape(r.text)


def _current(uid: int) -> User:
    # Looks up by id (stable across a username change), not by username string -- several tests here
    # rename the account, and a string-keyed lookup would silently return None/a stale row afterward.
    with SessionLocal() as s:
        return s.get(User, uid)


def test_settings_page_has_user_and_integrations_sections(client):
    # Note: `client` (conftest's "Tester") is the first-ever account, so it IS the admin (see
    # conftest.py's client fixture docstring) -- this test only checks the sections every user gets;
    # Admin-section visibility is tested properly in Task 4 with an explicitly non-admin account.
    t = text(client.get("/settings"))
    assert 'id="user"' in t and 'id="integrations"' in t
    assert "Apple Health" in t and "Hume" in t and "Coming soon" in t


def test_menu_has_one_settings_link_not_separate_2fa_and_backup(client):
    t = client.get("/protocols").text
    assert 'href="/settings"' in t
    assert 'href="/account/2fa"' not in t and 'href="/backup"' not in t


def test_change_username_requires_current_password(client, db, me):
    original = _current(me).username
    r = client.post("/settings/username", data={"username": "NewName", "current_password": "wrong"})
    assert r.status_code == 422 and "incorrect" in text(r).lower()
    assert _current(me).username == original

    r = client.post("/settings/username", data={"username": "NewName", "current_password": PW},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _current(me).username == "NewName"
    # restore for other tests sharing this session-scoped client/user
    r = client.post("/settings/username", data={"username": original, "current_password": PW},
                    follow_redirects=False)
    assert r.status_code == 303 and _current(me).username == original


def test_change_username_rejects_duplicate_case_insensitive(client, db):
    other_key = "dupuser"
    with SessionLocal() as s:
        if not s.scalar(select(User).where(User.username_key == other_key)):
            s.add(User(username="DupUser", username_key=other_key, password_hash=passwords.hash_password("x")))
            s.commit()
    r = client.post("/settings/username", data={"username": "dupuser", "current_password": PW})
    assert r.status_code == 422 and "taken" in text(r).lower()


def test_change_password_ends_other_sessions_not_this_one(client, db):
    # A genuinely separate session for the same account needs its own login (a new LoginSession row is
    # minted on every sign-in -- copying `client`'s cookies would just be a second handle on the SAME
    # session/row, which can't be independently kept and ended).
    from fastapi.testclient import TestClient
    from app.main import app

    other_device = TestClient(app, follow_redirects=False)
    other_device.post("/notice", data={"understand": "1"})
    other_device.post("/login", data={"username": "tester", "password": PW})
    assert other_device.get("/protocols").status_code == 200  # proves it starts out signed in

    r = client.post("/settings/password", data={"current_password": PW, "new_password": "Newer1!",
                                                 "confirm": "Newer1!"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/protocols").status_code == 200  # this session: still signed in
    assert other_device.get("/protocols").status_code in (302, 303, 307)  # the other device: signed out

    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username_key == "tester"))
        assert passwords.verify_password(u.password_hash, "Newer1!")
    client.post("/settings/password", data={"current_password": "Newer1!", "new_password": PW, "confirm": PW})


def test_change_password_wrong_current_rejected(client, db):
    r = client.post("/settings/password", data={"current_password": "wrong", "new_password": "Whatever1!",
                                                 "confirm": "Whatever1!"})
    assert r.status_code == 422 and "incorrect" in text(r).lower()


def test_timezone_saves_and_blank_clears_it(client, db, me):
    client.post("/settings/timezone", data={"mode": "manual", "timezone": "America/Chicago"})
    assert _current(me).timezone == "America/Chicago"
    client.post("/settings/timezone", data={"mode": "system", "timezone": ""})
    assert _current(me).timezone is None


def test_timezone_rejects_unknown_zone(client, db, me):
    r = client.post("/settings/timezone", data={"mode": "manual", "timezone": "Nowhere/Fake"})
    assert r.status_code == 422
    assert _current(me).timezone is None


def test_email_saves_and_validates(client, db, me):
    r = client.post("/settings/email", data={"email": "not-an-email"})
    assert r.status_code == 422
    client.post("/settings/email", data={"email": "me@example.com"})
    assert _current(me).email == "me@example.com"
    client.post("/settings/email", data={"email": ""})
    assert _current(me).email is None


def test_colorway_swatches_render(client):
    t = text(client.get("/settings"))
    for label in ("Light", "Dark", "Tequila Sunrise", "Fireworks", "Solarin", "The Bricks", "Retro",
                 "Greensleeves", "High Contrast"):
        assert label in t


def test_colorway_saves_and_sets_data_theme(client, db, me):
    client.post("/settings/display", data={"colorway": "tequila_sunrise"})
    assert _current(me).colorway.value == "tequila_sunrise"
    t = client.get("/protocols").text
    assert 'data-theme="tequila_sunrise"' in t

    client.post("/settings/display", data={"colorway": ""})
    assert _current(me).colorway is None
    t = client.get("/protocols").text
    assert "data-theme=" not in t  # Auto: no attribute, prefers-color-scheme rules as before


def test_colorway_rejects_unknown_value(client, db, me):
    r = client.post("/settings/display", data={"colorway": "not-a-real-one"})
    assert r.status_code == 422
    assert _current(me).colorway is None

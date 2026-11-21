"""S-01 registration can be closed, S-08 the Secure cookie flag, N-12 the passkey host pattern."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import config
from app.auth import passkeys, registration
from app.db import Base, SessionLocal
from app.main import app
from app.models import InstallationSetting, User

from tests.test_auth_flow import PW, accept, fresh, register, text, user  # noqa: F401


@pytest.fixture
def closed_default(monkeypatch):
    """The shipped behaviour: no AMIDE_REGISTRATION override (the suite sets it to open)."""
    monkeypatch.delenv("AMIDE_REGISTRATION", raising=False)
    with SessionLocal() as s:
        s.query(InstallationSetting).delete()
        s.commit()
    yield
    with SessionLocal() as s:
        s.query(InstallationSetting).delete()
        s.commit()


def test_the_first_account_is_always_allowed_then_it_closes(monkeypatch):
    monkeypatch.delenv("AMIDE_REGISTRATION", raising=False)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert registration.is_open(db)
        db.add(User(username="First", username_key="first", password_hash="x", is_admin=True))
        db.commit()
        assert not registration.is_open(db)


def test_registration_is_refused_once_an_account_exists(fresh, closed_default):
    accept(fresh)
    assert fresh.get("/register").status_code == 403
    r = register(fresh, "Intruder1")
    assert r.status_code == 403
    assert user("intruder1") is None


def test_the_welcome_page_hides_new_user_while_closed(fresh, closed_default):
    accept(fresh)
    page = text(fresh.get("/welcome"))
    assert "New User" not in page and "Login" in page
    assert "Add New User" not in text(fresh.get("/login"))


def test_the_admin_can_open_registration_in_settings(client, fresh, closed_default):
    accept(fresh)
    assert fresh.get("/register").status_code == 403
    assert client.post("/settings/admin/registration", data={"allow": "0"}, follow_redirects=False).status_code == 303
    assert fresh.get("/register").status_code == 403
    assert client.post("/settings/admin/registration", data={"allow": "1"}, follow_redirects=False).status_code == 303
    assert fresh.get("/register").status_code == 200
    assert "New User" in text(fresh.get("/welcome"))
    assert register(fresh, "Welcomed1").status_code == 303
    assert "Allow New Users To Register" in text(client.get("/settings"))


def test_only_an_admin_can_change_the_switch(client, closed_default):
    other = TestClient(app, follow_redirects=False)
    with SessionLocal() as s:
        s.add(InstallationSetting(key=registration.KEY, value="1"))
        s.commit()
    accept(other)
    register(other, "Plain1")
    r = other.post("/settings/admin/registration", data={"allow": "0"})
    assert r.status_code == 404
    with SessionLocal() as s:
        assert registration.admin_choice(s)


def test_the_environment_overrides_the_switch(fresh, monkeypatch, closed_default):
    accept(fresh)
    monkeypatch.setenv("AMIDE_REGISTRATION", "open")
    assert fresh.get("/register").status_code == 200
    with SessionLocal() as s:
        registration.set_open(s, True)
        s.commit()
    monkeypatch.setenv("AMIDE_REGISTRATION", "closed")
    assert fresh.get("/register").status_code == 403
    assert register(fresh, "Blocked1").status_code == 403


# ---------------------------------------------------------------- S-08 secure cookie

def test_the_public_url_does_not_stop_the_plain_http_lan_address_from_working(fresh, monkeypatch):
    """AMIDE_PUBLIC_URL pins the passkey site name; it must not force the Secure flag, or a browser on http://<lan address> drops the cookie."""
    monkeypatch.setattr(config, "PUBLIC_URL", "https://amide.example.test")
    r = fresh.post("/notice", data={"understand": "1"})
    assert "secure" not in r.headers["set-cookie"].lower()
    r = fresh.post("/notice", data={"understand": "1"}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()          # the same server, reached through the https proxy


def test_the_cookie_follows_the_request_scheme_without_a_public_url(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    plain = TestClient(app, follow_redirects=False)
    assert "secure" not in plain.post("/notice", data={"understand": "1"}).headers["set-cookie"].lower()
    tls = TestClient(app, base_url="https://testserver", follow_redirects=False)
    assert "secure" in tls.post("/notice", data={"understand": "1"}).headers["set-cookie"].lower()


def test_cookie_secure_helper(monkeypatch):
    class Req:
        headers: dict = {}

        class url:
            scheme = "http"
    monkeypatch.setattr(config, "PUBLIC_URL", "https://x.example.test")
    assert not config.cookie_secure(Req)                         # the public address alone no longer forces it
    Req.headers = {"x-forwarded-proto": "https"}
    assert config.cookie_secure(Req)


# ---------------------------------------------------------------- N-12 host pattern

@pytest.mark.parametrize("host", ["example.test", "example.test:8080", "localhost:8000", "192.0.2.7", "[::1]:8000", "a"])
def test_plain_hosts_are_accepted(host):
    assert passkeys.host_is_valid(host)


@pytest.mark.parametrize("host", ["", "evil.test/path", "user@evil.test", "evil.test?x=1", "a b.test", "evil.test:99999999",
                                  "-evil.test", "evil.test#", "ev\\il.test", "evil.test:port"])
def test_odd_hosts_are_refused(host):
    assert not passkeys.host_is_valid(host)


def test_passkeys_refuse_a_strange_host_when_no_public_url_is_set(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    r = client.post("/account/passkeys/options", headers={"x-forwarded-host": "evil.test/x@y"})
    assert r.status_code == 400
    r = client.post("/login/passkey/options", headers={"x-forwarded-host": "evil.test/x@y"})
    assert r.status_code == 400
    assert client.post("/account/passkeys/options").status_code == 200

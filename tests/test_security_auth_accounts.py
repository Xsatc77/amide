"""N-02 hashing off the event loop, N-03 PDF cap, N-05 credentials die with a password reset, N-13 security log."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config
from app.auth import audit, passwords
from app.db import SessionLocal
from app.main import app
from app.models import LoginSession, Passkey, SecurityEvent, TrustedDevice, User
from app.routers import labs

from tests.test_auth_flow import PW, accept, fresh, next_code, register, setup_2fa, text, user  # noqa: F401
from tests.test_passkeys_devices import Phone, add_passkey, passkey_login, signed_in  # noqa: F401


def browser(address: str) -> TestClient:
    return TestClient(app, follow_redirects=False, client=(address, 50000))


def events(*names) -> list[SecurityEvent]:
    with SessionLocal() as s:
        return list(s.scalars(select(SecurityEvent).where(SecurityEvent.event.in_(names)).order_by(SecurityEvent.id)))


# ---------------------------------------------------------------- N-02

@pytest.fixture
def where_hashing_runs(monkeypatch):
    """Records, for each verify/hash call, whether it ran on the event-loop thread."""
    seen = []

    def spy(real):
        def wrapper(*args):
            try:
                asyncio.get_running_loop()
                seen.append("loop")
            except RuntimeError:
                seen.append("worker")
            return real(*args)
        return wrapper

    monkeypatch.setattr(passwords, "verify_password", spy(passwords.verify_password))
    monkeypatch.setattr(passwords, "hash_password", spy(passwords.hash_password))
    return seen


def test_login_register_and_password_change_hash_off_the_event_loop(fresh, where_hashing_runs):
    accept(fresh)
    assert register(fresh, "Worker1").status_code == 303                         # hash
    fresh.post("/logout")
    assert fresh.post("/login", data={"username": "worker1", "password": PW}).status_code == 303      # verify
    assert fresh.post("/login", data={"username": "worker1", "password": "bad"}).status_code == 422
    assert fresh.post("/login", data={"username": "nosuchperson", "password": "bad"}).status_code == 422   # the dummy check
    r = fresh.post("/settings/password", data={"current_password": PW, "new_password": "Zz9!zz", "confirm": "Zz9!zz"})
    assert r.status_code == 303                                                  # verify + hash
    assert where_hashing_runs and set(where_hashing_runs) == {"worker"}


def test_a_name_that_does_not_exist_costs_a_real_hash_check(fresh, where_hashing_runs):
    accept(fresh)
    fresh.post("/login", data={"username": "nosuchperson", "password": "bad"})
    assert where_hashing_runs == ["worker"]


# ---------------------------------------------------------------- N-03

def _pdf(pages: int) -> bytes:
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [" + b" ".join(f"{3 + i} 0 R".encode() for i in range(pages)) + b"] /Count %d >>" % pages]
    objects += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>"] * pages
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def test_a_pdf_with_too_many_pages_is_refused_before_parsing(client, monkeypatch):
    read = []
    monkeypatch.setattr(labs.reports, "read_pdf", lambda lab, data: read.append(1))
    r = client.post("/labs/import", data={"lab": "quest"}, follow_redirects=False,
                    files={"report": ("big.pdf", _pdf(labs.PDF_MAX_PAGES + 1), "application/pdf")})
    assert r.status_code == 422 and f"more than {labs.PDF_MAX_PAGES} pages" in text(r)
    assert read == []


def test_a_short_pdf_still_reaches_the_reader(client, monkeypatch):
    read = []

    class Empty:
        rows = []
    monkeypatch.setattr(labs.reports, "read_pdf", lambda lab, data: read.append(1) or Empty())
    r = client.post("/labs/import", data={"lab": "quest"}, follow_redirects=False,
                    files={"report": ("ok.pdf", _pdf(2), "application/pdf")})
    assert read == [1] and "No Quest" in text(r)


# ---------------------------------------------------------------- N-05

def _signed_in_user_with_keys(name: str):
    c = browser("198.51.100.40")
    accept(c)
    register(c, name)
    add_passkey(c, Phone())
    uid = user(name.lower()).id
    with SessionLocal() as s:
        s.add(TrustedDevice(user_id=uid, token_hash=name * 3, created_at=audit.naive_utcnow(), expires_at=audit.naive_utcnow()))
        s.commit()
    return c, uid


def _left(uid):
    with SessionLocal() as s:
        return (s.query(Passkey).filter(Passkey.user_id == uid).count(), s.query(TrustedDevice).filter(TrustedDevice.user_id == uid).count(),
                s.query(LoginSession).filter(LoginSession.user_id == uid).count())


def test_an_admin_password_reset_removes_passkeys_devices_and_sessions(client):
    c, uid = _signed_in_user_with_keys("Reset1")
    assert _left(uid) == (1, 1, 1)
    r = client.post(f"/settings/admin/users/{uid}/reset-password", data={"password": "Nn7!nn", "confirm": "Nn7!nn"}, follow_redirects=False)
    assert r.status_code == 303
    assert _left(uid) == (0, 0, 0)
    assert not signed_in(c)


def test_an_admin_removing_2fa_removes_passkeys_devices_and_sessions(client):
    c, uid = _signed_in_user_with_keys("Nofa1")
    with SessionLocal() as s:
        s.get(User, uid).totp_enabled = True
        s.get(User, uid).totp_secret = "JBSWY3DPEHPK3PXP"
        s.commit()
    r = client.post(f"/settings/admin/users/{uid}/remove-2fa", follow_redirects=False)
    assert r.status_code == 303
    assert _left(uid) == (0, 0, 0)


def test_changing_your_own_password_keeps_this_session_only(fresh):
    accept(fresh)
    register(fresh, "Own1")
    add_passkey(fresh, Phone())
    other = browser("198.51.100.41")
    accept(other)
    assert other.post("/login", data={"username": "own1", "password": PW}).status_code == 303
    r = fresh.post("/settings/password", data={"current_password": PW, "new_password": "Zz9!zz", "confirm": "Zz9!zz"})
    assert r.status_code == 303
    assert signed_in(fresh) and not signed_in(other)
    assert _left(user("own1").id)[0] == 1      # the person proved the password in this request: their passkey stays


def test_adding_a_passkey_needs_the_password_again(fresh):
    accept(fresh)
    register(fresh, "Reauth1")
    phone = Phone()
    for proof in ({}, {"password": "wrong"}, {"password": ""}):
        options = fresh.post("/account/passkeys/options").json()
        r = fresh.post("/account/passkeys", json={"name": "x", **proof, "credential": phone.register(options)})
        assert r.status_code == 403, proof
    assert _left(user("reauth1").id)[0] == 0
    assert add_passkey(fresh, phone).status_code == 200
    assert _left(user("reauth1").id)[0] == 1


def test_with_2fa_on_adding_a_passkey_needs_a_fresh_code_not_the_password(fresh, monkeypatch):
    accept(fresh)
    register(fresh, "Reauth2")
    secret = setup_2fa(fresh)
    phone = Phone()
    options = fresh.post("/account/passkeys/options").json()
    r = fresh.post("/account/passkeys", json={"name": "x", "password": PW, "credential": phone.register(options)})
    assert r.status_code == 403
    assert add_passkey(fresh, phone, secret=secret).status_code == 200
    monkeypatch.setattr(config, "PUBLIC_URL", "https://amide.example.test")      # the add form only shows on a secure address
    assert "6-digit code" in text(fresh.get("/account/passkeys"))


def test_the_passkey_page_asks_for_the_password(fresh, monkeypatch):
    accept(fresh)
    register(fresh, "Reauth3")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://amide.example.test")      # the add form only shows on a secure address
    assert 'name="password"' in text(fresh.get("/account/passkeys"))


# ---------------------------------------------------------------- N-13

def test_sign_ins_failures_lockouts_and_logout_are_logged_without_secrets(fresh, caplog):
    caplog.set_level(logging.INFO, logger="amide.security")
    accept(fresh)
    register(fresh, "Audited1")
    fresh.post("/logout")
    secret_try = "Sup3rSecret!try"
    for _ in range(config.LOCKOUT_ATTEMPTS):
        fresh.post("/login", data={"username": "audited1", "password": secret_try})
    fresh.post("/login", data={"username": "typo-name", "password": secret_try})
    names = [e.event for e in events("registered", "logout", "login_failed", "lockout")]
    assert names.count("login_failed") >= config.LOCKOUT_ATTEMPTS + 1 and "lockout" in names and "registered" in names and "logout" in names
    with SessionLocal() as s:
        dump = " ".join(str(v) for e in s.scalars(select(SecurityEvent)) for v in (e.username, e.event, e.detail, e.client))
    assert secret_try not in dump and PW not in dump
    assert any(r.name == "amide.security" and "lockout" in r.getMessage() for r in caplog.records)
    assert secret_try not in caplog.text


def test_a_successful_login_and_admin_actions_are_logged(client, fresh):
    accept(fresh)
    register(fresh, "Audited2")
    fresh.post("/logout")
    fresh.post("/login", data={"username": "audited2", "password": PW})
    uid = user("audited2").id
    client.post(f"/settings/admin/users/{uid}/reset-password", data={"password": "Nn7!nn", "confirm": "Nn7!nn"})
    client.post("/settings/admin/users/new", data={"username": "Audited3", "password": "Nn7!nn", "confirm": "Nn7!nn"})
    client.post(f"/settings/admin/users/{user('audited3').id}/delete", data={"username": "Audited3"})
    got = {e.event for e in events("login_ok", "admin_password_reset", "admin_user_created", "admin_user_deleted")}
    assert got == {"login_ok", "admin_password_reset", "admin_user_created", "admin_user_deleted"}


def test_passkey_and_password_changes_are_logged(fresh):
    accept(fresh)
    register(fresh, "Audited4")
    add_passkey(fresh, Phone())
    key = _left(user("audited4").id)
    assert key[0] == 1
    with SessionLocal() as s:
        key_id = s.scalar(select(Passkey.id).where(Passkey.user_id == user("audited4").id))
    fresh.post(f"/account/passkeys/{key_id}/delete")
    fresh.post("/settings/password", data={"current_password": PW, "new_password": "Zz9!zz", "confirm": "Zz9!zz"})
    got = {e.event for e in events("passkey_added", "passkey_removed", "password_changed")}
    assert got == {"passkey_added", "passkey_removed", "password_changed"}


def test_record_never_raises_and_truncates():
    audit.record(None, "login_ok")                      # a broken session: swallowed
    with SessionLocal() as s:
        audit.record(s, "login_failed", username="x" * 500, detail="d" * 500)
        s.commit()
        row = s.scalar(select(SecurityEvent).order_by(SecurityEvent.id.desc()))
        assert len(row.username) == 64 and len(row.detail) == 200


def test_only_the_admin_sees_the_security_log(client, fresh):
    page = text(client.get("/settings"))
    assert "Security Log" in page
    accept(fresh)
    register(fresh, "Audited5")
    assert "Security Log" not in text(fresh.get("/settings"))

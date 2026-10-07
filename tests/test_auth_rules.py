from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from sqlalchemy.orm import Session

from app import config
from app.auth import passwords, sessions, totp
from app.db import make_engine
from app.migrate import upgrade_db
from app.models import LoginSession, User

T0 = datetime(2026, 9, 23, 12, 0, 0)


# ---------------------------------------------------------------- usernames & passwords

@pytest.mark.parametrize("name,ok", [("chris", True), ("Chris.W-77_", True), ("ab", False), ("a" * 33, False),
                                     ("has space", False), ("émile", False), ("", False)])
def test_username_rules(name, ok):
    assert (passwords.username_error(name) is None) == ok


def test_password_checks_report_each_rule(monkeypatch):
    monkeypatch.setattr(config, "PASSWORD_MIN_LENGTH", 4)
    checks = {c.key: c.met for c in passwords.password_checks("aB3!")}
    assert checks == {"length": True, "upper": True, "lower": True, "number": True, "special": True}
    checks = {c.key: c.met for c in passwords.password_checks("abc")}
    assert checks == {"length": False, "upper": False, "lower": True, "number": False, "special": False}


def test_password_min_length_is_configurable(monkeypatch):
    monkeypatch.setattr(config, "PASSWORD_MIN_LENGTH", 12)
    assert not {c.key: c.met for c in passwords.password_checks("aB3!")}["length"]
    assert "12" in next(c.label for c in passwords.password_checks("") if c.key == "length")


def test_password_errors_include_mismatch(monkeypatch):
    monkeypatch.setattr(config, "PASSWORD_MIN_LENGTH", 4)
    assert passwords.password_errors("aB3!", "aB3!") == []
    assert "Passwords don't match." in passwords.password_errors("aB3!", "aB3?")
    assert len(passwords.password_errors("abc", "abc")) == 4  # length, upper, number, special


def test_hash_and_verify_is_case_sensitive():
    h = passwords.hash_password("aB3!")
    assert h != "aB3!" and passwords.verify_password(h, "aB3!")
    assert not passwords.verify_password(h, "ab3!")
    assert not passwords.verify_password("not-a-hash", "aB3!")


# ---------------------------------------------------------------- TOTP

def test_totp_verify_window():
    secret = totp.new_secret()
    t = pyotp.TOTP(secret)
    at = lambda dt: t.at(dt.replace(tzinfo=timezone.utc))  # noqa: E731  (Amide's clock is naive UTC)
    assert totp.verify(secret, at(T0), now=T0)
    assert totp.verify(secret, at(T0 - timedelta(seconds=30)), now=T0)  # one step of clock drift
    assert not totp.verify(secret, at(T0 - timedelta(seconds=90)), now=T0)
    assert not totp.verify(secret, "12345", now=T0) and not totp.verify(secret, "abcdef", now=T0)


def test_totp_qr_and_uri():
    secret = totp.new_secret()
    uri = totp.provisioning_uri(secret, "Chris")
    assert uri.startswith("otpauth://totp/Amide:Chris?") and "issuer=Amide" in uri
    assert totp.qr_svg(uri).lstrip().startswith("<svg")


# ---------------------------------------------------------------- sessions & lockout

@pytest.fixture
def s(tmp_path):
    url = f"sqlite:///{(tmp_path / 'auth.db').as_posix()}"
    upgrade_db(url)
    engine = make_engine(url)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_session_expires_after_idle_limit(s):
    row = sessions.create(s, now=T0)
    token = row.id
    assert len(token) >= 40
    assert sessions.get_live(s, token, now=T0 + timedelta(minutes=9, seconds=59)) is not None
    sessions.touch(s, row, now=T0 + timedelta(minutes=9, seconds=59))
    assert sessions.get_live(s, token, now=T0 + timedelta(minutes=19, seconds=58)) is not None
    assert sessions.get_live(s, token, now=T0 + timedelta(minutes=30)) is None
    assert s.get(LoginSession, token) is None  # expired sessions are removed
    assert sessions.get_live(s, "nope", now=T0) is None and sessions.get_live(s, None, now=T0) is None


def test_lockout(s):
    u = User(username="Chris", username_key="chris", password_hash=passwords.hash_password("aB3!"))
    s.add(u)
    s.commit()
    for _ in range(4):
        sessions.record_failure(u, now=T0)
    assert not sessions.is_locked(u, now=T0)
    sessions.record_failure(u, now=T0)
    assert sessions.is_locked(u, now=T0 + timedelta(minutes=14))
    assert not sessions.is_locked(u, now=T0 + timedelta(minutes=15, seconds=1))
    sessions.clear_failures(u)
    assert u.failed_attempts == 0 and u.locked_until is None


def test_the_shipped_minimum_password_length_is_eight():
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k != "AMIDE_PASSWORD_MIN_LENGTH"}
    out = subprocess.run([sys.executable, "-c", "from app import config; print(config.PASSWORD_MIN_LENGTH)"], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "8"

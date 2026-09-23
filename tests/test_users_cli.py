from datetime import datetime

import pytest
from sqlalchemy import select

from app import users
from app.auth import passwords
from app.db import SessionLocal
from app.models import User


@pytest.fixture
def victim(client):
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username_key == "clivictim"))
        if u is None:
            u = User(username="CliVictim", username_key="clivictim", password_hash=passwords.hash_password("Old1!"))
            s.add(u)
        u.totp_enabled, u.totp_secret, u.totp_last_step = True, "JBSWY3DPEHPK3PXP", 5
        u.failed_attempts, u.locked_until = 3, datetime(2099, 1, 1)
        s.commit()
    return "clivictim"


def reload(name) -> User:
    with SessionLocal() as s:
        return s.scalar(select(User).where(User.username_key == name))


def test_list(victim, capsys):
    assert users.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "CliVictim" in out and "2FA on" in out and "Tester" in out and "admin" in out


def test_reset_password(victim, capsys):
    assert users.main(["reset-password", "CLIVICTIM", "--password", "New1!"]) == 0
    u = reload(victim)
    assert passwords.verify_password(u.password_hash, "New1!") and not passwords.verify_password(u.password_hash, "Old1!")
    assert u.failed_attempts == 0 and u.locked_until is None  # lock lifted too


def test_reset_password_enforces_rules(victim, capsys):
    assert users.main(["reset-password", victim, "--password", "weak"]) == 1
    assert "uppercase" in capsys.readouterr().err


def test_reset_2fa(victim):
    assert users.main(["reset-2fa", victim]) == 0
    u = reload(victim)
    assert not u.totp_enabled and u.totp_secret is None and u.totp_last_step is None


def test_unknown_user(capsys):
    assert users.main(["reset-2fa", "nobody-at-all"]) == 1
    assert "No user" in capsys.readouterr().err

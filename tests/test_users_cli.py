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


def _plant(uid):
    """Give the account a signed-in browser, a passkey and a remembered device (counted by owner, never by id)."""
    from app.models import LoginSession, Passkey, TrustedDevice
    from app.models import naive_utcnow
    with SessionLocal() as s:
        now = naive_utcnow()
        s.add(LoginSession(id="cli-test-session-" + str(uid), user_id=uid, created_at=now, last_seen=now))
        s.add(Passkey(user_id=uid, credential_id="cli-cred-" + str(uid), public_key=b"k", name="Phone", created_at=now))
        s.add(TrustedDevice(user_id=uid, token_hash=("c" * 60) + str(uid).zfill(4), created_at=now, expires_at=now))
        s.commit()


def _left(uid):
    from app.models import LoginSession, Passkey, TrustedDevice
    with SessionLocal() as s:
        return [s.query(m).filter(m.user_id == uid).count() for m in (LoginSession, Passkey, TrustedDevice)]


def _events(name):
    from app.models import SecurityEvent
    with SessionLocal() as s:
        return [e for e in s.scalars(select(SecurityEvent).where(SecurityEvent.event == name))]


@pytest.mark.parametrize("command,event", [(["reset-password", "{n}", "--password", "New1!"], "cli_password_reset"),
                                           (["reset-2fa", "{n}"], "cli_2fa_removed")])
def test_cli_ends_sessions_passkeys_devices_and_logs(victim, command, event):
    uid = reload(victim).id
    _plant(uid)
    assert _left(uid) == [1, 1, 1]
    before = len(_events(event))
    assert users.main([c.replace("{n}", victim) for c in command]) == 0
    assert _left(uid) == [0, 0, 0]
    rows = _events(event)
    assert len(rows) == before + 1 and rows[-1].user_id == uid and rows[-1].client is None
    assert "New1!" not in (rows[-1].detail or "")

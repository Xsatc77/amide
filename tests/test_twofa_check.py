"""app.auth.twofa.check_code: a code works once, and wrong codes count toward the lockout."""

from datetime import timedelta, timezone

import pyotp
import pytest

from app import config, photo_access
from app.auth import passwords, sessions
from app.auth.twofa import check_code
from app.models import LoginSession, User, naive_utcnow


def _at(secret, when):
    return pyotp.TOTP(secret).at(when.replace(tzinfo=timezone.utc))       # the app's times are naive UTC


def test_still_importable_from_the_router():
    from app.routers.auth import check_code as old
    assert old is check_code


@pytest.fixture
def twofa_user(db):
    secret = pyotp.random_base32()
    u = User(username="TwofaCheck", username_key="twofacheck", password_hash=passwords.hash_password("Pw1!"),
             totp_enabled=True, totp_secret=secret)
    db.add(u)
    db.commit()
    yield u, secret
    db.query(LoginSession).filter(LoginSession.user_id == u.id).delete()
    db.delete(u)
    db.commit()


def test_a_used_code_is_refused_the_second_time(twofa_user):
    user, secret = twofa_user
    now = sessions.now_utc()
    code = _at(secret, now)
    assert check_code(user, code, now) is None
    assert user.totp_last_step is not None
    assert "already used" in check_code(user, code, now)
    # an older step is refused too, not only the identical one
    assert "already used" in check_code(user, _at(secret, now - timedelta(seconds=30)), now)


def test_a_wrong_code_is_refused_and_not_recorded(twofa_user):
    user, secret = twofa_user
    now = sessions.now_utc()
    wrong = "000000" if _at(secret, now) != "000000" else "111111"
    assert "didn't match" in check_code(user, wrong, now)
    assert user.totp_last_step is None


def test_wrong_codes_count_toward_lockout(db, twofa_user):
    user, _ = twofa_user
    now = sessions.now_utc()
    row = LoginSession(id="twofa-check-session", user_id=user.id, created_at=now, last_seen=now)
    db.add(row)
    db.commit()
    for _ in range(config.LOCKOUT_ATTEMPTS):
        assert photo_access.unlock(db, user, row, "123456", now)       # a message, never None
    assert sessions.is_locked(user, now)
    assert "Too many attempts" in photo_access.unlock(db, user, row, "123456", now)

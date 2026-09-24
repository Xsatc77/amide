from datetime import datetime, timedelta

import pytest

from app.auth import sessions
from app.auth.sessions import end_all_sessions, end_other_sessions
from app.db import make_engine
from app.migrate import upgrade_db
from app.models import LoginSession
from app.settings.rules import TIMEZONES, email_error, timezone_error
from sqlalchemy.orm import Session

T0 = datetime(2026, 9, 24, 12, 0, 0)


@pytest.mark.parametrize("value,ok", [("a@b.com", True), ("first.last@sub.example.co", True),
                                     ("", True), ("not-an-email", False), ("a@b", False), ("@b.com", False)])
def test_email_error(value, ok):
    assert (email_error(value) is None) == ok


def test_timezone_list_has_common_zones():
    assert "America/Chicago" in TIMEZONES and "UTC" in TIMEZONES
    assert TIMEZONES == sorted(TIMEZONES)


@pytest.mark.parametrize("value,ok", [("", True), ("America/Chicago", True), ("Nowhere/Fake", False)])
def test_timezone_error(value, ok):
    assert (timezone_error(value) is None) == ok


@pytest.fixture
def s(tmp_path):
    url = f"sqlite:///{(tmp_path / 'sessions.db').as_posix()}"
    upgrade_db(url)
    engine = make_engine(url)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_end_other_sessions_keeps_only_the_given_one(s):
    from app.models import User
    from app.auth import passwords
    u = User(username="A", username_key="a", password_hash=passwords.hash_password("x"))
    s.add(u)
    s.commit()
    keep = sessions.create(s, T0)
    keep.user_id = u.id
    other1, other2 = sessions.create(s, T0), sessions.create(s, T0)
    other1.user_id = other2.user_id = u.id
    s.commit()

    end_other_sessions(s, u.id, keep.id)
    remaining = {row.id for row in s.query(LoginSession).all()}
    assert remaining == {keep.id}


def test_end_all_sessions_removes_every_session_for_that_user(s):
    from app.models import User
    from app.auth import passwords
    u = User(username="B", username_key="b", password_hash=passwords.hash_password("x"))
    other_user = User(username="C", username_key="c", password_hash=passwords.hash_password("x"))
    s.add_all([u, other_user])
    s.commit()
    mine1, mine2 = sessions.create(s, T0), sessions.create(s, T0)
    mine1.user_id = mine2.user_id = u.id
    not_mine = sessions.create(s, T0)
    not_mine.user_id = other_user.id
    s.commit()

    end_all_sessions(s, u.id)
    remaining = {row.id for row in s.query(LoginSession).all()}
    assert remaining == {not_mine.id}

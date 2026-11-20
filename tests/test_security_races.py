"""Races between simultaneous requests: a 2FA code is good once, the lockout counter cannot lose a count, and the same new
username registered twice at once is a clean 'already taken', never a server error.

The database-level ones are deterministic: every request's session loads the account BEFORE any of them writes (exactly what happens
when requests overlap), then they run one after the other."""

import threading
from datetime import timedelta, timezone

import pyotp
import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import passwords, sessions
from app.auth.twofa import check_code
from app.db import SessionLocal
from app.main import app
from app.models import LoginSession, SecurityEvent, User


def _user(name, **extra):
    with SessionLocal() as s:
        u = User(username=name, username_key=name.lower(), password_hash=passwords.hash_password("Pw1!"), **extra)
        s.add(u)
        s.commit()
        return u.id


def _drop(uid):
    with SessionLocal() as s:
        s.query(SecurityEvent).filter(SecurityEvent.user_id == uid).delete()
        s.query(LoginSession).filter(LoginSession.user_id == uid).delete()
        s.query(User).filter(User.id == uid).delete()
        s.commit()


def test_a_2fa_code_cannot_be_used_twice_by_overlapping_requests():
    secret = pyotp.random_base32()
    uid = _user("RaceTotp", totp_enabled=True, totp_secret=secret)
    try:
        now = sessions.now_utc()
        code = pyotp.TOTP(secret).at(now.replace(tzinfo=timezone.utc))
        sessions_ = [SessionLocal() for _ in range(3)]
        users = [s.get(User, uid) for s in sessions_]                 # all three have read "no code used yet"
        answers = []
        for s, u in zip(sessions_, users):
            answers.append(check_code(u, code, now))
            s.commit()
        assert answers.count(None) == 1, answers
        assert all("already used" in a for a in answers if a)
        for s in sessions_:
            s.close()
    finally:
        _drop(uid)


def test_a_2fa_code_from_threads_is_accepted_once():
    secret = pyotp.random_base32()
    uid = _user("RaceTotpThreads", totp_enabled=True, totp_secret=secret)
    try:
        now = sessions.now_utc()
        code = pyotp.TOTP(secret).at(now.replace(tzinfo=timezone.utc))
        barrier, answers = threading.Barrier(6), []

        def attempt():
            with SessionLocal() as s:
                u = s.get(User, uid)
                barrier.wait()
                answers.append(check_code(u, code, now))
                s.commit()

        threads = [threading.Thread(target=attempt) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(answers) == 6 and answers.count(None) == 1, answers
    finally:
        _drop(uid)


def test_lockout_counts_every_failure_even_when_requests_overlap():
    uid = _user("RaceLock")
    try:
        now = sessions.now_utc()
        opened = [SessionLocal() for _ in range(config.LOCKOUT_ATTEMPTS)]
        users = [s.get(User, uid) for s in opened]                    # each request read failed_attempts = 0
        for s, u in zip(opened, users):
            sessions.record_failure(u, now)
            s.commit()
        with SessionLocal() as s:
            row = s.get(User, uid)
            assert sessions.is_locked(row, now), (row.failed_attempts, row.locked_until)
            assert row.failed_attempts == 0
        assert sessions.is_locked(users[-1], now)
        for s in opened:
            s.close()
    finally:
        _drop(uid)


def test_lockout_from_threads_locks_after_the_limit():
    uid = _user("RaceLockThreads")
    try:
        now = sessions.now_utc()
        barrier = threading.Barrier(config.LOCKOUT_ATTEMPTS)

        def fail():
            with SessionLocal() as s:
                u = s.get(User, uid)
                barrier.wait()
                sessions.record_failure(u, now)
                s.commit()

        threads = [threading.Thread(target=fail) for _ in range(config.LOCKOUT_ATTEMPTS)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        with SessionLocal() as s:
            assert sessions.is_locked(s.get(User, uid), now + timedelta(seconds=1))
    finally:
        _drop(uid)


def test_the_same_new_username_registered_twice_at_once_is_one_account_and_one_clean_refusal(monkeypatch):
    import asyncio
    real = passwords.hash_password_async
    gate = threading.Barrier(2)

    async def slow_hash(password):                                     # keeps both requests inside the window between "name is free" and "insert"
        await asyncio.sleep(0.4)
        return await real(password)

    monkeypatch.setattr(passwords, "hash_password_async", slow_hash)
    results, errors = [], []

    def go():
        try:
            c = TestClient(app)                                       # no `with`: no second startup
            if True:
                c.post("/notice", data={"understand": "1"})
                gate.wait()
                r = c.post("/register", data={"username": "RaceTwin", "password": "Pw1!", "confirm": "Pw1!"})
                results.append((r.status_code, r.url.path, "already taken" in r.text))
        except Exception as error:                                     # an unhandled IntegrityError would surface here
            errors.append(repr(error))

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    try:
        assert not errors, errors
        assert sorted(r[0] for r in results) == [200, 422], results
        assert [r for r in results if r[0] == 422][0][2] is True
        with SessionLocal() as s:
            assert s.query(User).filter(User.username_key == "racetwin").count() == 1
    finally:
        with SessionLocal() as s:
            for u in s.query(User).filter(User.username_key == "racetwin").all():
                _drop(u.id)


def test_an_integrity_error_at_insert_time_is_a_clean_message(monkeypatch, client):
    """Another process may take the name between the check and the insert: the unique constraint answers, and so does the page."""

    uid = _user("TakenElsewhere")
    real_scalar = None
    try:
        # Make the in-lock name check blind (as if the other process committed just after it) so the insert hits the constraint.
        from sqlalchemy.orm import Session
        real_scalar = Session.scalar

        def blind(self, statement, *a, **k):
            if "username_key" in str(statement) and "LIMIT" not in str(statement).upper():
                return None
            return real_scalar(self, statement, *a, **k)

        c = TestClient(app)
        if True:
            c.post("/notice", data={"understand": "1"})
            monkeypatch.setattr(Session, "scalar", blind)
            r = c.post("/register", data={"username": "TakenElsewhere", "password": "Pw1!", "confirm": "Pw1!"})
        assert r.status_code == 422 and "already taken" in r.text
    finally:
        monkeypatch.undo()
        _drop(uid)


def test_accepting_a_share_that_was_declined_a_moment_earlier_is_a_404_not_a_500(monkeypatch):
    from app.models import Share, ShareCategory, naive_utcnow
    from app.routers import settings as settings_router

    owner = _user("ShareRaceOwner")
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    assert other.post("/register", data={"username": "ShareRaceGrantee", "password": "Pw1!", "confirm": "Pw1!"}).status_code == 303
    with SessionLocal() as s:
        grantee = s.query(User).filter(User.username_key == "shareracegrantee").one().id
        s.add(Share(owner_id=owner, grantee_id=grantee, category=ShareCategory.INVENTORY))
        s.commit()
    real = settings_router._my_received_share

    def stale(session, uid, owner_id, category):
        row = real(session, uid, owner_id, category)                # this request has read the offer ...
        with SessionLocal() as other_session:                       # ... and a simultaneous decline deletes it before the accept writes
            other_session.query(Share).filter(Share.owner_id == owner_id, Share.grantee_id == uid).delete()
            other_session.commit()
        return row

    monkeypatch.setattr(settings_router, "_my_received_share", stale)
    try:
        r = other.post(f"/settings/shared-with-me/{owner}/inventory/accept")
        assert r.status_code == 404, r.status_code
    finally:
        monkeypatch.undo()
        with SessionLocal() as s:
            s.query(Share).filter(Share.owner_id == owner).delete()
            s.commit()
        _drop(owner)
        _drop(grantee)

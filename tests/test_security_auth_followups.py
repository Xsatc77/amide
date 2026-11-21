"""Auth follow-ups: the lock must not reveal a name, 'current password' prompts share the sign-in lockout, the security log is trimmed,
a name change ends other sessions, throttle tables are thread-safe and bounded, and the proxy / secure-cookie start-up warnings."""

import logging
import threading
from datetime import timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from sqlalchemy import select

from app import config
from app.auth import audit, gate, sessions, throttle
from app.db import SessionLocal
from app.ingest.tokens import RateLimiter
from app.models import SecurityEvent

from tests.test_auth_flow import PW, accept, text, user
from tests.test_redteam_auth import browser, login_page, member

GENERIC = "Username or password is incorrect."


def _events(*names, username=None):
    with SessionLocal() as s:
        q = select(SecurityEvent).where(SecurityEvent.event.in_(names))
        if username:
            q = q.where(SecurityEvent.username == username)
        return list(s.scalars(q))


def _signed_in(c) -> bool:
    return c.get("/protocols").status_code == 200


# ---------------------------------------------------------------- 1. the lock does not prove a name exists

def test_a_locked_name_looks_like_an_unknown_one_to_a_stranger(client):
    member("fu_oracle", "203.0.113.130")
    stranger = browser("203.0.113.131")
    accept(stranger)
    shown = [login_page(stranger, "fu_oracle", "bad") for _ in range(config.LOCKOUT_ATTEMPTS)]      # the 5th one locks the account
    assert user("fu_oracle").locked_until is not None
    assert all(r.status_code == 422 and GENERIC in text(r) for r in shown)
    other = browser("203.0.113.132")
    accept(other)
    real = login_page(other, "fu_oracle", PW)                  # even the right password: the lock is not shown
    fake = login_page(other, "fu_nonexistent", PW)
    assert (real.status_code, GENERIC in text(real)) == (fake.status_code, GENERIC in text(fake)) == (422, True)
    assert "Too many attempts" not in text(real)


def test_the_lock_message_is_still_shown_to_an_address_known_for_the_name(client):
    c = member("fu_known", "203.0.113.133")
    c.post("/logout")
    assert login_page(c, "fu_known").status_code == 303        # now a known address for this name
    c.post("/logout")
    replies = [text(login_page(c, "fu_known", "bad")) for _ in range(config.LOCKOUT_ATTEMPTS)]
    assert "Too many attempts" in replies[-1]                  # the just-locked answer to a known address


# ---------------------------------------------------------------- 2. 'current password' prompts share the lockout

def test_wrong_current_passwords_in_settings_count_lock_and_are_logged(client):
    c = member("fu_brute", "203.0.113.134")
    for i in range(config.LOCKOUT_ATTEMPTS):
        r = c.post("/settings/password", data={"current_password": f"Wrong{i}!x", "new_password": PW + "n", "confirm": PW + "n"})
        assert r.status_code == 422
    assert user("fu_brute").locked_until is not None
    right = c.post("/settings/password", data={"current_password": PW, "new_password": PW + "n", "confirm": PW + "n"})
    assert right.status_code == 422 and "Too many attempts" in text(right)          # refused while locked, even with the right one
    renamed = c.post("/settings/username", data={"username": "fu_brute2", "current_password": PW})
    assert renamed.status_code == 422 and user("fu_brute").username == "fu_brute"
    names = [e.event for e in _events("password_reauth_failed", "lockout", username="fu_brute")]
    assert names.count("password_reauth_failed") == config.LOCKOUT_ATTEMPTS and "lockout" in names
    with SessionLocal() as s:
        dump = " ".join(str(v) for e in s.scalars(select(SecurityEvent)) for v in (e.username, e.event, e.detail))
    assert "Wrong0!x" not in dump


def test_a_right_current_password_clears_earlier_wrong_ones(client):
    c = member("fu_clear", "203.0.113.135")
    c.post("/settings/username", data={"username": "fu_clear", "current_password": "nope"})
    assert user("fu_clear").failed_attempts == 1
    assert c.post("/settings/username", data={"username": "fu_clear", "current_password": PW}).status_code == 303
    assert user("fu_clear").failed_attempts == 0


# ---------------------------------------------------------------- 3. security log retention

def test_the_security_log_drops_old_lines_and_keeps_a_bounded_number(client, monkeypatch):
    now = audit.naive_utcnow()
    with SessionLocal() as s:
        s.add_all([SecurityEvent(created_at=now - timedelta(days=audit.RETENTION_DAYS + 1), event="fu_old", detail="old"),
                   SecurityEvent(created_at=now - timedelta(days=audit.RETENTION_DAYS - 1), event="fu_recent", detail="recent")])
        s.commit()
        audit.purge(s, now)
        assert s.scalar(select(SecurityEvent.id).where(SecurityEvent.event == "fu_old")) is None
        assert s.scalar(select(SecurityEvent.id).where(SecurityEvent.event == "fu_recent")) is not None
        s.add_all([SecurityEvent(created_at=now, event="fu_cap", detail=str(i)) for i in range(8)])
        s.commit()
        total = s.query(SecurityEvent).count()
        monkeypatch.setattr(audit, "MAX_ROWS", total - 3)
        assert audit.purge(s, now) == 3
        assert s.query(SecurityEvent).count() == total - 3
        newest = s.scalar(select(SecurityEvent).order_by(SecurityEvent.id.desc()))
        assert newest.event == "fu_cap" and newest.detail == "7"        # the oldest went, not the newest
        s.query(SecurityEvent).filter(SecurityEvent.event.in_(["fu_recent", "fu_cap"])).delete()
        s.commit()


def test_purge_stale_also_trims_the_log(client):
    now = sessions.now_utc()
    with SessionLocal() as s:
        s.add(SecurityEvent(created_at=now - timedelta(days=400), event="fu_ancient", detail=""))
        s.commit()
        sessions.purge_stale(s, now)
        assert s.scalar(select(SecurityEvent.id).where(SecurityEvent.event == "fu_ancient")) is None


# ---------------------------------------------------------------- 4. username change

def test_a_username_change_is_logged_and_ends_other_sessions(client):
    c = member("fu_rename", "203.0.113.136")
    other = browser("203.0.113.137")
    accept(other)
    assert login_page(other, "fu_rename").status_code == 303
    assert _signed_in(other)
    assert c.post("/settings/username", data={"username": "fu_renamed", "current_password": PW}).status_code == 303
    assert _signed_in(c) and not _signed_in(other)
    rows = _events("username_changed", username="fu_renamed")
    assert rows and "fu_rename" in rows[0].detail and PW not in rows[0].detail


# ---------------------------------------------------------------- 6. thread safety and bounded memory

def _hammer(work, threads=6, calls=1500):
    errors = []

    def run(n):
        try:
            for i in range(calls):
                work(n, i)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    pool = [threading.Thread(target=run, args=(n,)) for n in range(threads)]
    [t.start() for t in pool]
    [t.join() for t in pool]
    assert errors == []


def test_the_rate_limiter_forgets_quiet_keys_and_is_capped():
    lim = RateLimiter(5, 10.0)
    for i in range(1000):
        lim.allow(f"quiet{i}", 0.0)
    for i in range(RateLimiter.SWEEP_EVERY):
        lim.allow("busy", 100.0 + i * 0.001)              # long after the window: the sweep drops every quiet key
    assert len(lim._hits) < 50
    lim.MAX_KEYS = 100
    for i in range(500):
        lim.allow(f"k{i}", 200.0 + i * 0.001)             # all inside the window: only the cap can shrink the table
    assert len(lim._hits) <= 101 and "k499" in lim._hits


def test_the_rate_limiter_survives_threads():
    lim = RateLimiter(10, 1.0)
    _hammer(lambda n, i: lim.allow(f"{n}-{i % 700}", i * 0.01))


def test_the_throttle_tables_survive_threads_and_stay_bounded(monkeypatch):
    t = throttle.Throttle()
    now = sessions.now_utc()
    monkeypatch.setattr(throttle, "MAX_KEYS", 300)
    _hammer(lambda n, i: (t.check("login", f"10.{n}.{i % 250}.{i % 7}", i * 0.01),
                          throttle.note_failure(f"10.{n}.{i % 250}.{i % 7}", "name", now),
                          throttle.note_success(f"10.{n}.{i % 250}.{i % 7}", "name", now),
                          throttle.is_known(f"10.{n}.{i % 250}.{i % 7}", "name", now),
                          throttle.backoff_wait(f"10.{n}.{i % 250}.{i % 7}", "name", now)))
    assert len(t._hits) <= 300 + throttle._SWEEP_EVERY and len(throttle._known) <= 300 + throttle._SWEEP_EVERY
    throttle.reset_all()


def test_old_failure_and_known_entries_are_evicted(monkeypatch):
    throttle.reset_all()
    long_ago = sessions.now_utc() - timedelta(days=40)
    for i in range(50):
        throttle.note_failure(f"192.0.2.{i}", "x", long_ago)
        throttle.note_success(f"192.0.2.{i}", "y", long_ago)
    for i in range(throttle._SWEEP_EVERY):
        throttle.note_failure("198.51.100.1", f"n{i}", sessions.now_utc())
    assert not any(k[0].startswith("192.0.2.") for k in throttle._failures)
    for i in range(throttle._SWEEP_EVERY):
        throttle.note_success("198.51.100.2", f"n{i}", sessions.now_utc())
    assert not any(k[0].startswith("192.0.2.") for k in throttle._known)
    throttle.reset_all()


# ---------------------------------------------------------------- 7 and 8. start-up warnings

def test_an_https_public_address_no_longer_warns_about_plain_http_at_start(monkeypatch, caplog):
    """The public address no longer forces the Secure cookie, so there is nothing to warn about for the LAN address."""
    caplog.set_level(logging.WARNING, logger="amide")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://amide.example.test")
    gate.install(FastAPI())
    assert "plain http" not in caplog.text and "always Secure" not in caplog.text


def _request(host):
    return SimpleNamespace(client=SimpleNamespace(host=host))


def test_a_private_first_peer_behind_an_https_address_warns_once(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="amide")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://amide.example.test")
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    monkeypatch.setattr(gate, "_proxy_checked", False)
    gate._warn_if_behind_untrusted_proxy(_request("172.18.0.2"))
    assert "FORWARDED_ALLOW_IPS" in caplog.text
    caplog.clear()
    gate._warn_if_behind_untrusted_proxy(_request("172.18.0.2"))
    assert caplog.text == ""                                    # once per process


def test_no_proxy_warning_when_trusted_public_or_not_https(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="amide")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://amide.example.test")
    for host, env, url in (("172.18.0.2", "172.18.0.0/16", None), ("8.8.8.8", None, None), ("172.18.0.2", None, "")):
        monkeypatch.setattr(gate, "_proxy_checked", False)
        monkeypatch.setattr(config, "PUBLIC_URL", url if url is not None else "https://amide.example.test")
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
        if env:
            monkeypatch.setenv("FORWARDED_ALLOW_IPS", env)
        gate._warn_if_behind_untrusted_proxy(_request(host))
        assert "FORWARDED_ALLOW_IPS" not in caplog.text, (host, env, url)

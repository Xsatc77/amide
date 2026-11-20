"""A background loop that hits an error keeps going, and says so in the log (no message text from the error, no personal data)."""

import asyncio
import logging

import pytest

from app import reminders
from app.backup import scheduled
from app.ingest import process, worker


def _stop_after_one_round(monkeypatch, module):
    rounds = []

    async def fake_sleep(_seconds):
        rounds.append(1)
        if len(rounds) >= 2:
            raise asyncio.CancelledError          # the second sleep ends the loop; the first proves it kept going
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    return rounds


def test_the_ingest_worker_logs_a_failed_round_and_carries_on(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("secret-looking detail")
    monkeypatch.setattr(process, "process_due", boom)
    rounds = _stop_after_one_round(monkeypatch, worker)
    with caplog.at_level(logging.ERROR, logger="app.ingest.worker"), pytest.raises(asyncio.CancelledError):
        asyncio.run(worker._loop())
    assert len(rounds) == 2
    assert [r.getMessage() for r in caplog.records] == ["ingest worker round failed"] * 2


def test_the_reminder_loop_logs_a_failed_round(monkeypatch, caplog):
    monkeypatch.setattr(reminders, "run_once", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    _stop_after_one_round(monkeypatch, reminders)
    with caplog.at_level(logging.ERROR, logger="app.reminders"), pytest.raises(asyncio.CancelledError):
        asyncio.run(reminders._loop())
    assert any(r.getMessage() == "reminder round failed" for r in caplog.records)


def test_the_scheduled_backup_loop_logs_a_failed_round(monkeypatch, caplog):
    monkeypatch.setattr(scheduled, "run_if_due", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    _stop_after_one_round(monkeypatch, scheduled)
    with caplog.at_level(logging.ERROR, logger="app.backup.scheduled"), pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduled._loop())
    assert any(r.getMessage() == "scheduled backup failed" for r in caplog.records)

"""Scheduled automatic backups: an encrypted whole-installation file in data/backups every few days, with the passphrase kept in the environment."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.backup import container
from app.backup.archive import read_archive
from app.backup.restore import backups_dir
from app.backup.scheduled import run_if_due
from app.db import SessionLocal

PASSPHRASE = "a-long-test-passphrase"
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _setup(client, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE", PASSPHRASE)
    monkeypatch.setattr(config, "BACKUP_EVERY_DAYS", 7)
    monkeypatch.setattr(config, "BACKUP_KEEP", 3)
    folder = backups_dir()
    for f in folder.glob("amide-auto-*.amidebackup"):
        f.unlink()
    yield
    for f in folder.glob("amide-auto-*.amidebackup"):
        f.unlink()


def autos():
    return sorted(backups_dir().glob("amide-auto-*.amidebackup"))


def test_the_first_run_writes_an_encrypted_backup_that_opens_with_the_passphrase(client, db):
    path = run_if_due(SessionLocal, NOW)
    assert path is not None and autos() == [path]
    blob = path.read_bytes()
    assert PASSPHRASE.encode() not in blob and b"PK\x03\x04" not in blob[:8]                # sealed, not a plain zip
    archive = read_archive(container.unseal(blob, PASSPHRASE), max_bytes=config.MAX_BACKUP_BYTES)
    assert archive.manifest["kind"] == "backup"
    with pytest.raises(Exception):
        container.unseal(blob, "wrong passphrase!!")


def test_nothing_new_is_written_until_the_interval_has_passed(client, db):
    first = run_if_due(SessionLocal, NOW)
    assert run_if_due(SessionLocal, NOW + timedelta(days=3)) is None and autos() == [first]
    stamp = time.time() - 8 * 86400
    os.utime(first, (stamp, stamp))                                                        # the file is now eight days old
    assert run_if_due(SessionLocal, datetime.now(timezone.utc)) is not None and len(autos()) == 2


def test_only_the_newest_few_are_kept(client, db):
    for n in range(5):
        for f in autos():
            old = time.time() - (9 + n) * 86400
            os.utime(f, (old, old))
        run_if_due(SessionLocal, datetime.now(timezone.utc))
    assert len(autos()) == 3


def test_without_a_passphrase_or_with_a_short_one_nothing_happens(client, db, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE", "")
    assert run_if_due(SessionLocal, NOW) is None
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE", "short")
    assert run_if_due(SessionLocal, NOW) is None and autos() == []


def test_settings_says_whether_automatic_backups_are_on(client, db, monkeypatch):
    assert "Automatic backups are on" in client.get("/settings").text
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE", "")
    assert "Automatic backups are off" in client.get("/settings").text

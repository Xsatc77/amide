"""An update keeps a consistent copy of the database first, so it can be undone."""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app import config, migrate


def _cfg(url):
    cfg = Config(str(migrate.ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(migrate.ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def _url(folder):
    return f"sqlite:///{(folder / 'amide.db').as_posix()}"


def _revision(path):
    return sqlite3.connect(str(path)).execute("select version_num from alembic_version").fetchone()[0]


def test_an_old_database_is_copied_before_it_is_updated(folder):
    url = _url(folder)
    command.upgrade(_cfg(url), "0100")
    migrate.upgrade_db(url)
    copies = list((folder / "backups").glob("pre-upgrade-0100-to-*.db"))
    assert len(copies) == 1
    assert _revision(copies[0]) == "0100"                           # the copy is the OLD database, untouched by the update
    assert _revision(folder / "amide.db") != "0100"                 # while the real one moved on


def test_a_new_install_and_an_up_to_date_database_get_no_copy(folder):
    url = _url(folder)
    migrate.upgrade_db(url)                                          # no database yet
    migrate.upgrade_db(url)                                          # nothing left to migrate
    assert not (folder / "backups").exists() or not list((folder / "backups").glob("pre-upgrade-*.db"))


def test_only_the_newest_few_copies_are_kept(folder):
    url = _url(folder)
    backups = folder / "backups"
    backups.mkdir()
    import os
    for i in range(5):
        f = backups / f"pre-upgrade-0001-to-0002-2026010{i}-000000.db"
        f.write_bytes(b"x")
        os.utime(f, (1000 + i, 1000 + i))
    command.upgrade(_cfg(url), "0100")
    migrate.upgrade_db(url)
    names = sorted(p.name for p in backups.glob("pre-upgrade-*.db"))
    assert len(names) == migrate.SNAPSHOT_KEEP and any("0100-to-" in n for n in names)


def test_a_failed_copy_does_not_stop_the_update(folder, monkeypatch):
    url = _url(folder)
    command.upgrade(_cfg(url), "0100")
    monkeypatch.setattr(migrate.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
    assert migrate.snapshot_before_upgrade(_cfg(url), url) is None
    monkeypatch.undo()
    migrate.upgrade_db(url)                                          # and the real update still runs
    assert _revision(folder / "amide.db") != "0100"


def test_other_databases_are_left_alone(folder):
    assert migrate.snapshot_before_upgrade(_cfg("postgresql://u:p@h/db"), "postgresql://u:p@h/db") is None

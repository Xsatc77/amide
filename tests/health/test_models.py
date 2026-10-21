"""The Apple Health tables: created by the migration, keyed so a re-sent reading cannot double up."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from app.models import HealthReading

ROOT = Path(__file__).resolve().parent.parent.parent


def test_upgrade_creates_the_health_tables_and_scope_column(tmp_path):
    db = tmp_path / "m.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0076")
    with sqlite3.connect(db) as c:
        c.execute("insert into users (id, username, username_key, password_hash, is_admin, totp_enabled, failed_attempts, created_at) values (1, 'A', 'a', 'x', 1, 0, 0, '2026-01-01')")
        c.execute("insert into ingest_tokens (owner_id, label, prefix, token_hash, created_at) values (1, 'old', 'p', 'h', '2026-01-01')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type = 'table'")}
        assert {"health_raw", "health_readings", "health_events", "health_metric_choices"} <= tables
        assert "headers" in {r[1] for r in c.execute("pragma table_info(health_raw)")}
        assert "scope" in {r[1] for r in c.execute("pragma table_info(ingest_tokens)")}
        assert c.execute("select scope from ingest_tokens").fetchone()[0] == "watcher"
        assert [r[3] for r in c.execute("pragma table_info(health_metric_choices)") if r[1] == "decided_at"] == [0]      # 0078: pending rows have none
        c.execute("insert into health_raw (owner_id, received_at, sha256, size_bytes, file_name, status, readings_count) "
                  "values (1, '2026-01-01', 's', 1, '', 'stored', 0)")
        assert c.execute("select attempts from health_raw").fetchone()[0] == 0      # 0079: the read counter starts at nought
    command.downgrade(cfg, "0078")
    with sqlite3.connect(db) as c:
        assert "attempts" not in {r[1] for r in c.execute("pragma table_info(health_raw)")}
        c.execute("delete from health_raw")
    with sqlite3.connect(db) as c:
        c.execute("insert into ingest_tokens (owner_id, label, prefix, token_hash, created_at, scope) values (1, 'phone', 'q', 'h2', '2026-01-01', 'health')")
        c.execute("insert into health_metric_choices (owner_id, metric, enabled, decided_at) values (1, 'step_count', 0, null)")      # pending
        c.execute("insert into health_metric_choices (owner_id, metric, enabled, decided_at) values (1, 'heart_rate', 1, '2026-01-01')")
    command.downgrade(cfg, "0077")
    with sqlite3.connect(db) as c:
        assert [r[0] for r in c.execute("select metric from health_metric_choices")] == ["heart_rate"]      # pending rows were not decisions
        assert [r[3] for r in c.execute("pragma table_info(health_metric_choices)") if r[1] == "decided_at"] == [1]   # not null again
    command.downgrade(cfg, "0076")
    with sqlite3.connect(db) as c:
        assert [r[0] for r in c.execute("select label from ingest_tokens")] == ["old"]      # a health token must not turn into a watcher token
        tables = {r[0] for r in c.execute("select name from sqlite_master where type = 'table'")}
        assert not {"health_raw", "health_readings", "health_events", "health_metric_choices"} & tables
        assert "scope" not in {r[1] for r in c.execute("pragma table_info(ingest_tokens)")}


def test_reading_key_is_unique(db, me):
    def reading():
        return HealthReading(owner_id=me, metric="heart_rate", start_at=datetime(2026, 1, 1, 8), end_at=datetime(2026, 1, 1, 8),
                             grain="minute", value=60.0, unit="bpm", source="Acme Watch")
    db.add(reading())
    db.commit()
    db.add(reading())
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.query(HealthReading).delete()
    db.commit()

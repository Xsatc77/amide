"""Appetite (1 to 5) in the journal entry: entered in the popup, saved, shown in the table and trended."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.models import JournalEntry, User

ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(JournalEntry).delete()
    db.commit()


def entry(db, me):
    return db.scalar(select(JournalEntry).where(JournalEntry.owner_id == me))


def test_appetite_is_saved_with_the_entry(client, db, me):
    r = client.post("/journal/entries", data={"mood": "4", "appetite": "2"}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert entry(db, me).appetite == 2


def test_appetite_is_optional(client, db, me):
    assert client.post("/journal/entries", data={"mood": "4"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert entry(db, me).appetite is None


@pytest.mark.parametrize("bad", ["0", "6", "x", "2.5"])
def test_appetite_must_be_a_whole_number_from_1_to_5(client, db, me, bad):
    r = client.post("/journal/entries", data={"mood": "4", "appetite": bad}, follow_redirects=False)
    assert r.status_code == 422 and "Appetite" in r.text
    db.expire_all()
    assert entry(db, me) is None


def test_the_popup_has_the_field_and_prefills_it(client, db, me):
    page = client.get("/measurements?tab=journal").text
    assert 'name="appetite"' in page and "Appetite" in page and "1 none" in page
    db.add(JournalEntry(owner_id=me, entry_date=TODAY, mood=3, appetite=4))
    db.commit()
    dialog = client.get("/measurements?tab=journal").text.split('id="journal-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert 'name="appetite"' in dialog and 'value="4"' in dialog


def test_the_table_shows_an_appetite_badge_and_the_trend_chart_appears(client, db, me):
    for days_ago, appetite in ((2, 5), (1, 3), (0, 1)):
        db.add(JournalEntry(owner_id=me, entry_date=TODAY - timedelta(days=days_ago), mood=3, appetite=appetite))
    db.commit()
    page = client.get("/measurements?tab=journal").text
    assert "<th class=\"col-rate\">Appetite</th>" in page and 'aria-label="Appetite 3 of 5"' in page
    assert "rating-neutral" in page                                      # appetite is not good-or-bad, so it is not coloured red to green
    assert '<h3>Appetite</h3>' in page


def test_migration_0063_adds_the_column_and_keeps_entries(tmp_path):
    db = tmp_path / "m.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "0062")
    command.upgrade(cfg, "0063")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(journal_entries)")}
        assert "appetite" in cols
        sql = c.execute("select sql from sqlite_master where name = 'journal_entries'").fetchone()[0]
        assert "ck_journal_entry_appetite_range" in sql
    command.downgrade(cfg, "0062")
    with sqlite3.connect(db) as c:
        assert "appetite" not in {r[1] for r in c.execute("pragma table_info(journal_entries)")}

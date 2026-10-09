"""Migration 0058: old-style peptide names become the new-style names without losing anything a person entered."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def cfg(db: Path) -> Config:
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option("script_location", str(ROOT / "migrations"))
    c.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    c.attributes["configure_logger"] = False
    return c


def pid(c, name):
    return c.execute("select id from peptides where name = ? collate nocase", (name,)).fetchone()[0]


def setup(tmp_path):
    db = tmp_path / "m.db"
    command.upgrade(cfg(db), "0057")
    return db


def test_old_only_is_renamed_keeping_its_id_and_references(tmp_path):
    db = setup(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("delete from goal_peptides where peptide_id = (select id from peptides where name = 'Epithalon')")
        c.execute("delete from peptides where name = 'Epithalon'")
        c.execute("insert into peptides (name, source) values ('Epitalon', 'card')")
        old = pid(c, "Epitalon")
        c.execute("insert into goal_peptides (goal, peptide_id, position) values ('test-goal', ?, 1)", (old,))
    command.upgrade(cfg(db), "0058")
    with sqlite3.connect(db) as c:
        assert pid(c, "Epithalon") == old and c.execute("select count(*) from peptides where name = 'Epitalon'").fetchone()[0] == 0
        assert c.execute("select peptide_id from goal_peptides where goal = 'test-goal'").fetchone()[0] == old


def test_old_and_new_both_present_moves_references_and_removes_the_empty_old_one(tmp_path):
    db = setup(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("insert into peptides (name, source) values ('Octreotide', 'card')")
        old, new = pid(c, "Octreotide"), pid(c, "Octreotide (Sandostatin)")
        c.execute("insert into goal_peptides (goal, peptide_id, position) values ('only-old', ?, 1)", (old,))
        c.execute("insert into goal_peptides (goal, peptide_id, position) values ('both', ?, 1)", (old,))      # a clash: the new one is in this goal too
        c.execute("insert into goal_peptides (goal, peptide_id, position) values ('both', ?, 2)", (new,))
        c.execute("insert into protocols (name, start_date, paused, titration_enabled, created_at, updated_at) values ('P', '2026-10-01', 0, 0, '2026-10-01', '2026-10-01')")
        c.execute("insert into protocol_items (protocol_id, peptide_id, position, dose_unit, frequency, time_of_day, route) values (1, ?, 0, 'MG', 'DAILY', 'ANY', 'SUBQ')", (old,))
    command.upgrade(cfg(db), "0058")
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from peptides where name = 'Octreotide'").fetchone()[0] == 0
        assert c.execute("select peptide_id from goal_peptides where goal = 'only-old'").fetchone()[0] == new
        assert [r[0] for r in c.execute("select peptide_id from goal_peptides where goal = 'both'")] == [new]
        assert c.execute("select peptide_id from protocol_items").fetchone()[0] == new
        assert c.execute("pragma foreign_key_check").fetchall() == []


def test_an_old_entry_that_holds_card_data_is_left_alone(tmp_path):
    db = setup(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute("insert into peptides (name, source, summary) values ('Vasopressin', 'card', 'my own card text')")
    command.upgrade(cfg(db), "0058")
    with sqlite3.connect(db) as c:
        assert c.execute("select summary from peptides where name = 'Vasopressin'").fetchone()[0] == "my own card text"
        assert c.execute("select count(*) from peptides where name = 'Vasopressin (Vasostrict)'").fetchone()[0] == 1


def test_a_fresh_install_is_untouched_and_the_seed_has_no_old_names(tmp_path):
    db = tmp_path / "f.db"
    command.upgrade(cfg(db), "head")
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from peptides").fetchone()[0] == 105
        assert c.execute("select count(*) from peptides where name in ('Epitalon', 'Octreotide', 'Amylin')").fetchone()[0] == 0

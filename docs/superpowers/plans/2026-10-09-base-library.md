# Base library bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the 105 base peptide cards (aliases, tags, summaries, tiers, cycles, stack notes, monitoring, sections) with Amide, and bring existing installs to the same state without overwriting anything a person entered.

**Architecture:** `tools/export_base_library.py` builds `app/library/base_library.json` from the owner's library (no prices, no notes, no images). `app/library/base_library.py` fills missing or empty entries from it by reusing `load_sheets`; it runs at app start (idempotent, cheap when nothing is missing). Migration 0058 (raw SQL, names only) merges the 33 old-name pairs into the new-style names first.

**Tech Stack:** SQLAlchemy, Alembic, FastAPI lifespan, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-base-library-design.md`

## Global Constraints

- No `cost_estimate_text`, no `$` amount, no owner `notes`, no card images, no vendor or price-list data in `base_library.json` (repo rule).
- A peptide that already has card data, or that is custom, is never overwritten; `notes` is never touched.
- Rulings: (1) the fill runs at app start, not inside migration 0058, because a migration that uses the current ORM breaks on a fresh install once a later migration adds a column to `peptides`; (2) env `AMIDE_BASE_LIBRARY=0` turns it off (the test suite sets it so the shared test database keeps its bare seed; the loader is tested directly).
- Test command: `.venv/Scripts/python.exe -m pytest -q -p no:warnings`. Commit means commit and push, only when the owner says so.

## Review Focus

- Running the fill twice, or on an install that already has everything: no change, no error.
- An old-name entry that holds data when the new-style name also exists: left alone.
- A person's protocol or dose log pointing at an old-name entry: still points at the right peptide after the merge.
- Both old and new names holding references to the same protocol (unique key clash): no crash.
- Empty `STARTER` seed entries (KPV, NAD+, Glutathione, LL-37, Pinealon) are filled, but a custom entry of the same name is not.

---

### Task 1: Export tool and the bundled data

**Files:**
- Create: `tools/export_base_library.py`, `app/library/base_library.json` (generated), `tests/test_base_library_data.py`

- [ ] **Step 1: Write the failing test** (`tests/test_base_library_data.py`)

```python
"""The shipped base library: 105 names, full cards, and nothing that must not ship."""

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "app" / "library" / "base_library.json"
SEED = next(ROOT.glob("migrations/versions/0003_*.py"))


def seed_names():
    spec = importlib.util.spec_from_file_location("_seed_0003_data", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CARD_PEPTIDES + module.STARTER_PEPTIDES


def records():
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_it_has_exactly_the_base_names_each_with_a_full_card():
    recs = records()
    assert sorted(r["name"] for r in recs) == sorted(seed_names())
    for r in recs:
        assert r["summary"] and r["tags"] and r["sheet_sections_simple"], r["name"]


def test_nothing_that_must_not_ship_is_in_the_file():
    text = DATA.read_text(encoding="utf-8")
    assert not re.search(r"\$\s?\d", text)
    for r in records():
        assert not r.get("cost_estimate_text") and not r.get("notes") and not r.get("card_image")


def test_records_have_the_shape_load_sheets_reads():
    for r in records():
        assert isinstance(r["aliases"], list) and isinstance(r["dosing_tiers"], list) and isinstance(r["stack_relations"], list)
        assert isinstance(r["monitoring_tests"], list) and isinstance(r["sheet_sections"], dict) and "cycle" in r
```

- [ ] **Step 2:** Run it: expected FAIL (no data file).
- [ ] **Step 3: Write `tools/export_base_library.py`** — reads a database (argument, default `data/amide.db`), for each base name builds a `load_sheets` record from the `Peptide` row and children (aliases split on ", "; tiers as `{level, dose_text, frequency_text, time_of_day}`; cycle as `{on_weeks, off_weeks, note}` or null; relations as `{partner_name, relation, note}`; tests as `{test_name, when_text, why_text, target_text}`), sets `cost_estimate_text` to null, omits `notes` and `card_image`, refuses to write if any record lacks a summary or if any `$` amount remains, and writes `app/library/base_library.json` (UTF-8, indent 1, sorted by name). Then run it against the owner's database.
- [ ] **Step 4:** Run the data tests: expected PASS. Run the vendor scan, proven first on a planted name, over the JSON; any real vendor name stops the task.
- [ ] **Step 5: Commit (after "commit")** `git add tools/export_base_library.py app/library/base_library.json tests/test_base_library_data.py`

---

### Task 2: The loader

**Files:**
- Create: `app/library/base_library.py`, `tests/test_base_library.py`
- Modify: `app/main.py` (call it in `lifespan` after `load_starter`), `tests/conftest.py` (`AMIDE_BASE_LIBRARY=0`), `app/config.py` (`BASE_LIBRARY`)

**Interfaces:**
- Consumes: `app.library.loader.load_sheets(session, sheets, force_names)`
- Produces: `load_base_library(session, records=None) -> list[str]` (names filled or created), `is_empty_entry(peptide) -> bool`.

- [ ] **Step 1: Write the failing tests** — using small made-up records (never real vendor data) and the real `Peptide` model: a missing peptide is created with its tiers and aliases; an empty seed `CARD` entry is filled (same id, so protocols keep pointing at it); an empty `STARTER` entry is filled; an entry with a summary is left exactly as it is; a `CUSTOM` entry of the same name is untouched; `notes` survives; a second run returns `[]`; and `is_empty_entry` is False when any of summary, tags, card_class, card_details or sheet_sections is set.
- [ ] **Step 2:** Run: expected FAIL (module missing).
- [ ] **Step 3: Implement**

```python
"""The base library that ships with Amide: fills a missing or empty entry from base_library.json, never one that has card data."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.loader import load_sheets
from app.models import Peptide, PeptideSource

DATA = Path(__file__).with_name("base_library.json")


def is_empty_entry(peptide: Peptide) -> bool:
    return not (peptide.summary or peptide.tags or peptide.card_class or peptide.card_details or peptide.sheet_sections)


def load_base_library(session: Session, records: list[dict] | None = None) -> list[str]:
    """Create or fill what is missing; return the names touched. An entry that already has data, and anything custom, is left alone."""
    existing = {p.name.lower(): p for p in session.scalars(select(Peptide))}
    wanted = []
    for record in records if records is not None else json.loads(DATA.read_text(encoding="utf-8")):
        peptide = existing.get(record["name"].lower())
        if peptide is None or (peptide.source in (PeptideSource.CARD, PeptideSource.STARTER) and is_empty_entry(peptide)):
            wanted.append(record)
    if wanted:
        load_sheets(session, wanted, force_names={r["name"] for r in wanted})
    return [r["name"] for r in wanted]
```

  In `app/main.py` lifespan after `load_starter(session)`: `if config.BASE_LIBRARY: load_base_library(session)`. In `app/config.py`: `BASE_LIBRARY = os.environ.get("AMIDE_BASE_LIBRARY", "1") != "0"`. In `tests/conftest.py` set `os.environ["AMIDE_BASE_LIBRARY"] = "0"` beside the other test settings.
- [ ] **Step 4:** Run the new tests and the full suite: expected PASS.
- [ ] **Step 5: Commit (after "commit")**

---

### Task 3: Migration 0058 (old-name pairs) and a fresh-install check

**Files:**
- Create: `migrations/versions/0058_new_style_names.py`, `tests/test_migration_0058.py`

- [ ] **Step 1: Write the failing tests** — build a throwaway SQLite database at revision 0057 (use `alembic` programmatically against a temp URL, as the repo's migration tests do), insert old-name empty entries with references (a protocol item, a dose log, a goal stack row, a price-list row), upgrade to 0058, and assert: old-only entry is renamed with its id and references intact; old+new both present with the old empty → references moved to the new one, old deleted; a clash on a unique key (a goal stack already holding both) does not crash and leaves one row; an old entry with a summary is left alone; running the migration's logic twice changes nothing.
- [ ] **Step 2:** Run: expected FAIL (no revision 0058).
- [ ] **Step 3: Write the migration** with the 33-name `OLD_TO_NEW` mapping (the same mapping the seed rename used), raw SQL only: look up ids by `name = ? COLLATE NOCASE`; if old only → `UPDATE peptides SET name = :new`; if both and old is empty (`summary IS NULL AND tags IS NULL AND card_details IS NULL AND card_class IS NULL AND sheet_sections IS NULL`) → for each table that references `peptides` (discovered through `pragma_foreign_key_list`) run `UPDATE OR IGNORE <t> SET peptide_id = :new WHERE peptide_id = :old`, then `DELETE FROM <t> WHERE peptide_id = :old` for what could not move, then delete the old peptide. `downgrade()` is a no-op.
- [ ] **Step 4:** Run the migration tests and the full suite (a fresh test database walks the whole chain, so this also proves 0058 is harmless on a fresh install).
- [ ] **Step 5: Commit (after "commit")**

---

### Task 4: Docs

- [ ] Update `README.md` (a new install comes with the base library), `docs/USER_GUIDE.md` (Library section), `docs/ROADMAP.md`, `CHANGELOG.md`, and `docs/DEPLOYING.md` (updating an existing install: re-pull the image; duplicates merge and empty cards fill on start). Run the full suite. Commit after "commit".

## Self-review

- **Spec coverage:** shipped data without prices or notes (Task 1), fill-not-overwrite loader (Task 2), old-name merge and renames (Task 3), update path for existing installs and docs (Task 4).
- **Review Focus coverage:** idempotence and "data wins" (Task 2 tests), old+new both present and reference moves incl. a unique-key clash (Task 3 tests), starter/custom handling (Task 2 tests).
- **Placeholders:** none; Task 1 step 3 and Task 3 step 3 describe their code in full prose because their exact content depends on the owner's data (the export) and the 33-name mapping already fixed in the seed rename.

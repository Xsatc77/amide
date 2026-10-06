# Workout Calories, TDEE and Progress Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Estimate the calories a logged workout burns from the owner's exercise workbook, keep exercise history per exercise across plan changes, add a full TDEE breakdown, and derive colored charts (daily burn against TDEE, progression, weekly volume by body area, burn by equipment) from the logged data.

**Architecture:** Pure modules do the work (exercise data and lookup, calorie engine, fuzzy matcher, TDEE report, chart geometry, progress aggregation) and are proven by tests before anything touches the database or UI. Logged exercises store a full snapshot of what their estimate used, and the plan-row foreign keys become `ON DELETE SET NULL`, so history never depends on a plan. Routes build rows from snapshots and templates draw server-rendered inline SVG.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic (SQLite, batch mode), Jinja2, vanilla JS, pytest. Standard library only for reading the workbook (an xlsx is a zip of XML). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-06-workout-calories-tdee-design.md` (read it first; it records the owner's decisions and the workbook and site behaviours this plan reproduces).

## Global Constraints

- Run tests with `.venv/Scripts/python.exe -m pytest -q -p no:warnings`. The suite is the project's definition of green; it must pass in full after every task.
- Commit messages end with the trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Work directly on `main`; do not push (the owner says "Push").
- **Vendors and price lists are never released in the repo** (the owner's legal rule): nothing in this plan involves them, and no tracked file, test, doc, comment or commit message may contain real vendor names or price-list data. Gym exercise names are fine.
- The workbook path is passed to the generator as an argument; no personal path is written into any tracked file.
- Nothing is stored in browser storage (no localStorage/sessionStorage/IndexedDB); charts are server-rendered inline SVG; the only client script state is the DOM.
- No new Python or JS dependencies. Do not copy text from tdeecalculator.org; reproduce its formulas and numbers only.
- Reps and sets are whole numbers entered exactly; a range ("8-12") is never accepted as a log value.
- Every calorie figure shown to a person is labeled as an estimate ("about", "estimated").
- Migration revisions are `0034` (workout history) and `0035` (life stage); Alembic must keep a single head and `upgrade`/`downgrade` must both work.
- Match the surrounding code: docstrings explain why, not what; templates use the existing `lib-table`, `side-box`, `chips`, `tag`, `btn` classes and the `price-chart` SVG conventions; new CSS goes at the end of `app/static/css/app.css`.

## Review Focus

Failure modes the spec implies that no single task's happy path exercises; each has a test in the named task.

1. **Plan edits must never delete logged history** (removing an exercise, removing a day, renaming): Task 4 tests that logs survive with their snapshots, and that a removed day's history is not replaced by logging a new day on the same date.
2. **Missing body weight, sets or reps** must save the log and say what is missing instead of failing or inventing a number: Task 5.
3. **Rep ranges and non-integers** ("8-12", "10.5", "0", "1000") are refused with a clear message and never reach the calculator; the existing log is kept on a bad post: Task 5.
4. **A renamed plan exercise must not keep a stale database match**, and a confirmed match survives unrelated edits: Task 7.
5. **Empty and extreme data in the charts** (no workouts, one day, one huge day, a TDEE lower than the target line, an exercise logged once): Tasks 10-12.
6. **Profile gaps** (no sex, birth date, height, activity level or weight; male profile with a life stage set): the Energy tab explains what is missing instead of calculating: Tasks 9 and 11.
7. **Another person's workouts never appear in your Journal** (the workout-only rows are scoped to the viewer's own logs): Task 8.

## File Structure

| File | Responsibility |
|---|---|
| `tools/compendium_speed_tables.json` | Compendium walking, hill-walking and running rows by speed or grade (source for the data file) |
| `tools/build_exercise_data.py` | Reads the owner's workbook (stdlib only) into `app/workouts/exercise_data.json` |
| `app/workouts/exercise_data.json` | Generated: 230 exercises, aliases, style profiles, Compendium rows, speed tables |
| `app/workouts/exercise_db.py` | Loads the data once; lookup, search, styles, speed tables |
| `app/workouts/calories.py` | Pure calorie engine (workbook formulas) |
| `app/workouts/exercise_match.py` | Pure fuzzy matcher: plan exercise name to database exercise |
| `app/workouts/logging.py` | Parses the log form, builds `WorkoutExerciseLog` snapshots, history helpers |
| `app/workouts/charts.py` | Pure geometry: stacked bars, ring |
| `app/workouts/progress.py` | Pure aggregation over `LoggedExercise` rows |
| `app/measurements/tdee.py` | Pure TDEE report, goal ladder, activity levels, macro grid, life stages |
| `app/routers/workouts.py` | Extended: log form/save with calories, plan-editor matching, history survival |
| `app/routers/workout_insights.py` | New: Energy and Progress tabs |
| `app/templates/workouts/` | `log.html` (extended), `edit.html` (match column), `_tabs.html`, `energy.html`, `progress.html` |
| `app/routers/journal.py`, `measurements/index.html` | Log Workout button and kcal on workout lines |
| `migrations/versions/0034_workout_history.py`, `0035_user_life_stage.py` | Schema |

---

### Task 1: Exercise data and loader

**Files:**
- Create: `tools/compendium_speed_tables.json`, `tools/build_exercise_data.py`, `app/workouts/exercise_db.py`, `app/workouts/exercise_data.json` (generated)
- Test: `tests/test_exercise_db.py`

**Interfaces:**
- Produces (used by Tasks 2-12): `exercise_db.Exercise` (frozen dataclass: `name, equipment, area, pattern, code, met, evidence, style, sec_per_rep, rest_min, model, note, speed_table`, property `is_duration`), `SpeedBand(code, min, met, label)`, `SpeedTable(key, basis, unit_label, rows)`, `StyleProfile(name, met, sec_per_rep, rest_min)`, and functions `all_exercises() -> tuple[Exercise, ...]`, `get(name) -> Exercise | None`, `aliases() -> dict[str, str]`, `canonical_for_alias(text) -> str | None`, `styles() -> dict[str, StyleProfile]`, `choosable_styles() -> list[str]`, `speed_tables() -> dict[str, SpeedTable]`, `search(text, limit=12) -> list[Exercise]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_exercise_db.py`:

```python
from app.workouts import exercise_db


def test_the_database_holds_the_workbooks_230_exercises():
    exercises = exercise_db.all_exercises()
    assert len(exercises) == 230
    assert len({e.name.casefold() for e in exercises}) == 230


def test_every_alias_points_at_a_real_exercise():
    assert len(exercise_db.aliases()) >= 280
    assert all(exercise_db.get(target) for target in exercise_db.aliases().values())


def test_lookup_ignores_case_and_surrounding_space():
    assert exercise_db.get("  bench press ").name == "Bench Press"
    assert exercise_db.get("not an exercise") is None
    assert exercise_db.get(None) is None


def test_rows_carry_what_the_estimator_needs():
    bench = exercise_db.get("Bench Press")
    assert (bench.equipment, bench.area, bench.style, bench.sec_per_rep, bench.rest_min) == (
        "Barbell", "Chest", "Heavy Strength", 4.5, 3.0)
    assert bench.model == "rep" and not bench.is_duration and bench.evidence == "Mapped estimate"
    assert exercise_db.get("Plank").is_duration and exercise_db.get("Elliptical").evidence == "Direct Compendium activity"


def test_the_style_table_is_the_workbooks():
    styles = exercise_db.styles()
    assert {n: s.met for n, s in styles.items() if n in exercise_db.choosable_styles()} == {
        "Hypertrophy / General": 3.5, "Heavy Strength": 5.0, "Isolation": 3.5, "Explosive / Power": 6.0,
        "Circuit / Superset": 5.8, "Bodyweight": 3.0, "Kettlebell / Conditioning": 7.5}
    assert styles["General"].met == 3.5   # the label the workbook gives its cardio rows


def test_walking_and_running_use_the_compendium_tables_not_a_flat_3_5():
    assert {e.name: e.speed_table for e in exercise_db.all_exercises() if e.speed_table} == {
        "Treadmill Walk": "treadmill_walk", "Treadmill Incline Walk": "hill_walk",
        "Treadmill Jog": "run", "Treadmill Run": "run"}
    tables = exercise_db.speed_tables()
    assert tables["treadmill_walk"].basis == "speed_mph" and tables["hill_walk"].basis == "grade_pct"
    assert tables["run"].rows[0].code == "12026" and tables["run"].rows[-1].met == 23.0
    for table in tables.values():
        mins = [r.min for r in table.rows]
        assert mins == sorted(mins) and len(set(mins)) == len(mins)


def test_search_finds_names_and_aliases_shortest_first():
    names = [e.name for e in exercise_db.search("incline press")]
    assert "Dumbbell Incline Press" in names
    assert [e.name for e in exercise_db.search("plank")][0] == "Plank"
    assert exercise_db.search("") == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exercise_db.py -q -p no:warnings`
Expected: FAIL (collection error: `ImportError: cannot import name 'exercise_db'`).

- [ ] **Step 3: Create the Compendium speed tables source**

Create `tools/compendium_speed_tables.json` (values from the 2024 Adult Compendium of Physical Activities, pacompendium.com: treadmill walking 17340-17367, hill climbing 17034-17036, running 12026-12135; each row applies from its `min` up to the next row's):

```json
{
 "treadmill_walk": {
  "basis": "speed_mph", "unit_label": "mph",
  "note": "Compendium walking, treadmill, 0% grade. Each row applies from its minimum speed up to the next row's.",
  "rows": [
   {"code": "17340", "min": 0.0, "met": 2.1, "label": "Walking, treadmill, under 1.0 mph"},
   {"code": "17343", "min": 1.0, "met": 2.3, "label": "Walking, treadmill, 1.0 mph"},
   {"code": "17346", "min": 1.2, "met": 2.8, "label": "Walking, treadmill, 1.2 to 1.9 mph"},
   {"code": "17349", "min": 2.0, "met": 3.0, "label": "Walking, treadmill, 2.0 to 2.4 mph"},
   {"code": "17352", "min": 2.5, "met": 3.5, "label": "Walking, treadmill, 2.5 to 2.9 mph"},
   {"code": "17355", "min": 3.0, "met": 3.8, "label": "Walking, treadmill, 3.0 to 3.4 mph"},
   {"code": "17358", "min": 3.5, "met": 4.8, "label": "Walking, treadmill, 3.5 to 3.9 mph"},
   {"code": "17361", "min": 4.0, "met": 5.8, "label": "Walking, treadmill, 4.0 to 4.4 mph"},
   {"code": "17364", "min": 4.5, "met": 6.8, "label": "Walking, treadmill, 4.5 to 4.9 mph"},
   {"code": "17367", "min": 5.0, "met": 8.3, "label": "Walking, treadmill, 5.0 to 5.5 mph"}
  ]
 },
 "hill_walk": {
  "basis": "grade_pct", "unit_label": "% grade",
  "note": "Compendium climbing hills, no load, moderate-to-brisk pace; used for incline walking by grade.",
  "rows": [
   {"code": "17034", "min": 1.0, "met": 5.3, "label": "Climbing hills, no load, 1 to 5% grade"},
   {"code": "17035", "min": 6.0, "met": 7.0, "label": "Climbing hills, no load, 6 to 10% grade"},
   {"code": "17036", "min": 11.0, "met": 8.8, "label": "Climbing hills, no load, 11 to 20% grade"}
  ]
 },
 "run": {
  "basis": "speed_mph", "unit_label": "mph",
  "note": "Compendium running and jogging by speed. Each row applies from its minimum speed up to the next row's.",
  "rows": [
   {"code": "12026", "min": 2.6, "met": 3.3, "label": "Jogging 2.6 to 3.7 mph"},
   {"code": "12028", "min": 4.0, "met": 6.5, "label": "Running, 4 to 4.2 mph (13 min/mile)"},
   {"code": "12029", "min": 4.3, "met": 7.8, "label": "Running 4.3 to 4.8 mph"},
   {"code": "12030", "min": 5.0, "met": 8.5, "label": "Running, 5.0 to 5.2 mph (12 min/mile)"},
   {"code": "12045", "min": 5.5, "met": 9.0, "label": "Running, 5.5 to 5.8 mph"},
   {"code": "12050", "min": 6.0, "met": 9.3, "label": "Running, 6 to 6.3 mph (10 min/mile)"},
   {"code": "12060", "min": 6.7, "met": 10.5, "label": "Running, 6.7 mph (9 min/mile)"},
   {"code": "12070", "min": 7.0, "met": 11.0, "label": "Running, 7 mph (8.5 min/mile)"},
   {"code": "12080", "min": 7.5, "met": 11.8, "label": "Running, 7.5 mph (8 min/mile)"},
   {"code": "12090", "min": 8.0, "met": 12.0, "label": "Running, 8 mph (7.5 min/mile)"},
   {"code": "12100", "min": 8.6, "met": 12.5, "label": "Running, 8.6 mph (7 min/mile)"},
   {"code": "12110", "min": 9.0, "met": 13.0, "label": "Running, 9 mph (6.5 min/mile)"},
   {"code": "12115", "min": 9.3, "met": 14.8, "label": "Running, 9.3 to 9.6 mph"},
   {"code": "12120", "min": 10.0, "met": 14.8, "label": "Running, 10 mph (6 min/mile)"},
   {"code": "12130", "min": 11.0, "met": 16.8, "label": "Running, 11 mph (5.5 min/mile)"},
   {"code": "12132", "min": 12.0, "met": 18.5, "label": "Running, 12 mph (5.0 min/mile)"},
   {"code": "12134", "min": 13.0, "met": 19.8, "label": "Running, 13 mph (4.6 min/mile)"},
   {"code": "12135", "min": 14.0, "met": 23.0, "label": "Running, 14 mph (4.3 min/mile)"}
  ]
 }
}
```

- [ ] **Step 4: Create the generator**

Create `tools/build_exercise_data.py`:

```python
"""Builds app/workouts/exercise_data.json from the owner's exercise workbook (an .xlsx file).

    python tools/build_exercise_data.py PATH_TO_WORKBOOK.xlsx

Reads the xlsx with the standard library only (an xlsx is a zip of XML). The Compendium speed tables for
walking and running come from tools/compendium_speed_tables.json, so building needs no network.
"""

import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "workouts" / "exercise_data.json"
SPEED_TABLES = Path(__file__).resolve().parent / "compendium_speed_tables.json"

# exercise name -> the speed table that gives its MET (the workbook's flat 3.5 for these is replaced)
SPEED_TABLE_FOR = {
    "Treadmill Walk": "treadmill_walk",
    "Treadmill Incline Walk": "hill_walk",
    "Treadmill Jog": "run",
    "Treadmill Run": "run",
}


def sheet_rows(book: zipfile.ZipFile, sheet_name: str) -> list[dict[str, str]]:
    """Every row of the named sheet as {column letter: text}. Handles inline strings (no shared-strings part)."""
    names = [s.get("name") for s in ET.fromstring(book.read("xl/workbook.xml")).find(_NS + "sheets")]
    index = names.index(sheet_name) + 1
    root = ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in root.find(_NS + "sheetData").findall(_NS + "row"):
        cells = {}
        for cell in row.findall(_NS + "c"):
            inline, value = cell.find(_NS + "is"), cell.find(_NS + "v")
            if inline is not None:
                text = "".join(t.text or "" for t in inline.iter(_NS + "t"))
            else:
                text = value.text if value is not None else None
            if text is not None:
                cells[cell.get("r").rstrip("0123456789")] = text
        rows.append(cells)
    return rows


def number(text: str | None) -> float | None:
    try:
        return round(float(text), 4) if text not in (None, "") else None
    except ValueError:
        return None


def build(workbook: Path) -> dict:
    with zipfile.ZipFile(workbook) as book:
        exercises = []
        for r in sheet_rows(book, "Exercise Database")[1:]:
            if not r.get("A"):
                continue
            name = r["A"].strip()
            exercises.append({
                "name": name, "equipment": r.get("B"), "area": r.get("C"), "pattern": r.get("D"),
                "code": r.get("F"), "met": number(r.get("G")), "evidence": r.get("H"), "style": r.get("J"),
                "sec_per_rep": number(r.get("K")), "rest_min": number(r.get("L")),
                "model": "duration" if r.get("M") == "Duration-based" else "rep",
                "note": r.get("O"), "speed_table": SPEED_TABLE_FOR.get(name),
            })
        known = {e["name"] for e in exercises}
        aliases = {}
        for r in sheet_rows(book, "Exercise Aliases")[1:]:
            alias, target = (r.get("A") or "").strip(), (r.get("B") or "").strip()
            if alias and target in known:
                aliases.setdefault(alias.casefold(), target)
        styles = {}
        for r in sheet_rows(book, "Estimator Assumptions"):
            if r.get("A") and number(r.get("B")) is not None:  # the style table is the rows with a numeric MET
                styles[r["A"].strip()] = {"met": number(r["B"]), "sec_per_rep": number(r.get("C")),
                                          "rest_min": number(r.get("D")), "use": r.get("F")}
        # the Exercise Database labels 14 cardio/conditioning rows "General": the workbook treats them as the default style
        styles.setdefault("General", dict(styles["Hypertrophy / General"]))
        compendium = [{"code": r["A"], "met": number(r["B"]), "description": r.get("C")}
                      for r in sheet_rows(book, "Compendium Source")[1:] if r.get("A")]
    missing = [n for n in SPEED_TABLE_FOR if n not in known]
    if missing:
        raise SystemExit(f"workbook has no row for: {', '.join(missing)}")
    return {
        "source": "2024 Adult Compendium of Physical Activities (pacompendium.com), via the owner's workbook",
        "exercises": exercises, "aliases": aliases, "styles": styles, "compendium": compendium,
        "speed_tables": json.loads(SPEED_TABLES.read_text(encoding="utf-8")),
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    data = build(Path(sys.argv[1]))
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(data['exercises'])} exercises, {len(data['aliases'])} aliases, {len(data['styles'])} styles -> {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Generate the data file from the owner's workbook**

The workbook is the xlsx the owner attached to the request ("Exercise_Gym_Calorie_Estimator_COMPLETE_Target.xlsx"); pass its path as the argument.

Run: `.venv/Scripts/python.exe tools/build_exercise_data.py "<path to the workbook>"`
Expected: `230 exercises, 286 aliases, 9 styles -> .../app/workouts/exercise_data.json`

- [ ] **Step 6: Create the loader**

Create `app/workouts/exercise_db.py`:

```python
"""The exercise database: the owner's workbook of 230 gym exercises (MET, default style, seconds per rep, rest),
its aliases, the style profiles and the Compendium speed tables, loaded once from exercise_data.json.

Regenerate that file from the workbook with tools/build_exercise_data.py."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).with_name("exercise_data.json")


@dataclass(frozen=True)
class Exercise:
    name: str
    equipment: str | None
    area: str | None
    pattern: str | None
    code: str | None
    met: float | None
    evidence: str | None
    style: str | None
    sec_per_rep: float | None
    rest_min: float | None
    model: str                # "rep" or "duration"
    note: str | None
    speed_table: str | None   # a key of speed_tables(): the Compendium rows that give this exercise its MET

    @property
    def is_duration(self) -> bool:
        return self.model == "duration"


@dataclass(frozen=True)
class SpeedBand:
    code: str
    min: float
    met: float
    label: str


@dataclass(frozen=True)
class SpeedTable:
    key: str
    basis: str                # "speed_mph" or "grade_pct"
    unit_label: str
    rows: tuple[SpeedBand, ...]


@dataclass(frozen=True)
class StyleProfile:
    name: str
    met: float
    sec_per_rep: float | None
    rest_min: float | None


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def all_exercises() -> tuple[Exercise, ...]:
    return tuple(Exercise(**row) for row in _raw()["exercises"])


@lru_cache(maxsize=1)
def _by_name() -> dict[str, Exercise]:
    return {e.name.casefold(): e for e in all_exercises()}


def get(name: str | None) -> Exercise | None:
    """The exercise with this canonical name (case-insensitive), or None."""
    return _by_name().get((name or "").strip().casefold())


@lru_cache(maxsize=1)
def aliases() -> dict[str, str]:
    """casefolded alias -> canonical exercise name."""
    return dict(_raw()["aliases"])


def canonical_for_alias(text: str | None) -> str | None:
    return aliases().get((text or "").strip().casefold())


@lru_cache(maxsize=1)
def styles() -> dict[str, StyleProfile]:
    return {name: StyleProfile(name, p["met"], p["sec_per_rep"], p["rest_min"]) for name, p in _raw()["styles"].items()}


def choosable_styles() -> list[str]:
    """The styles a person can pick on a rep-based row (not the rest-recovery assumption or the "General" label)."""
    return [name for name, s in styles().items() if s.sec_per_rep is not None and name != "General"]


@lru_cache(maxsize=1)
def speed_tables() -> dict[str, SpeedTable]:
    out = {}
    for key, table in _raw()["speed_tables"].items():
        rows = tuple(SpeedBand(r["code"], r["min"], r["met"], r["label"])
                     for r in sorted(table["rows"], key=lambda r: r["min"]))
        out[key] = SpeedTable(key, table["basis"], table["unit_label"], rows)
    return out


def search(text: str, limit: int = 12) -> list[Exercise]:
    """Exercises whose name or an alias contains every word of `text`, shortest names first."""
    words = (text or "").casefold().split()
    if not words:
        return []
    alias_hits = {canonical for alias, canonical in aliases().items() if all(w in alias for w in words)}
    hits = [e for e in all_exercises() if all(w in e.name.casefold() for w in words) or e.name in alias_hits]
    return sorted(hits, key=lambda e: (len(e.name), e.name))[:limit]
```

- [ ] **Step 7: Run the tests and the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exercise_db.py -q -p no:warnings` then the full suite.
Expected: 7 passed, then the full suite green.

- [ ] **Step 8: Commit**

```bash
git add tools/compendium_speed_tables.json tools/build_exercise_data.py app/workouts/exercise_data.json app/workouts/exercise_db.py tests/test_exercise_db.py
git commit -m "feat: exercise database from the workbook, with Compendium walking and running tables

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Calorie engine

**Files:**
- Create: `app/workouts/calories.py`
- Test: `tests/test_calories.py`

**Interfaces:**
- Consumes: `exercise_db.Exercise`, `exercise_db.styles()`, `exercise_db.speed_tables()`.
- Produces: `calories.Burn` (frozen: `met, active_min, rest_min, gross_kcal, net_kcal, volume_lb, code, band_label`), `calories.estimate(ex, *, body_weight_lb, sets=None, reps=None, load_lb=None, implements=1, style=None, minutes=None, speed_mph=None, grade_pct=None) -> (Burn | None, list[str])` where the list names what is missing, `calories.style_met(style) -> float`, `calories.band_for(table_key, value) -> SpeedBand`, `calories.kg_from_lb(lb)`, constants `LB_PER_KG`, `REST_MET`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calories.py` (hand-worked from the workbook's "Workout Log Estimator" sheet and the Compendium rows):

```python
import pytest

from app.workouts import calories, exercise_db
from app.workouts.calories import band_for, estimate

BW = 200.0
KG = BW / 2.2046226218


def per_min(met):
    return met * 3.5 * KG / 200


def test_a_rep_based_estimate_follows_the_workbook_sheet():
    # Bench Press: 4.5 s/rep, 3 min rest per set. 185 lb x 4 sets x 8 reps in the Hypertrophy / General style (MET 3.5).
    burn, missing = estimate(exercise_db.get("Bench Press"), body_weight_lb=BW, sets=4, reps=8, load_lb=185,
                             style="Hypertrophy / General")
    assert missing == []
    assert burn.active_min == pytest.approx(2.4) and burn.rest_min == pytest.approx(9.0)
    assert burn.met == 3.5
    assert burn.gross_kcal == pytest.approx(per_min(3.5) * 2.4 + per_min(1.5) * 9.0)      # 34.77
    assert burn.net_kcal == pytest.approx(per_min(2.5) * 2.4 + per_min(0.5) * 9.0)        # 16.67
    assert burn.volume_lb == 185 * 4 * 8


def test_the_style_chooses_the_met_and_defaults_to_the_exercises_own_style():
    bench = exercise_db.get("Bench Press")                                                 # default style Heavy Strength
    heavy, _ = estimate(bench, body_weight_lb=BW, sets=3, reps=5)
    light, _ = estimate(bench, body_weight_lb=BW, sets=3, reps=5, style="Isolation")
    assert heavy.met == 5.0 and light.met == 3.5 and heavy.gross_kcal > light.gross_kcal


def test_one_set_has_no_rest_and_two_implements_double_the_volume_only():
    curl = exercise_db.get("Dumbbell Curl")
    one, _ = estimate(curl, body_weight_lb=BW, sets=1, reps=10, load_lb=30)
    two, _ = estimate(curl, body_weight_lb=BW, sets=1, reps=10, load_lb=30, implements=2)
    assert one.rest_min == 0 and one.volume_lb == 300 and two.volume_lb == 600
    assert two.gross_kcal == pytest.approx(one.gross_kcal)


def test_missing_inputs_give_no_estimate_and_name_what_is_missing():
    bench = exercise_db.get("Bench Press")
    assert estimate(bench, body_weight_lb=BW, sets=3) == (None, ["reps per set"])
    assert estimate(bench, body_weight_lb=None, sets=3, reps=8)[1] == ["body weight"]
    assert estimate(bench, body_weight_lb=BW)[1] == ["sets", "reps per set"]
    assert estimate(bench, body_weight_lb=BW, sets=0, reps=8)[1] == ["sets"]
    assert estimate(bench, body_weight_lb=-5, sets=3, reps=8)[1] == ["body weight"]


def test_load_is_optional_because_it_only_feeds_volume():
    burn, missing = estimate(exercise_db.get("Push-Up"), body_weight_lb=BW, sets=3, reps=15)
    assert missing == [] and burn.volume_lb is None and burn.gross_kcal > 0


def test_a_duration_exercise_uses_minutes_and_its_own_met():
    burn, missing = estimate(exercise_db.get("Elliptical"), body_weight_lb=BW, minutes=30)
    assert missing == [] and burn.met == 5.0 and burn.rest_min == 0
    assert burn.gross_kcal == pytest.approx(per_min(5.0) * 30) and burn.net_kcal == pytest.approx(per_min(4.0) * 30)
    assert estimate(exercise_db.get("Plank"), body_weight_lb=BW)[1] == ["minutes"]


def test_running_picks_its_met_from_the_compendium_by_speed():
    run = exercise_db.get("Treadmill Run")
    burn, missing = estimate(run, body_weight_lb=180, minutes=30, speed_mph=6.0)
    assert missing == [] and burn.met == 9.3 and burn.code == "12050"
    assert burn.gross_kcal == pytest.approx(9.3 * 3.5 * (180 / 2.2046226218) / 200 * 30)
    assert estimate(run, body_weight_lb=180, minutes=30)[1] == ["speed"]


def test_walking_uses_the_treadmill_walking_rows():
    walk = exercise_db.get("Treadmill Walk")
    burn, _ = estimate(walk, body_weight_lb=180, minutes=20, speed_mph=4.5)
    assert burn.met == 6.8 and burn.code == "17364"
    assert burn.gross_kcal == pytest.approx(6.8 * 3.5 * (180 / 2.2046226218) / 200 * 20)


def test_incline_walking_is_chosen_by_grade_and_needs_at_least_one_percent():
    incline = exercise_db.get("Treadmill Incline Walk")
    assert estimate(incline, body_weight_lb=180, minutes=20, grade_pct=8)[0].met == 7.0
    assert estimate(incline, body_weight_lb=180, minutes=20, grade_pct=15)[0].code == "17036"
    assert estimate(incline, body_weight_lb=180, minutes=20, grade_pct=0.5)[1] == ["incline grade of at least 1%"]
    assert estimate(incline, body_weight_lb=180, minutes=20)[1] == ["incline grade"]


@pytest.mark.parametrize("table,value,met", [
    ("run", 2.0, 3.3), ("run", 4.1, 6.5), ("run", 4.8, 7.8), ("run", 5.3, 8.5), ("run", 6.5, 9.3), ("run", 9.5, 14.8),
    ("run", 20.0, 23.0), ("treadmill_walk", 0.5, 2.1), ("treadmill_walk", 1.1, 2.3), ("treadmill_walk", 2.5, 3.5),
    ("treadmill_walk", 3.49, 3.8), ("treadmill_walk", 5.5, 8.3), ("treadmill_walk", 7.0, 8.3)])
def test_a_speed_falls_in_the_band_that_contains_it(table, value, met):
    assert band_for(table, value).met == met
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calories.py -q -p no:warnings`
Expected: FAIL (`ModuleNotFoundError: app.workouts.calories`).

- [ ] **Step 3: Implement the engine**

Create `app/workouts/calories.py`:

```python
"""Calorie estimates for a logged exercise, following the owner's workbook ("Workout Log Estimator" sheet):

    active_min = sets * reps * seconds_per_rep / 60                (rep-based)
    rest_min   = max(sets - 1, 0) * rest_per_set_min
    gross      = MET * 3.5 * kg / 200 * active_min + 1.5 * 3.5 * kg / 200 * rest_min
    net        = (MET - 1) * 3.5 * kg / 200 * active_min + 0.5 * 3.5 * kg / 200 * rest_min
    volume_lb  = load * implements * reps * sets

A rep-based exercise takes its MET from its style (Heavy Strength 5, Hypertrophy 3.5, ...); a duration-based one
(plank, bike, treadmill) from its own MET, or from the Compendium speed table by speed or grade. Pure: numbers in,
numbers out; a missing or impossible input yields no estimate and a list of what is missing, never a guess."""

from dataclasses import dataclass

from app.workouts import exercise_db
from app.workouts.exercise_db import Exercise, SpeedBand

LB_PER_KG = 2.2046226218
REST_MET = 1.5


@dataclass(frozen=True)
class Burn:
    met: float
    active_min: float
    rest_min: float
    gross_kcal: float
    net_kcal: float
    volume_lb: float | None
    code: str | None = None          # the Compendium code that gave the MET, for speed-table rows
    band_label: str | None = None


def kg_from_lb(lb: float) -> float:
    return lb / LB_PER_KG


def style_met(style: str | None) -> float:
    profile = exercise_db.styles().get(style or "")
    return profile.met if profile else exercise_db.styles()["Hypertrophy / General"].met


def band_for(table_key: str, value: float) -> SpeedBand:
    """The band whose minimum is the highest one at or below `value`; below every band uses the lowest."""
    rows = exercise_db.speed_tables()[table_key].rows
    chosen = rows[0]
    for row in rows:
        if row.min <= value:
            chosen = row
    return chosen


def _kcal_per_min(met: float, kg: float) -> float:
    return met * 3.5 * kg / 200


def _positive(value) -> bool:
    return value is not None and value > 0


def estimate(ex: Exercise, *, body_weight_lb: float | None, sets: int | None = None, reps: int | None = None,
             load_lb: float | None = None, implements: int = 1, style: str | None = None,
             minutes: float | None = None, speed_mph: float | None = None,
             grade_pct: float | None = None) -> tuple[Burn | None, list[str]]:
    """(Burn, []) when everything needed is present, else (None, [names of what is missing])."""
    missing = []
    if not _positive(body_weight_lb):
        missing.append("body weight")
    if ex.is_duration:
        if not _positive(minutes):
            missing.append("minutes")
        met, band = ex.met, None
        if ex.speed_table:
            table = exercise_db.speed_tables()[ex.speed_table]
            value = speed_mph if table.basis == "speed_mph" else grade_pct
            if not _positive(value):
                missing.append("speed" if table.basis == "speed_mph" else "incline grade")
            elif table.basis == "grade_pct" and value < table.rows[0].min:
                missing.append(f"incline grade of at least {table.rows[0].min:g}%")
            else:
                band = band_for(ex.speed_table, value)
                met = band.met
        if missing or met is None:
            return None, missing
        kg = kg_from_lb(body_weight_lb)
        gross = _kcal_per_min(met, kg) * minutes
        net = _kcal_per_min(met - 1, kg) * minutes
        return Burn(met, minutes, 0.0, gross, net, None, band.code if band else ex.code,
                    band.label if band else None), []
    for label, value in (("sets", sets), ("reps per set", reps)):
        if not _positive(value):
            missing.append(label)
    if ex.sec_per_rep is None:
        missing.append("seconds per rep")
    if missing:
        return None, missing
    met = style_met(style or ex.style)
    kg = kg_from_lb(body_weight_lb)
    active = sets * reps * ex.sec_per_rep / 60
    rest = max(sets - 1, 0) * (ex.rest_min or 0.0)
    gross = _kcal_per_min(met, kg) * active + _kcal_per_min(REST_MET, kg) * rest
    net = _kcal_per_min(met - 1, kg) * active + _kcal_per_min(REST_MET - 1, kg) * rest
    volume = load_lb * max(implements, 1) * reps * sets if _positive(load_lb) else None
    return Burn(met, active, rest, gross, net, volume), []
```

- [ ] **Step 4: Run the tests and the whole suite**

Expected: 22 passed in the file (including 13 parametrized speed bands), full suite green.

- [ ] **Step 5: Commit**

```bash
git add app/workouts/calories.py tests/test_calories.py
git commit -m "feat: calorie engine following the workbook's logging sheet

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Fuzzy exercise matcher

**Files:**
- Create: `app/workouts/exercise_match.py`
- Test: `tests/test_exercise_match.py`

**Interfaces:**
- Consumes: `exercise_db.get`, `canonical_for_alias`, `all_exercises`, `aliases`, `Exercise`.
- Produces: `exercise_match.ExerciseMatch(exercise: Exercise | None, how: str, score: float, suggestions: tuple[Exercise, ...])` with property `confident`, and `exercise_match.match_exercise(name) -> ExerciseMatch`. `how` is one of `exact | alias | normalized | fuzzy | none`; `suggestions` holds up to three candidates when nothing was accepted.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_exercise_match.py`:

```python
import pytest

from app.workouts.exercise_match import match_exercise


@pytest.mark.parametrize("typed,how,name", [
    ("Bench Press", "exact", "Bench Press"),
    ("bench press", "exact", "Bench Press"),
    ("DB Incline Press", "alias", "Dumbbell Incline Press"),
    ("Incline DB Press", "normalized", "Dumbbell Incline Press"),
    ("Barbell Bench Press", "normalized", "Bench Press"),
    ("Back Squats", "normalized", "Back Squat"),
    ("Pushups", "normalized", "Push-Up"),
    ("Pull-ups", "normalized", "Pull-Up"),
    ("RDL", "normalized", "Romanian Deadlift"),
    ("Walking Lunges", "normalized", "Walking Lunge"),
    ("Machine Chest Press", "normalized", "Chest Press Machine"),
    ("Running", "alias", "Treadmill Run"),
    ("Military Press", "alias", "Overhead Press"),
    ("Leg Extension", "fuzzy", "Leg Extension Machine"),
])
def test_names_people_actually_write_land_on_the_right_exercise(typed, how, name):
    match = match_exercise(typed)
    assert (match.how, match.exercise.name) == (how, name)
    assert match.confident


def test_candidates_that_burn_the_same_are_interchangeable():
    # Cable and machine lat pulldowns share one calorie profile, so "Lat Pulldown" is accepted.
    assert match_exercise("Lat Pulldown").exercise.name in {"Cable Lat Pulldown", "Lat Pulldown Machine"}


@pytest.mark.parametrize("typed", ["Squats", "Lunges", "Treadmill", "Calf Raises", "Bicep Curl"])
def test_a_generic_name_with_different_candidates_is_a_suggestion_not_a_guess(typed):
    match = match_exercise(typed)
    assert not match.confident and match.exercise is None
    assert 1 <= len(match.suggestions) <= 3


@pytest.mark.parametrize("typed", ["", "   ", None, "Zzz Quasar Lift", "Crunches Of The Gods 9000"])
def test_nonsense_matches_nothing_and_suggests_nothing(typed):
    match = match_exercise(typed)
    assert match.exercise is None and match.suggestions == () and match.how == "none"


def test_equipment_words_keep_different_exercises_apart():
    assert match_exercise("Cable Curl").exercise.name == "Cable Curl"
    assert match_exercise("Barbell Biceps Curl").exercise.name == "Barbell Curl"
    assert match_exercise("Dumbbell Bench Press").exercise.name == "Dumbbell Bench Press"


def test_the_match_is_stable_and_cheap_to_repeat():
    first = match_exercise("Hammer Curls")
    assert all(match_exercise("Hammer Curls") == first for _ in range(50))
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (`ModuleNotFoundError: app.workouts.exercise_match`).

- [ ] **Step 3: Implement the matcher**

Create `app/workouts/exercise_match.py`:

```python
"""Match the exercise name on a workout plan to an exercise in the database, by function rather than spelling.

Tiers, first hit wins: exact name; alias; normalised (case, punctuation, DB/BB/KB abbreviations, plurals and word
order ignored; the exercise's own equipment counts, so "Barbell Bench Press" is "Bench Press"); then fuzzy, which
scores shared words and spelling together and compares equipment words. A fuzzy match is accepted only when it is
clearly the best, or when every near-best candidate would give the same calorie estimate. Anything weaker comes back
as suggestions for a person to confirm, never applied."""

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from app.workouts import exercise_db
from app.workouts.exercise_db import Exercise

_SYNONYMS = {"db": "dumbbell", "dumbell": "dumbbell", "bb": "barbell", "kb": "kettlebell", "bw": "bodyweight",
             "pushup": "push up", "pullup": "pull up", "chinup": "chin up", "situp": "sit up", "stepup": "step up",
             "ohp": "overhead press", "rdl": "romanian deadlift", "sldl": "stiff leg deadlift",
             "dumbbells": "dumbbell", "barbells": "barbell"}
# whole-query shortcuts for names people use that no database name spells
_QUERY_ALIASES = {"running": "Treadmill Run", "run": "Treadmill Run", "jogging": "Treadmill Jog",
                  "jog": "Treadmill Jog", "walking": "Treadmill Walk", "walk": "Treadmill Walk",
                  "military press": "Overhead Press", "incline walk": "Treadmill Incline Walk",
                  "incline walking": "Treadmill Incline Walk"}
_EQUIPMENT = {"barbell", "dumbbell", "cable", "machine", "kettlebell", "smith", "bodyweight", "band", "ez"}
_NOISE = {"the", "a", "an", "with", "on", "of", "and"}
_CONFIDENT = 0.70
_MARGIN = 0.05
_SUGGEST = 0.50
_MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class ExerciseMatch:
    exercise: Exercise | None
    how: str                      # "exact" | "alias" | "normalized" | "fuzzy" | "none"
    score: float = 1.0
    suggestions: tuple[Exercise, ...] = field(default_factory=tuple)

    @property
    def confident(self) -> bool:
        return self.exercise is not None


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 2 and word.endswith("s") and not word.endswith("ss") else word


@lru_cache(maxsize=4096)
def tokens(text: str) -> tuple[str, ...]:
    """Lowercase words with abbreviations expanded and plurals folded, in order."""
    text = unicodedata.normalize("NFKC", text or "").casefold().replace("&", " and ")
    out = []
    for word in re.findall(r"[a-z0-9]+", text):
        expanded = _SYNONYMS.get(word) or _SYNONYMS.get(_singular(word)) or word
        for part in expanded.split():
            part = _singular(part)
            if part and part not in _NOISE:
                out.append(part)
    return tuple(out)


def _key(words) -> str:
    return " ".join(sorted(words))


def _equipment_words(ex: Exercise) -> tuple[str, ...]:
    return tuple(w for w in tokens(ex.equipment or "") if w in _EQUIPMENT)


def _profile(ex: Exercise) -> tuple:
    """What decides a calorie estimate: two exercises with the same profile burn the same."""
    return ex.style, ex.sec_per_rep, ex.rest_min, ex.met, ex.model, ex.speed_table


@lru_cache(maxsize=1)
def _candidates() -> tuple[tuple[Exercise, tuple[str, ...], tuple[str, ...]], ...]:
    """(exercise, words of the name or alias, the same plus the exercise's own equipment) for every name and alias."""
    by_name = {e.name: e for e in exercise_db.all_exercises()}
    rows = []
    for e in by_name.values():
        words = tokens(e.name)
        rows.append((e, words, words + tuple(w for w in _equipment_words(e) if w not in words)))
    for alias, canonical in exercise_db.aliases().items():
        e = by_name.get(canonical)
        if e is not None:
            words = tokens(alias)
            rows.append((e, words, words + tuple(w for w in _equipment_words(e) if w not in words)))
    return tuple(rows)


def _score(query: tuple[str, ...], cand: tuple[str, ...]) -> float:
    a, b = set(query), set(cand)
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    spelling = difflib.SequenceMatcher(None, _key(a), _key(b)).ratio()
    score = 0.55 * jaccard + 0.45 * spelling
    if a < b or b < a:
        score += 0.15          # every word of one is in the other: one name is the other with extra detail
    eq_a, eq_b = a & _EQUIPMENT, b & _EQUIPMENT
    if eq_a and eq_b and not (eq_a & eq_b):
        score -= 0.30          # a barbell curl is not a cable curl
    return min(max(score, 0.0), 0.99)


def match_exercise(name: str | None) -> ExerciseMatch:
    raw = (name or "").strip()
    query = tokens(raw)
    if not query:
        return ExerciseMatch(None, "none", 0.0)
    exact = exercise_db.get(raw)
    if exact:
        return ExerciseMatch(exact, "exact")
    canonical = exercise_db.canonical_for_alias(raw) or _QUERY_ALIASES.get(" ".join(re.findall(r"[a-z]+", raw.casefold())))
    if canonical and exercise_db.get(canonical):
        return ExerciseMatch(exercise_db.get(canonical), "alias")
    key = _key(query)
    for position in (1, 2):   # the name as written, then the name with its equipment ("barbell bench press")
        same = {row[0].name: row[0] for row in _candidates() if _key(row[position]) == key}
        if len(same) == 1:
            return ExerciseMatch(next(iter(same.values())), "normalized")

    best: dict[str, tuple[float, Exercise]] = {}
    for e, _, words in _candidates():
        score = _score(query, words)
        if score > best.get(e.name, (0.0, e))[0]:
            best[e.name] = (score, e)
    ranked = sorted(best.values(), key=lambda item: (-item[0], len(item[1].name), item[1].name))
    if not ranked or ranked[0][0] < _SUGGEST:
        return ExerciseMatch(None, "none", ranked[0][0] if ranked else 0.0)
    top_score = ranked[0][0]
    suggestions = tuple(e for s, e in ranked[:_MAX_SUGGESTIONS] if s >= _SUGGEST)
    if top_score >= _CONFIDENT:
        group = [e for s, e in ranked if s >= top_score - _MARGIN]
        if len(group) == 1 or len({_profile(e) for e in group}) == 1:
            return ExerciseMatch(group[0], "fuzzy", top_score, suggestions)
    return ExerciseMatch(None, "none", top_score, suggestions)
```

- [ ] **Step 4: Run the tests and the whole suite**

Expected: 27 passed in the file, full suite green.

- [ ] **Step 5: Commit**

```bash
git add app/workouts/exercise_match.py tests/test_exercise_match.py
git commit -m "feat: fuzzy matcher from plan exercise names to database exercises

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 4: History survives plan changes (schema and behaviour)

**Files:**
- Create: `migrations/versions/0034_workout_history.py`, `tests/test_workout_history.py`
- Modify: `tests/conftest.py` (the cleanup), `app/models.py` (the three workout tables, ~lines 1148-1182), `app/routers/workouts.py` (`save_workout_plan` docstring, `workouts_log_save`, `workouts_edit`), `app/routers/journal.py` (`workouts_for`), `app/templates/workouts/edit.html` (warning to note), `app/static/js/workouts.js` (`confirmRemoval` text), `tests/test_workouts.py` (the history-warning tests and the day-removal test)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (Tasks 5-12): `WorkoutExercise.db_exercise: str | None`, `WorkoutExercise.db_exercise_confirmed: bool`; `WorkoutLog.plan_day_id: int | None` (nullable, SET NULL), `WorkoutLog.day_label`, `WorkoutLog.plan_name`; `WorkoutExerciseLog.exercise_id: int | None` (nullable, SET NULL) and the snapshot columns `name, db_exercise, area, equipment, sets, duration_min, speed_mph, grade_pct, implements, style, sec_per_rep, rest_min, met, body_weight_lb, gross_kcal, net_kcal, volume_lb, compendium_code, kcal_note`. A removed plan day or exercise leaves its logs in place.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workout_history.py`:

```python
from datetime import date

from sqlalchemy import select

from app.models import WorkoutExerciseLog, WorkoutLog, WorkoutPlan, WorkoutPlanDay
from app.routers.journal import workouts_for
from test_workouts import _form_from_plan, _two_day_plan  # helpers: a two-day plan, Day A logged 2026-01-05, Day B logged 2026-01-06


def _without_day_b(plan):
    form = _form_from_plan(plan)
    form = {k: v for k, v in form.items() if not k.endswith("[1][]")}
    form["day_label[]"], form["day_id[]"] = form["day_label[]"][:1], form["day_id[]"][:1]
    return form


def test_removing_a_day_keeps_its_logged_history(client, db):
    plan = _two_day_plan(client, db, name="History Day Plan")
    owner_id, plan_name = plan.owner_id, plan.name
    client.post(f"/workouts/{plan.id}", data=_without_day_b(plan))
    db.expire_all()
    log = db.scalar(select(WorkoutLog).where(WorkoutLog.owner_id == owner_id, WorkoutLog.log_date == date(2026, 1, 6),
                                             WorkoutLog.plan_name == plan_name))
    assert log is not None and log.plan_day_id is None and log.day_label == "Day B"
    assert [(el.exercise_id, el.name, el.completed) for el in log.exercise_logs] == [(None, "Plank", True)]


def test_removing_an_exercise_keeps_what_was_logged_for_it(client, db):
    plan = _two_day_plan(client, db, name="History Exercise Plan")
    form = _form_from_plan(plan)
    for key in ("id", "name", "sets", "reps", "rest"):
        form[f"exercise_{key}[0][]"] = form[f"exercise_{key}[0][]"][1:]   # drop Push-up, keep Squat
    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()
    kept = db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == "Push-up",
                                                      WorkoutExerciseLog.weight_value == 20.0))
    assert kept is not None and kept.exercise_id is None and kept.completed is True


def test_logging_a_new_day_never_replaces_a_removed_days_history(client, db):
    plan = _two_day_plan(client, db, name="History Replace Plan")
    plan_name = plan.name
    form = _without_day_b(plan)
    form["day_label[]"].append("Day C")
    form["day_id[]"].append("")
    form["exercise_id[1][]"], form["exercise_name[1][]"] = [""], ["Lunge"]
    form["exercise_sets[1][]"], form["exercise_reps[1][]"], form["exercise_rest[1][]"] = ["3"], ["10"], [""]
    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()
    day_c = db.scalar(select(WorkoutPlanDay).where(WorkoutPlanDay.label == "Day C"))
    lunge = day_c.exercises[0]
    assert client.post(f"/workouts/day/{day_c.id}/log", data={
        "log_date": "2026-01-06", f"completed[{lunge.id}]": "on"}, follow_redirects=False).status_code == 303
    db.expire_all()
    labels = sorted(l.day_label for l in db.scalars(select(WorkoutLog).where(
        WorkoutLog.log_date == date(2026, 1, 6), WorkoutLog.plan_name == plan_name)))
    assert labels == ["Day B", "Day C"]


def test_the_journal_uses_the_saved_label_when_the_day_is_gone(client, db):
    plan = _two_day_plan(client, db, name="History Journal Plan")
    owner_id = plan.owner_id
    client.post(f"/workouts/{plan.id}", data=_without_day_b(plan))
    db.expire_all()
    rows = workouts_for(db, owner_id, date(2026, 1, 6))
    assert [r["label"] for r in rows if r["label"] == "Day B"] == ["Day B"]


def test_the_edit_page_says_history_is_kept_instead_of_threatening_to_delete_it(client, db):
    plan = _two_day_plan(client, db, name="History Note Plan")
    page = client.get(f"/workouts/{plan.id}/edit").text
    assert "stay in your history" in page and "will also delete" not in page
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_history.py -q -p no:warnings`
Expected: FAIL (history is deleted today: the first two assertions find no log; the note text is missing).

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0034_workout_history.py`. It was run against a scratch copy of the migrations with seeded data (backfill, `ON DELETE SET NULL`, downgrade, re-upgrade all verified):

```python
"""workout history that survives plan changes, with the data behind calorie estimates

A logged workout used to be deleted when its plan day or exercise was removed (ON DELETE CASCADE). History is now
kept: the log keeps a snapshot of its day label and plan name, every exercise log keeps its own name and what its
calorie estimate used, and the foreign keys to the plan rows become ON DELETE SET NULL. Plan exercises also gain
the database exercise they were matched to.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0034'
down_revision: Union[str, None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 0024 created these foreign keys without names; this convention gives the reflected ones a name to drop by.
_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}

_LOG_COLUMNS = (
    ('name', sa.String(200)), ('db_exercise', sa.String(200)), ('area', sa.String(60)), ('equipment', sa.String(60)),
    ('sets', sa.Integer()), ('duration_min', sa.Float()), ('speed_mph', sa.Float()), ('grade_pct', sa.Float()),
    ('implements', sa.Integer()), ('style', sa.String(40)), ('sec_per_rep', sa.Float()), ('rest_min', sa.Float()),
    ('met', sa.Float()), ('body_weight_lb', sa.Float()), ('gross_kcal', sa.Float()), ('net_kcal', sa.Float()),
    ('volume_lb', sa.Float()), ('compendium_code', sa.String(10)), ('kcal_note', sa.String(200)),
)


def upgrade() -> None:
    with op.batch_alter_table('workout_exercises') as batch_op:
        batch_op.add_column(sa.Column('db_exercise', sa.String(200)))
        batch_op.add_column(sa.Column('db_exercise_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table('workout_logs', naming_convention=_NAMING) as batch_op:
        batch_op.add_column(sa.Column('day_label', sa.String(200)))
        batch_op.add_column(sa.Column('plan_name', sa.String(200)))
        batch_op.alter_column('plan_day_id', existing_type=sa.Integer(), nullable=True)
        batch_op.drop_constraint('fk_workout_logs_plan_day_id_workout_plan_days', type_='foreignkey')
        batch_op.create_foreign_key('fk_workout_logs_plan_day_id_workout_plan_days', 'workout_plan_days',
                                    ['plan_day_id'], ['id'], ondelete='SET NULL')
    op.execute(
        "UPDATE workout_logs SET "
        "day_label = (SELECT d.label FROM workout_plan_days d WHERE d.id = workout_logs.plan_day_id), "
        "plan_name = (SELECT p.name FROM workout_plan_days d JOIN workout_plans p ON p.id = d.plan_id "
        "WHERE d.id = workout_logs.plan_day_id)")

    with op.batch_alter_table('workout_exercise_logs', naming_convention=_NAMING) as batch_op:
        for name, type_ in _LOG_COLUMNS:
            batch_op.add_column(sa.Column(name, type_))
        batch_op.alter_column('exercise_id', existing_type=sa.Integer(), nullable=True)
        batch_op.drop_constraint('fk_workout_exercise_logs_exercise_id_workout_exercises', type_='foreignkey')
        batch_op.create_foreign_key('fk_workout_exercise_logs_exercise_id_workout_exercises', 'workout_exercises',
                                    ['exercise_id'], ['id'], ondelete='SET NULL')
    op.execute(
        "UPDATE workout_exercise_logs SET "
        "name = (SELECT e.name FROM workout_exercises e WHERE e.id = workout_exercise_logs.exercise_id), "
        "implements = 1")


def downgrade() -> None:
    # The old schema cannot hold history that no longer has a plan row, so that history is dropped here.
    op.execute("DELETE FROM workout_exercise_logs WHERE exercise_id IS NULL")
    op.execute("DELETE FROM workout_logs WHERE plan_day_id IS NULL")
    with op.batch_alter_table('workout_exercise_logs', naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint('fk_workout_exercise_logs_exercise_id_workout_exercises', type_='foreignkey')
        batch_op.create_foreign_key('fk_workout_exercise_logs_exercise_id_workout_exercises', 'workout_exercises',
                                    ['exercise_id'], ['id'], ondelete='CASCADE')
        batch_op.alter_column('exercise_id', existing_type=sa.Integer(), nullable=False)
        for name, _ in reversed(_LOG_COLUMNS):
            batch_op.drop_column(name)
    with op.batch_alter_table('workout_logs', naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint('fk_workout_logs_plan_day_id_workout_plan_days', type_='foreignkey')
        batch_op.create_foreign_key('fk_workout_logs_plan_day_id_workout_plan_days', 'workout_plan_days',
                                    ['plan_day_id'], ['id'], ondelete='CASCADE')
        batch_op.alter_column('plan_day_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('plan_name')
        batch_op.drop_column('day_label')
    with op.batch_alter_table('workout_exercises') as batch_op:
        batch_op.drop_column('db_exercise_confirmed')
        batch_op.drop_column('db_exercise')
```

- [ ] **Step 4: Change the models**

In `app/models.py`, add to `WorkoutExercise` (after `rest_text`):

```python
    # The database exercise this plan row was matched to (see app/workouts/exercise_match.py). A person-confirmed
    # match survives edits; an automatic one is recomputed when the name changes.
    db_exercise: Mapped[str | None] = mapped_column(String(200))
    db_exercise_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
```

Replace the `WorkoutLog` columns `plan_day_id` and add the snapshots (keep the unique constraint, `id`, `owner_id`, `log_date`, `completed_at`, the relationships):

```python
    plan_day_id: Mapped[int | None] = mapped_column(ForeignKey("workout_plan_days.id", ondelete="SET NULL"))
    # Snapshots, so a log reads the same after its plan day or plan is edited or removed.
    day_label: Mapped[str | None] = mapped_column(String(200))
    plan_name: Mapped[str | None] = mapped_column(String(200))
```

and change the relationship line to `plan_day: Mapped["WorkoutPlanDay | None"] = relationship()`.

Replace `WorkoutExerciseLog` with:

```python
class WorkoutExerciseLog(Base):
    """One exercise within a logged workout. Everything its calorie estimate used is stored here, so the log keeps
    reading the same after the plan or the exercise database changes, and history can be followed per exercise."""
    __tablename__ = "workout_exercise_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_log_id: Mapped[int] = mapped_column(ForeignKey("workout_logs.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[int | None] = mapped_column(ForeignKey("workout_exercises.id", ondelete="SET NULL"))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    weight_value: Mapped[float | None] = mapped_column(Float)
    weight_unit: Mapped[WeightUnit | None] = mapped_column(_enum_column(WeightUnit))
    reps_value: Mapped[int | None] = mapped_column(Integer)    # reps per set, an exact whole number

    name: Mapped[str | None] = mapped_column(String(200))        # the exercise as logged
    db_exercise: Mapped[str | None] = mapped_column(String(200)) # its canonical name in the exercise database
    area: Mapped[str | None] = mapped_column(String(60))
    equipment: Mapped[str | None] = mapped_column(String(60))
    sets: Mapped[int | None] = mapped_column(Integer)
    duration_min: Mapped[float | None] = mapped_column(Float)
    speed_mph: Mapped[float | None] = mapped_column(Float)
    grade_pct: Mapped[float | None] = mapped_column(Float)
    implements: Mapped[int | None] = mapped_column(Integer)
    style: Mapped[str | None] = mapped_column(String(40))
    sec_per_rep: Mapped[float | None] = mapped_column(Float)
    rest_min: Mapped[float | None] = mapped_column(Float)
    met: Mapped[float | None] = mapped_column(Float)
    body_weight_lb: Mapped[float | None] = mapped_column(Float)
    gross_kcal: Mapped[float | None] = mapped_column(Float)
    net_kcal: Mapped[float | None] = mapped_column(Float)
    volume_lb: Mapped[float | None] = mapped_column(Float)
    compendium_code: Mapped[str | None] = mapped_column(String(10))
    kcal_note: Mapped[str | None] = mapped_column(String(200))   # why there is no estimate, when there is none
```

- [ ] **Step 5: Snapshot on save, and read the saved label**

In `app/routers/workouts.py` `workouts_log_save`, build the exercise logs with their name and the log with its labels (the full calorie logic arrives in Task 5; this keeps history readable now):

```python
        exercise_logs.append(WorkoutExerciseLog(
            exercise_id=ex.id, name=ex.name, completed=raw.get(f"completed[{ex.id}]") == "on",
            weight_value=weight, weight_unit=unit, reps_value=reps,
        ))
```
```python
    log = WorkoutLog(owner_id=uid, plan_day_id=day.id, day_label=day.label, plan_name=day.plan.name,
                     log_date=log_date, exercise_logs=exercise_logs)
```

Replace the `save_workout_plan` docstring paragraph that says removal "cascades away that row's logged history -- the one intentionally destructive case" and the sentence about the earlier approach with: "An existing row whose id isn't posted back at all is removed (delete-orphan). Its logged history is kept: logs hold their own snapshot and the foreign keys to the plan rows are ON DELETE SET NULL, so removing a day or exercise never deletes what was logged."

In `app/routers/journal.py` `workouts_for`, use the saved label:

```python
        {"label": r.day_label or (r.plan_day.label if r.plan_day else "Workout"),
```

- [ ] **Step 6: Replace the warning with a note**

In `app/templates/workouts/edit.html` replace the `has_logged_history` paragraph with:

```jinja
  {% if has_logged_history %}
  <p class="muted small">Workouts you have already logged stay in your history, even if you remove a day or exercise here.</p>
  {% endif %}
```

In `app/static/js/workouts.js` change the confirm text in `confirmRemoval` to `` `Remove this ${what}? Workouts you already logged stay in your history.` ``.

- [ ] **Step 7: Make the test cleanup remove logs explicitly**

Logs now outlive their plans, so deleting `WorkoutPlan` rows in `tests/conftest.py`'s `clean` fixture no longer removes them and they would leak into later tests. Add `WorkoutLog` to the imported models and delete it (its exercise logs cascade) just before the plan rows:

```python
        s.query(WorkoutLog).delete()
        s.query(WorkoutPlan).delete()
```

(If the existing line is `s.query(WorkoutPlan).delete()`, put `s.query(WorkoutLog).delete()` directly above it.)

- [ ] **Step 8: Update the existing tests that asserted deletion**

In `tests/test_workouts.py`: change `_HISTORY_WARNING = "will also delete any logged history for it"` to `_HISTORY_NOTE = "stay in your history"`; in the two edit-page tests use `_HISTORY_NOTE` (no note without history; the note after a logged workout) and delete the `assert 'class="alert"' in r.text` line; rename `test_removing_a_day_deletes_it_and_its_logs` to `test_removing_a_day_deletes_the_day_but_keeps_its_logs` and replace its two log assertions with:

```python
    kept = db.scalar(select(WorkoutLog).where(WorkoutLog.day_label == "Day B", WorkoutLog.plan_day_id.is_(None)))
    assert kept is not None                                   # Day B's log is history now, not deleted
    assert db.scalar(select(WorkoutLog).where(WorkoutLog.plan_day_id == day_a_id)) is not None
```

- [ ] **Step 9: Run the new tests, then the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_history.py tests/test_workouts.py -q -p no:warnings` then the full suite.
Expected: all pass. Also check the single head and a round trip: `.venv/Scripts/python.exe -m alembic heads` prints only `0034` (head).

- [ ] **Step 10: Commit**

```bash
git add migrations/versions/0034_workout_history.py tests/conftest.py app/models.py app/routers/workouts.py app/routers/journal.py app/templates/workouts/edit.html app/static/js/workouts.js tests/test_workout_history.py tests/test_workouts.py
git commit -m "feat: logged workouts survive plan changes, with snapshots for calorie estimates

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Logging service and route (calories on every logged workout)

**Files:**
- Create: `app/workouts/logging.py`, `tests/test_workout_logging.py`
- Modify: `app/routers/workouts.py` (`workouts_log_form`, `workouts_log_save`, imports)

**Interfaces:**
- Consumes: `calories.estimate`, `exercise_db.get/choosable_styles/speed_tables`, `exercise_match.match_exercise`, the Task 4 columns.
- Produces (Tasks 6-12): `workout_logging.latest_body_weight(session, uid) -> float | None`; `workout_logging.parse_log_form(raw, day) -> ParsedLog` (raises `HTTPException(422)`); `workout_logging.build_exercise_log(row, body_weight_lb) -> WorkoutExerciseLog`; `workout_logging.form_row(session, uid, ex, prior) -> dict`; `workout_logging.last_performance(session, uid, db_exercise) -> WorkoutExerciseLog | None`; `workout_logging.single_int(text) -> int | None`; form field names `completed[ID]`, `weight_value[ID]`, `weight_unit[ID]`, `reps_value[ID]` (existing) plus `sets_value[ID]`, `minutes_value[ID]`, `speed_value[ID]`, `grade_value[ID]`, `style_value[ID]`, `implements_value[ID]`, `body_weight_lb`, and extras `extra-N-exercise`, `extra-N-weight_value`, `extra-N-weight_unit`, `extra-N-reps_value`, `extra-N-sets_value`, `extra-N-minutes_value`, `extra-N-speed_value`, `extra-N-grade_value`, `extra-N-style_value`, `extra-N-implements_value`.

Behaviour in one place: a ticked exercise that matches a database exercise gets an estimate when sets and reps (or minutes, and speed or grade for walk/jog/run) and a body weight are present; otherwise it saves with `kcal_note` saying what is missing. Reps and sets are exact whole numbers 1-999; a range is refused.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workout_logging.py`:

```python
from datetime import date

import pytest
from sqlalchemy import select

from app.models import BodyMeasurement, WorkoutExerciseLog, WorkoutLog, WorkoutPlan
from app.workouts import calories, exercise_db
from test_workouts import _plan_form

DAY = "2026-02-02"


@pytest.fixture
def weigh_in(db, me):
    """The test user's only weigh-in: 200 lb. Other tests leave body measurements behind, so start from none; remove after."""
    db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
    entry = BodyMeasurement(owner_id=me, measured_at=date(2026, 1, 1), weight_lbs=200.0)
    db.add(entry)
    db.commit()
    yield entry
    db.query(BodyMeasurement).filter(BodyMeasurement.id == entry.id).delete()
    db.commit()


def plan_with(client, db, names, name="Logging Plan", sets="3", reps="8"):
    client.post("/workouts", data=_plan_form(**{
        "name": name, "exercise_name[0][]": names, "exercise_sets[0][]": [sets] * len(names),
        "exercise_reps[0][]": [reps] * len(names), "exercise_rest[0][]": [""] * len(names)}))
    db.expire_all()
    return db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == name))


def post_log(client, day, extra=None, **fields):
    data = {"log_date": DAY, **fields, **(extra or {})}
    return client.post(f"/workouts/day/{day.id}/log", data=data, follow_redirects=False)


def logged(db, name):
    db.expire_all()
    return db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == name).order_by(WorkoutExerciseLog.id.desc()))


def test_a_ticked_matched_exercise_gets_the_workbook_estimate_and_a_snapshot(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Estimate Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    r = post_log(client, day, **{f"completed[{ex.id}]": "on", f"weight_value[{ex.id}]": "185", f"weight_unit[{ex.id}]": "lb",
                                 f"sets_value[{ex.id}]": "4", f"reps_value[{ex.id}]": "8",
                                 f"style_value[{ex.id}]": "Hypertrophy / General"})
    assert r.status_code == 303
    row = logged(db, "Bench Press")
    kg = 200 / calories.LB_PER_KG
    assert (row.db_exercise, row.area, row.equipment, row.met, row.style) == ("Bench Press", "Chest", "Barbell", 3.5, "Hypertrophy / General")
    assert row.sets == 4 and row.reps_value == 8 and row.body_weight_lb == 200 and row.kcal_note is None
    assert row.volume_lb == 185 * 4 * 8
    assert row.gross_kcal == pytest.approx(3.5 * 3.5 * kg / 200 * 2.4 + 1.5 * 3.5 * kg / 200 * 9.0)
    assert row.net_kcal == pytest.approx(2.5 * 3.5 * kg / 200 * 2.4 + 0.5 * 3.5 * kg / 200 * 9.0)
    log = db.get(WorkoutLog, row.workout_log_id)
    assert (log.day_label, log.plan_name) == (day.label, "Logging Estimate Plan")


def test_the_style_defaults_to_the_exercises_own(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Style Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "5"})
    row = logged(db, "Bench Press")
    assert (row.style, row.met) == ("Heavy Strength", 5.0)


def test_no_body_weight_saves_the_log_and_says_why(client, db, me):
    db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
    db.commit()
    plan = plan_with(client, db, ["Bench Press"], name="Logging No Weight Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    assert post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3",
                                    f"reps_value[{ex.id}]": "8"}).status_code == 303
    row = logged(db, "Bench Press")
    assert row.net_kcal is None and "body weight" in row.kcal_note and row.completed is True


def test_a_posted_body_weight_beats_the_latest_weigh_in(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Logging Override Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, body_weight_lb="150", **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    assert logged(db, "Push-up").body_weight_lb == 150.0


def test_missing_sets_or_reps_are_named_and_unticked_rows_get_no_estimate(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press", "Push-up"], name="Logging Missing Plan")
    day, bench, push = plan.days[0], plan.days[0].exercises[0], plan.days[0].exercises[1]
    post_log(client, day, **{f"completed[{bench.id}]": "on", f"sets_value[{bench.id}]": "3"})   # reps missing; Push-up not ticked
    assert logged(db, "Bench Press").kcal_note == "Enter reps per set for a calorie estimate."
    assert (logged(db, "Push-up").net_kcal, logged(db, "Push-up").kcal_note) == (None, None)


def test_an_exercise_the_database_does_not_know_logs_without_calories(client, db, weigh_in):
    plan = plan_with(client, db, ["Zorvex Lift"], name="Logging Unknown Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "8"})
    row = logged(db, "Zorvex Lift")
    assert row.db_exercise is None and row.net_kcal is None and "database" in row.kcal_note


@pytest.mark.parametrize("field,value", [
    ("reps_value", "8-12"), ("reps_value", "10.5"), ("reps_value", "0"), ("reps_value", "1000"), ("reps_value", "-3"),
    ("sets_value", "3-4"), ("sets_value", "0"), ("sets_value", "abc"),
    ("weight_value", "-5"), ("minutes_value", "0"), ("style_value", "Not A Style"), ("implements_value", "0")])
def test_ranges_and_impossible_numbers_are_refused_and_the_existing_log_is_kept(client, db, weigh_in, field, value):
    plan = plan_with(client, db, ["Bench Press"], name=f"Logging Refuse {field} {value}")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    good = {f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "8", f"weight_value[{ex.id}]": "100"}
    assert post_log(client, day, **good).status_code == 303
    bad = {**good, f"{field}[{ex.id}]": value}
    r = post_log(client, day, **bad)
    assert r.status_code == 422
    row = logged(db, "Bench Press")
    assert row.reps_value == 8 and row.sets == 3 and row.weight_value == 100.0


def test_a_bad_body_weight_is_refused(client, db):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Bad Weight Plan")
    for bad in ("abc", "0", "-10", "5000", "nan"):
        assert post_log(client, plan.days[0], body_weight_lb=bad).status_code == 422


def test_kilogram_loads_convert_for_volume_only(client, db, weigh_in):
    plan = plan_with(client, db, ["Dumbbell Curl"], name="Logging Kg Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"weight_value[{ex.id}]": "10", f"weight_unit[{ex.id}]": "kg",
                             f"sets_value[{ex.id}]": "2", f"reps_value[{ex.id}]": "10"})
    row = logged(db, "Dumbbell Curl")
    assert row.weight_unit.value == "kg" and row.weight_value == 10.0
    assert row.volume_lb == pytest.approx(10 * calories.LB_PER_KG * 2 * 10)


def test_a_walk_jog_or_run_needs_minutes_and_speed_and_uses_the_compendium(client, db, weigh_in):
    plan = plan_with(client, db, ["Treadmill Run", "Treadmill Incline Walk"], name="Logging Cardio Plan")
    day, run, incline = plan.days[0], plan.days[0].exercises[0], plan.days[0].exercises[1]
    post_log(client, day, **{f"completed[{run.id}]": "on", f"minutes_value[{run.id}]": "30",
                             f"completed[{incline.id}]": "on", f"minutes_value[{incline.id}]": "20"})
    assert logged(db, "Treadmill Run").kcal_note == "Enter speed for a calorie estimate."
    assert logged(db, "Treadmill Incline Walk").kcal_note == "Enter incline grade for a calorie estimate."
    post_log(client, day, **{f"completed[{run.id}]": "on", f"minutes_value[{run.id}]": "30", f"speed_value[{run.id}]": "6",
                             f"completed[{incline.id}]": "on", f"minutes_value[{incline.id}]": "20", f"grade_value[{incline.id}]": "8"})
    run_row, incline_row = logged(db, "Treadmill Run"), logged(db, "Treadmill Incline Walk")
    assert (run_row.met, run_row.compendium_code, run_row.speed_mph) == (9.3, "12050", 6.0)
    assert (incline_row.met, incline_row.compendium_code, incline_row.grade_pct) == (7.0, "17035", 8.0)


def test_an_exercise_added_on_the_day_is_matched_by_fuzzy_name_and_estimated(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Extra Plan")
    day = plan.days[0]
    r = post_log(client, day, extra={
        "extra-0-exercise": "DB incline press", "extra-0-weight_value": "40", "extra-0-weight_unit": "lb",
        "extra-0-sets_value": "3", "extra-0-reps_value": "10", "extra-0-implements_value": "2",
        "extra-1-exercise": "", "extra-1-sets_value": "9"})            # a blank row is ignored
    assert r.status_code == 303
    row = logged(db, "Dumbbell Incline Press")
    assert row.exercise_id is None and row.db_exercise == "Dumbbell Incline Press" and row.completed is True
    assert row.net_kcal > 0 and row.volume_lb == 40 * 2 * 3 * 10 and row.implements == 2
    db.expire_all()
    assert db.query(WorkoutExerciseLog).filter(WorkoutExerciseLog.name == "").count() == 0


def test_an_added_exercise_that_is_not_in_the_database_is_refused_with_suggestions(client, db):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Extra Refuse Plan")
    r = post_log(client, plan.days[0], extra={"extra-0-exercise": "Squats", "extra-0-sets_value": "3", "extra-0-reps_value": "5"})
    assert r.status_code == 422 and "Squats" in r.text
    r = post_log(client, plan.days[0], extra={"extra-0-exercise": "Zzz Quasar Lift"})
    assert r.status_code == 422


def test_saving_again_replaces_the_days_log_and_recalculates(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Logging Replace Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    first = logged(db, "Push-up").net_kcal
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "6", f"reps_value[{ex.id}]": "10"})
    db.expire_all()
    rows = db.scalars(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == "Push-up")).all()
    assert len(rows) == 1 and rows[0].net_kcal > first


def test_the_log_form_shows_the_estimate_the_total_and_last_times_numbers(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Logging Form Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"weight_value[{ex.id}]": "135", f"weight_unit[{ex.id}]": "lb",
                             f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "8"})
    page = client.get(f"/workouts/day/{day.id}/log", params={"log_date": DAY}).text
    assert "kcal" in page and 'name="body_weight_lb"' in page and 'value="200"' in page
    assert f'name="sets_value[{ex.id}]" value="3"' in page and f'name="reps_value[{ex.id}]" value="8"' in page
    other = client.get(f"/workouts/day/{day.id}/log", params={"log_date": "2026-02-09"}).text   # a day with no log yet
    assert "Last time" in other and "135" in other                                              # history pre-fills the next session


def test_history_follows_the_exercise_into_a_later_plan(client, db, weigh_in):
    first = plan_with(client, db, ["Bench Press"], name="Logging History One")
    ex = first.days[0].exercises[0]
    post_log(client, first.days[0], **{f"completed[{ex.id}]": "on", f"weight_value[{ex.id}]": "155", f"weight_unit[{ex.id}]": "lb",
                                       f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "5"})
    second = plan_with(client, db, ["Barbell Bench Press"], name="Logging History Two")      # same exercise, new plan, new wording
    page = client.get(f"/workouts/day/{second.days[0].id}/log", params={"log_date": "2026-03-01"}).text
    assert "Last time" in page and "155" in page
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_logging.py -q -p no:warnings`
Expected: FAIL (the route ignores `sets_value`, writes no estimate; imports fine).

- [ ] **Step 3: Write the logging service**

Create `app/workouts/logging.py`:

```python
"""Turns the workout log form into stored exercise logs with calorie estimates, and finds an exercise's history.

The form's rules live here, not in the route: reps and sets are exact whole numbers (never a range), loads are
non-negative, a style must be one the database knows, and an exercise added on the day must resolve to a database
exercise. A ticked exercise with a database match is estimated when it has what the calculation needs; otherwise it
is saved anyway with a note saying what is missing, so a log is never refused for lack of calorie inputs."""

import math
import re
from dataclasses import dataclass
from datetime import date as date_type

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BodyMeasurement, WeightUnit, WorkoutExercise, WorkoutExerciseLog, WorkoutLog, WorkoutPlanDay
from app.workouts import calories, exercise_db
from app.workouts.exercise_db import Exercise
from app.workouts.exercise_match import match_exercise

MAX_COUNT = 999
MAX_BODY_WEIGHT_LB = 1500.0
MAX_MINUTES = 1000.0
MAX_SPEED_MPH = 30.0
MAX_GRADE_PCT = 40.0
_FIELDS = ("weight_value", "weight_unit", "reps_value", "sets_value", "minutes_value", "speed_value",
           "grade_value", "style_value", "implements_value")


@dataclass
class ParsedRow:
    exercise_id: int | None
    name: str
    db: Exercise | None
    completed: bool
    load: float | None
    unit: WeightUnit | None
    sets: int | None
    reps: int | None
    minutes: float | None
    speed: float | None
    grade: float | None
    style: str | None
    implements: int

    @property
    def load_lb(self) -> float | None:
        if self.load is None:
            return None
        return self.load * calories.LB_PER_KG if self.unit == WeightUnit.KG else self.load


@dataclass
class ParsedLog:
    log_date: date_type
    body_weight_lb: float | None
    rows: list[ParsedRow]


def latest_body_weight(session: Session, uid: int) -> float | None:
    """The owner's most recent weigh-in that recorded a weight."""
    return session.scalar(
        select(BodyMeasurement.weight_lbs)
        .where(BodyMeasurement.owner_id == uid, BodyMeasurement.weight_lbs.is_not(None))
        .order_by(BodyMeasurement.measured_at.desc(), BodyMeasurement.id.desc()).limit(1))


def single_int(text: str | None) -> int | None:
    """A plan's "3" as 3; a range or free text ("3-4", "AMRAP") as None, so it pre-fills nothing."""
    return int(text) if text and text.strip().isdigit() and 1 <= int(text) <= MAX_COUNT else None


def _text(raw, key: str) -> str:
    return str(raw.get(key) or "").strip()


def _number(raw, key: str, label: str, *, positive: bool = False, maximum: float | None = None) -> float | None:
    text = _text(raw, key)
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise HTTPException(422, f"{label} must be a number.")
    if not math.isfinite(value) or value < 0 or (positive and value == 0) or (maximum and value > maximum):
        limit = f" and at most {maximum:g}" if maximum else ""
        raise HTTPException(422, f"{label} must be {'greater than 0' if positive else 'a number from 0 up'}{limit}.")
    return value


def _whole(raw, key: str, label: str) -> int | None:
    text = _text(raw, key)
    if not text:
        return None
    if not re.fullmatch(r"\d+", text) or not 1 <= int(text) <= MAX_COUNT:
        raise HTTPException(422, f"{label} must be one whole number from 1 to {MAX_COUNT}, not a range.")
    return int(text)


def _row(raw, suffix: str, *, exercise_id: int | None, name: str, db: Exercise | None, completed: bool) -> ParsedRow:
    get = lambda field: _text(raw, f"{field}{suffix}")           # noqa: E731 -- suffix is "[ID]" or "" with a prefix
    key = lambda field: f"{field}{suffix}"                       # noqa: E731
    unit_text = get("weight_unit")
    try:
        unit = WeightUnit(unit_text) if unit_text else None
    except ValueError:
        raise HTTPException(422, f"Unknown weight unit for {name}.")
    load = _number(raw, key("weight_value"), f"Weight for {name}")
    style = get("style_value") or None
    if style and style not in exercise_db.choosable_styles():
        raise HTTPException(422, f"Unknown style for {name}.")
    return ParsedRow(
        exercise_id=exercise_id, name=name, db=db, completed=completed, load=load,
        unit=(unit or WeightUnit.LB) if load is not None else unit,
        sets=_whole(raw, key("sets_value"), f"Sets for {name}"), reps=_whole(raw, key("reps_value"), f"Reps for {name}"),
        minutes=_number(raw, key("minutes_value"), f"Minutes for {name}", positive=True, maximum=MAX_MINUTES),
        speed=_number(raw, key("speed_value"), f"Speed for {name}", positive=True, maximum=MAX_SPEED_MPH),
        grade=_number(raw, key("grade_value"), f"Incline grade for {name}", maximum=MAX_GRADE_PCT),
        style=style, implements=_whole(raw, key("implements_value"), f"Implements for {name}") or 1)


def _extra_rows(raw) -> list[ParsedRow]:
    """Exercises added on the day: 'extra-N-field' keys. A blank exercise name drops the row; a name must resolve."""
    indexes = sorted({int(m.group(1)) for k in raw.keys() if (m := re.fullmatch(r"extra-(\d+)-\w+", k))})
    rows = []
    for i in indexes:
        typed = _text(raw, f"extra-{i}-exercise")
        if not typed:
            continue
        match = match_exercise(typed)
        if not match.confident:
            close = ", ".join(e.name for e in match.suggestions)
            hint = f" Closest: {close}." if close else " Pick an exercise from the list."
            raise HTTPException(422, f'"{typed}" is not in the exercise database.{hint}')
        prefixed = {k.replace(f"extra-{i}-", "", 1): v for k, v in raw.items() if k.startswith(f"extra-{i}-")}
        rows.append(_row(prefixed, "", exercise_id=None, name=match.exercise.name, db=match.exercise, completed=True))
    return rows


def parse_log_form(raw, day: WorkoutPlanDay) -> ParsedLog:
    """Everything on the form, validated, before the existing log is touched (a bad value never costs what was logged)."""
    try:
        log_date = date_type.fromisoformat(raw["log_date"])
    except (KeyError, ValueError):
        raise HTTPException(422, "A valid workout date (YYYY-MM-DD) is required.")
    body_weight = _number(raw, "body_weight_lb", "Body weight", positive=True, maximum=MAX_BODY_WEIGHT_LB)
    rows = []
    for ex in day.exercises:
        rows.append(_row(raw, f"[{ex.id}]", exercise_id=ex.id, name=ex.name, db=exercise_db.get(ex.db_exercise) or
                         match_exercise(ex.name).exercise, completed=raw.get(f"completed[{ex.id}]") == "on"))
    rows.extend(_extra_rows(raw))
    return ParsedLog(log_date, body_weight, rows)


def build_exercise_log(row: ParsedRow, body_weight_lb: float | None) -> WorkoutExerciseLog:
    """The stored row, with its estimate and everything the estimate used."""
    ex = row.db
    log = WorkoutExerciseLog(
        exercise_id=row.exercise_id, name=row.name, completed=row.completed, weight_value=row.load,
        weight_unit=row.unit, reps_value=row.reps, sets=row.sets, duration_min=row.minutes, speed_mph=row.speed,
        grade_pct=row.grade, implements=row.implements, body_weight_lb=body_weight_lb,
        db_exercise=ex.name if ex else None, area=ex.area if ex else None, equipment=ex.equipment if ex else None,
        style=(row.style or ex.style) if ex and not ex.is_duration else None,
        sec_per_rep=ex.sec_per_rep if ex else None, rest_min=ex.rest_min if ex else None)
    if not row.completed:
        return log
    if ex is None:
        log.kcal_note = "No matching exercise in the database, so no calorie estimate."
        return log
    burn, missing = calories.estimate(
        ex, body_weight_lb=body_weight_lb, sets=row.sets, reps=row.reps, load_lb=row.load_lb,
        implements=row.implements, style=row.style, minutes=row.minutes, speed_mph=row.speed, grade_pct=row.grade)
    if burn is None:
        log.kcal_note = "Enter " + " and ".join(missing) + " for a calorie estimate."
        return log
    log.met, log.gross_kcal, log.net_kcal, log.volume_lb = burn.met, burn.gross_kcal, burn.net_kcal, burn.volume_lb
    log.compendium_code = burn.code if ex.speed_table else None
    return log


def last_performance(session: Session, uid: int, db_exercise: str | None) -> WorkoutExerciseLog | None:
    """The most recent completed log of this database exercise by this owner, in any plan."""
    if not db_exercise:
        return None
    return session.scalar(
        select(WorkoutExerciseLog).join(WorkoutLog, WorkoutExerciseLog.workout_log_id == WorkoutLog.id)
        .where(WorkoutLog.owner_id == uid, WorkoutExerciseLog.db_exercise == db_exercise,
               WorkoutExerciseLog.completed.is_(True))
        .order_by(WorkoutLog.log_date.desc(), WorkoutExerciseLog.id.desc()).limit(1))


def form_row(session: Session, uid: int, ex: WorkoutExercise, prior: WorkoutExerciseLog | None) -> dict:
    """What the log form needs to draw one plan exercise: its database match, this date's saved log (if any), the
    last time the exercise was logged anywhere, and the values to show in the inputs."""
    db = exercise_db.get(ex.db_exercise) or match_exercise(ex.name).exercise
    last = None if prior is not None else last_performance(session, uid, db.name if db else None)
    source = prior or last
    table = exercise_db.speed_tables().get(db.speed_table) if db and db.speed_table else None
    return {
        "ex": ex, "db": db, "prior": prior, "last": last, "speed_basis": table.basis if table else None,
        "speed_label": table.unit_label if table else None,
        "shown": {
            "weight_value": source.weight_value if source else None, "weight_unit": source.weight_unit if source else None,
            "sets": (source.sets if source and source.sets else None) or single_int(ex.sets_text),
            "reps": source.reps_value if source else None, "minutes": source.duration_min if source else None,
            "speed": source.speed_mph if source else None, "grade": source.grade_pct if source else None,
            "style": (source.style if source and source.style else None) or (db.style if db else None),
            "implements": (source.implements if source and source.implements else 1),
        },
    }
```

- [ ] **Step 4: Use the service in the route**

In `app/routers/workouts.py` add `from app.workouts import logging as workout_logging` (the module name is `app.workouts.logging`; import it under that alias so it never shadows the standard-library `logging`), and `from app.workouts import exercise_db`. Replace `workouts_log_form` and `workouts_log_save` with:

```python
@router.get("/workouts/day/{plan_day_id}/log")
def workouts_log_form(plan_day_id: int, request: Request, log_date: date_type | None = None,
                      session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    day = _get_own_day(session, plan_day_id, uid)
    log_date = log_date or date_type.today()
    # Re-opening an already-logged date shows what was logged (saving replaces that log), instead
    # of a blank form that would silently overwrite it with blanks.
    existing = session.scalar(
        select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id, WorkoutLog.log_date == log_date))
    saved = existing.exercise_logs if existing else []
    prior = {el.exercise_id: el for el in saved if el.exercise_id is not None}
    rows = [workout_logging.form_row(session, uid, ex, prior.get(ex.id)) for ex in day.exercises]
    body_weight = next((el.body_weight_lb for el in saved if el.body_weight_lb), None) \
        or workout_logging.latest_body_weight(session, uid)
    net = sum(el.net_kcal for el in saved if el.net_kcal)
    gross = sum(el.gross_kcal for el in saved if el.gross_kcal)
    return templates.TemplateResponse(request, "workouts/log.html", {
        "day": day, "log_date": log_date, "units": list(WeightUnit), "rows": rows,
        "extras": [el for el in saved if el.exercise_id is None], "body_weight": body_weight,
        "styles": exercise_db.choosable_styles(), "exercise_names": [e.name for e in exercise_db.all_exercises()],
        "net_kcal": net, "gross_kcal": gross, "has_estimate": any(el.net_kcal for el in saved),
    })


@router.post("/workouts/day/{plan_day_id}/log")
async def workouts_log_save(plan_day_id: int, request: Request, session: Session = Depends(get_session),
                            uid: int = Depends(current_user_id)):
    day = _get_own_day(session, plan_day_id, uid)
    raw = await request.form()
    parsed = workout_logging.parse_log_form(raw, day)          # validates everything before touching the old log
    body_weight = parsed.body_weight_lb or workout_logging.latest_body_weight(session, uid)
    exercise_logs = [workout_logging.build_exercise_log(row, body_weight) for row in parsed.rows]

    existing = session.scalar(
        select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id, WorkoutLog.log_date == parsed.log_date))
    if existing is not None:
        session.delete(existing)
        session.flush()

    log = WorkoutLog(owner_id=uid, plan_day_id=day.id, day_label=day.label, plan_name=day.plan.name,
                     log_date=parsed.log_date, exercise_logs=exercise_logs)
    session.add(log)
    session.commit()
    return RedirectResponse("/today", status_code=303)
```

Remove the now-unused `import math` if nothing else in the file uses it. Task 6 replaces `log.html` to match this context.

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_logging.py tests/test_workouts.py -q -p no:warnings`
Expected: the logging tests that read the form page (`test_the_log_form_shows...`, `test_history_follows...`) still fail until Task 6 rewrites `log.html`; every other test passes. Do not weaken them. (If `tests/test_workouts.py::test_log_form_prefills_from_an_existing_log_for_that_date` fails here, that is also Task 6's template; continue.)

- [ ] **Step 6: Commit the service and route**

```bash
git add app/workouts/logging.py app/routers/workouts.py tests/test_workout_logging.py
git commit -m "feat: workout logging with calorie estimates, exact reps and sets, and exercise history

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

(The commit may land with the two form-page tests red; Task 6 makes them green in the same session. The full suite is run and must be green at the end of Task 6, not here.)

---
### Task 6: The log form (inputs, estimate, history hint, exercises added on the day)

**Files:**
- Modify: `app/templates/workouts/log.html` (rewrite), `app/static/css/app.css` (append), `tests/test_workouts.py` (one prefill test)
- Create: `app/static/js/workout-log.js`, `tests/test_workout_log_form.py`

**Interfaces:**
- Consumes (Task 5 context from `workouts_log_form`): `day`, `log_date`, `units`, `rows` (each `{ex, db, prior, last, speed_basis, speed_label, shown}`), `extras`, `body_weight`, `styles`, `exercise_names`, `net_kcal`, `gross_kcal`, `has_estimate`; the field names listed in Task 5.
- Produces: the page behaviour the Task 5 form tests and Task 8's Journal button rely on. The existing attribute shapes the older tests assert (`name="completed[ID]" checked`, `name="weight_value[ID]" value="27.5"`, `name="reps_value[ID]" value="12"`, the `weight_unit` select with `<option value="kg" selected>`) are preserved.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workout_log_form.py`:

```python
import html
import re

import pytest
from sqlalchemy import select

from app.models import BodyMeasurement, WorkoutPlan
from test_workout_logging import DAY, plan_with, post_log, weigh_in  # noqa: F401  (weigh_in is a fixture)


def page(client, day, date=DAY):
    return html.unescape(client.get(f"/workouts/day/{day.id}/log", params={"log_date": date}).text)


def test_the_form_has_a_body_weight_field_and_whole_number_inputs(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Basic Plan", sets="3", reps="8-12")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    text = page(client, day)
    assert re.search(r'name="body_weight_lb" value="200"', text)
    assert f'name="sets_value[{ex.id}]" value="3"' in text            # a single number on the plan pre-fills sets
    assert re.search(rf'name="reps_value\[{ex.id}\]" value=""', text)   # a range pre-fills nothing: the real count is typed
    assert re.search(rf'<input type="number"[^>]*min="1"[^>]*step="1"[^>]*name="reps_value\[{ex.id}\]"', text)


def test_a_range_on_the_plan_does_not_prefill_sets_either(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Range Plan", sets="3-4")
    ex = plan.days[0].exercises[0]
    assert f'name="sets_value[{ex.id}]" value=""' in page(client, plan.days[0])


def test_cardio_rows_ask_for_minutes_and_speed_or_grade(client, db, weigh_in):
    plan = plan_with(client, db, ["Treadmill Run", "Treadmill Incline Walk", "Plank"], name="Form Cardio Plan")
    day = plan.days[0]
    run, incline, plank = day.exercises
    text = page(client, day)
    assert f'name="minutes_value[{run.id}]"' in text and f'name="speed_value[{run.id}]"' in text
    assert f'name="grade_value[{incline.id}]"' in text and f'name="speed_value[{incline.id}]"' not in text
    assert f'name="minutes_value[{plank.id}]"' in text and f'name="sets_value[{plank.id}]"' not in text


def test_rep_rows_offer_style_and_implements_under_adjust(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Adjust Plan")
    ex = plan.days[0].exercises[0]
    text = page(client, plan.days[0])
    assert f'name="style_value[{ex.id}]"' in text and f'name="implements_value[{ex.id}]"' in text
    assert re.search(r'<option value="Heavy Strength" selected>', text)      # the exercise's own default style


def test_an_unmatched_exercise_says_it_has_no_estimate(client, db, weigh_in):
    plan = plan_with(client, db, ["Zorvex Lift"], name="Form Unmatched Plan")
    assert "no calorie estimate" in page(client, plan.days[0])


def test_the_page_offers_the_exercise_list_and_an_add_row(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Extra Plan")
    text = page(client, plan.days[0])
    assert 'id="exercise-names"' in text and text.count("<option value=") > 230
    assert 'id="extra-row-template"' in text and 'data-action="add-extra"' in text and "extra-__I__-exercise" in text


def test_exercises_added_on_the_day_come_back_prefilled(client, db, weigh_in):
    plan = plan_with(client, db, ["Bench Press"], name="Form Extra Saved Plan")
    day = plan.days[0]
    post_log(client, day, extra={"extra-0-exercise": "DB incline press", "extra-0-weight_value": "40",
                                 "extra-0-sets_value": "3", "extra-0-reps_value": "10"})
    text = page(client, day)
    assert 'name="extra-0-exercise" value="Dumbbell Incline Press"' in text
    assert 'name="extra-0-weight_value" value="40"' in text and "kcal" in text


def test_no_weigh_in_tells_the_person_how_to_get_estimates(client, db, me):
    db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
    db.commit()
    plan = plan_with(client, db, ["Bench Press"], name="Form No Weight Plan")
    text = page(client, plan.days[0])
    assert 'name="body_weight_lb" value=""' in text and "Measurements" in text


def test_estimates_are_labeled_as_estimates(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Form Label Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    text = page(client, day)
    assert "estimated" in text and re.search(r"≈ \d+ kcal", text)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_log_form.py tests/test_workout_logging.py -q -p no:warnings`
Expected: FAIL (the old `log.html` has none of the new inputs; the two Task 5 form-page tests still red).

- [ ] **Step 3: Rewrite the template**

Replace `app/templates/workouts/log.html` with:

```jinja
{% extends "base.html" %}
{% set active_nav = "workouts" %}
{% block title %}Log: {{ day.label }}{% endblock %}

{#- "27.5 kg · 3 × 8" / "30 min": what a past log of the same exercise recorded. -#}
{% macro last_text(l) -%}
  {%- if l.weight_value is not none %}{{ '%g' | format(l.weight_value) }} {{ l.weight_unit.value if l.weight_unit else 'lb' }}{% endif -%}
  {%- if l.sets and l.reps_value %} · {{ l.sets }} × {{ l.reps_value }}{% elif l.duration_min %} · {{ '%g' | format(l.duration_min) }} min{% endif -%}
{%- endmacro %}

{#- One exercise added on the day. `i` is its index (the literal "__I__" inside the row template, which workout-log.js
    replaces). Fields are named extra-N-field and always submit, blank or not; a blank exercise name drops the row. -#}
{% macro extra_row(i, el=None) %}
<tr data-extra-row>
  <td><input list="exercise-names" name="extra-{{ i }}-exercise" value="{{ el.name if el else '' }}" placeholder="Exercise" aria-label="Exercise" autocomplete="off"></td>
  <td>
    <input type="number" step="0.1" min="0" name="extra-{{ i }}-weight_value" value="{{ '%g' | format(el.weight_value) if el and el.weight_value is not none else '' }}" style="width: 5em" aria-label="Load">
    <select name="extra-{{ i }}-weight_unit" aria-label="Unit">
      {% for u in units %}<option value="{{ u.value }}"{% if el and el.weight_unit == u %} selected{% endif %}>{{ u.label }}</option>{% endfor %}
    </select>
  </td>
  <td><input type="number" min="1" max="999" step="1" name="extra-{{ i }}-sets_value" value="{{ el.sets if el and el.sets else '' }}" style="width: 4em" aria-label="Sets"></td>
  <td><input type="number" min="1" max="999" step="1" name="extra-{{ i }}-reps_value" value="{{ el.reps_value if el and el.reps_value else '' }}" style="width: 4em" aria-label="Reps per set"></td>
  <td>
    <input type="number" min="0.1" step="0.5" name="extra-{{ i }}-minutes_value" value="{{ '%g' | format(el.duration_min) if el and el.duration_min else '' }}" style="width: 4.5em" placeholder="min" aria-label="Minutes (cardio)">
    <input type="number" min="0.1" step="0.1" name="extra-{{ i }}-speed_value" value="{{ '%g' | format(el.speed_mph) if el and el.speed_mph else '' }}" style="width: 4.5em" placeholder="mph" aria-label="Speed in mph">
    <input type="number" min="0" step="0.5" name="extra-{{ i }}-grade_value" value="{{ '%g' | format(el.grade_pct) if el and el.grade_pct else '' }}" style="width: 4.5em" placeholder="% grade" aria-label="Incline grade">
  </td>
  <td class="log-estimate">{% if el and el.net_kcal %}≈ {{ el.net_kcal | round | int }} kcal{% elif el and el.kcal_note %}<span class="muted small">{{ el.kcal_note }}</span>{% endif %}</td>
  <td><button type="button" class="btn btn-ghost" data-action="remove-extra" title="Remove">&times;</button></td>
</tr>
{% endmacro %}

{% block content %}
<div class="page-head"><div><h1>{{ day.label }}</h1><p class="muted">{{ log_date }}</p></div></div>

<form method="post" action="/workouts/day/{{ day.id }}/log" id="workout-log-form">
  <input type="hidden" name="log_date" value="{{ log_date }}">

  <label class="field body-weight">
    <span>Body weight (lb)</span>
    <input type="number" step="0.1" min="1" name="body_weight_lb" value="{{ '%g' | format(body_weight) if body_weight else '' }}" style="width: 7em">
    <small class="muted">Used for the calorie estimate.{% if not body_weight %} Add a weigh-in under <a href="/measurements">Measurements</a>, or type it here.{% endif %}</small>
  </label>
  {% if has_estimate %}
  <p class="burn-total">This workout burned about <strong>{{ net_kcal | round | int }} kcal</strong> net ({{ gross_kcal | round | int }} gross), estimated from your sets, reps and body weight.</p>
  {% endif %}

  <table class="lib-table log-table">
    <thead><tr><th>Done</th><th>Exercise</th><th>Load</th><th>Sets</th><th>Reps per set</th><th>Estimate</th></tr></thead>
    <tbody>
      {% for r in rows %}
      {% set ex = r.ex %}{% set db = r.db %}{% set p = r.prior %}{% set sh = r.shown %}
      <tr>
        <td><input type="checkbox" name="completed[{{ ex.id }}]"{% if p and p.completed %} checked{% endif %}></td>
        <td>
          <strong>{{ ex.name }}</strong>
          {% if db and db.name != ex.name %}<span class="tag tag-plain" title="{{ db.evidence }}">{{ db.name }}</span>{% endif %}
          {% if not db %}<div class="muted small">No match in the exercise database, so no calorie estimate.</div>{% endif %}
          <div class="muted small">Plan: {{ ex.sets_text or '—' }} × {{ ex.reps_text or '—' }}</div>
          {% if r.last %}<div class="muted small">Last time: {{ last_text(r.last) }}</div>{% endif %}
          {% if db and not db.is_duration %}
          <details class="log-adjust">
            <summary>Adjust</summary>
            <label>Style
              <select name="style_value[{{ ex.id }}]">
                {% for s in styles %}<option value="{{ s }}"{% if s == sh.style %} selected{% endif %}>{{ s }}</option>{% endfor %}
              </select>
            </label>
            <label>Implements <input type="number" min="1" max="9" step="1" name="implements_value[{{ ex.id }}]" value="{{ sh.implements }}" style="width: 3.5em"></label>
            <span class="muted small">Dumbbells entered per hand: use 2 so volume counts both.</span>
          </details>
          {% endif %}
        </td>
        <td>
          <input type="number" step="0.1" min="0" name="weight_value[{{ ex.id }}]" value="{{ '%g' | format(sh.weight_value) if sh.weight_value is not none else '' }}" style="width: 5em">
          <select name="weight_unit[{{ ex.id }}]">
            {% for u in units %}<option value="{{ u.value }}"{% if sh.weight_unit == u %} selected{% endif %}>{{ u.label }}</option>{% endfor %}
          </select>
        </td>
        {% if db and db.is_duration %}
        <td colspan="2">
          <label class="log-inline">Minutes <input type="number" min="0.1" step="0.5" name="minutes_value[{{ ex.id }}]" value="{{ '%g' | format(sh.minutes) if sh.minutes else '' }}" style="width: 5em"></label>
          {% if r.speed_basis == 'speed_mph' %}
          <label class="log-inline">Speed (mph) <input type="number" min="0.1" step="0.1" name="speed_value[{{ ex.id }}]" value="{{ '%g' | format(sh.speed) if sh.speed else '' }}" style="width: 5em"></label>
          {% elif r.speed_basis == 'grade_pct' %}
          <label class="log-inline">Incline (% grade) <input type="number" min="1" step="0.5" name="grade_value[{{ ex.id }}]" value="{{ '%g' | format(sh.grade) if sh.grade else '' }}" style="width: 5em"></label>
          {% endif %}
        </td>
        {% else %}
        <td><input type="number" min="1" max="999" step="1" name="sets_value[{{ ex.id }}]" value="{{ sh.sets if sh.sets else '' }}" style="width: 4em"></td>
        <td><input type="number" min="1" max="999" step="1" name="reps_value[{{ ex.id }}]" value="{{ sh.reps if sh.reps is not none else '' }}" style="width: 4em"></td>
        {% endif %}
        <td class="log-estimate">
          {% if p and p.net_kcal %}≈ {{ p.net_kcal | round | int }} kcal<small class="muted"> net, estimated</small>
            {% if p.compendium_code %}<div class="muted small">Compendium {{ p.compendium_code }}</div>{% endif %}
          {% elif p and p.completed and p.kcal_note %}<span class="muted small">{{ p.kcal_note }}</span>
          {% else %}<span class="muted small">—</span>{% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <h2 class="section-title">Added today</h2>
  <p class="muted small">Add anything you did that is not on the plan. Pick it from the list (or type a common name) and enter its sets and reps, or minutes for cardio, to get its calorie estimate.</p>
  <table class="lib-table log-table">
    <thead><tr><th>Exercise</th><th>Load</th><th>Sets</th><th>Reps per set</th><th>Cardio: min / mph / % grade</th><th>Estimate</th><th></th></tr></thead>
    <tbody data-extra-rows>
      {% for el in extras %}{{ extra_row(loop.index0, el) }}{% endfor %}
    </tbody>
  </table>
  <button type="button" class="btn" data-action="add-extra">+ Add exercise</button>
  <datalist id="exercise-names">{% for n in exercise_names %}<option value="{{ n }}">{% endfor %}</datalist>
  <template id="extra-row-template">{{ extra_row("__I__") }}</template>

  <p><button type="submit" class="btn btn-primary">Save</button></p>
</form>
{% endblock %}

{% block scripts %}
<script src="{{ static_url('js/workout-log.js') }}" defer></script>
{% endblock %}
```

- [ ] **Step 4: Add the script and styles**

Create `app/static/js/workout-log.js`:

```js
// Workout log form: rows for exercises added on the day, from a <template> with the "__I__" index placeholder
// (same convention as the plan editor and the vendor contact rows). Nothing is stored in the browser.
(() => {
  const rows = document.querySelector("[data-extra-rows]");
  const template = document.getElementById("extra-row-template");
  const addButton = document.querySelector('[data-action="add-extra"]');
  if (!rows || !template || !addButton) return;
  let count = rows.querySelectorAll("[data-extra-row]").length;

  function wire(row) {
    row.querySelector('[data-action="remove-extra"]').addEventListener("click", () => row.remove());
  }

  rows.querySelectorAll("[data-extra-row]").forEach(wire);

  addButton.addEventListener("click", () => {
    const fragment = template.content.cloneNode(true);
    fragment.querySelectorAll("[name]").forEach((el) => {
      el.name = el.name.replace("__I__", String(count));
    });
    const row = fragment.querySelector("[data-extra-row]");
    count += 1;
    rows.appendChild(fragment);
    wire(row);
    row.querySelector("input").focus();
  });
})();
```

Append to `app/static/css/app.css`:

```css
/* Workout log form */
.body-weight { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-bottom: var(--space-3, 1rem); }
.burn-total { padding: .6rem .9rem; border-left: 4px solid var(--accent, #0d9488); background: var(--surface-2, rgba(13, 148, 136, .08)); border-radius: 6px; }
.log-table td { vertical-align: top; }
.log-adjust { margin-top: .35rem; font-size: .85rem; }
.log-adjust label, .log-inline { display: inline-flex; gap: .35rem; align-items: center; margin-right: .75rem; }
.log-estimate { white-space: nowrap; }
```

- [ ] **Step 5: Update the one older test whose premise changed**

In `tests/test_workouts.py::test_log_form_prefills_from_an_existing_log_for_that_date`, the final block asserted that another date shows a blank weight. A later date now offers last time's numbers (that is the feature). Replace the last four lines of the test (from the `# A different date has no log yet` comment) with:

```python
    # A different date has no log yet: nothing is ticked, and the last session's numbers are offered.
    r = client.get(f"/workouts/day/{day.id}/log", params={"log_date": "2026-01-09"})
    assert f'name="completed[{ex0.id}]" checked' not in r.text
    assert "Last time" in r.text
```

- [ ] **Step 6: Run the form tests, then the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_log_form.py tests/test_workout_logging.py tests/test_workouts.py -q -p no:warnings` then the full suite.
Expected: all pass (this closes Task 5's two red tests).

- [ ] **Step 7: Browser check (desktop and phone width, light and dark)**

Start a dev server on a scratch database (never the owner's `data/`): `AMIDE_DATA_DIR=<temp dir>` with the `.claude/launch.json` entry, register a test account, add a weigh-in, create a plan with "Bench Press", "Treadmill Run" and "Zorvex Lift", open the log form. Verify the inputs, the Adjust details, adding an exercise row, saving, the estimate and total after save, and no horizontal scroll at 375 px. Delete the scratch data afterwards.

- [ ] **Step 8: Commit**

```bash
git add app/templates/workouts/log.html app/static/js/workout-log.js app/static/css/app.css tests/test_workout_log_form.py tests/test_workouts.py
git commit -m "feat: workout log form with exact sets and reps, calorie estimates and added exercises

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Plan editor matching (auto-match, suggestions, confirm)

**Files:**
- Modify: `app/routers/workouts.py` (`_reconcile_exercises`, `workouts_edit`, new `workouts_match_exercise`), `app/templates/workouts/edit.html`, `app/static/css/app.css` (append)
- Test: `tests/test_workout_matching.py`

**Interfaces:**
- Consumes: `match_exercise`, `exercise_db.get/all_exercises`, Task 4 columns `WorkoutExercise.db_exercise/db_exercise_confirmed`.
- Produces: plan saves set `db_exercise` automatically for confident matches; `POST /workouts/exercises/{exercise_id}/match` with `suggested` or `db_exercise` sets a confirmed match (blank clears it and keeps it cleared); logging (Task 5) already prefers the stored match.

Rules: a plan exercise that is **renamed** or **new** is re-matched and unconfirmed; an unchanged **unconfirmed** one is re-matched on every save (so database updates are picked up); a **confirmed** one (including a confirmed "none") is left alone while its name is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workout_matching.py`:

```python
import re

import pytest
from sqlalchemy import select

from app.models import WorkoutExercise, WorkoutPlan
from test_workout_logging import post_log, weigh_in  # noqa: F401
from test_workouts import _form_from_plan, _plan_form


def make_plan(client, db, names, name):
    client.post("/workouts", data=_plan_form(**{
        "name": name, "exercise_name[0][]": names, "exercise_sets[0][]": ["3"] * len(names),
        "exercise_reps[0][]": ["8"] * len(names), "exercise_rest[0][]": [""] * len(names)}))
    db.expire_all()
    return db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == name))


def stored(db, plan):
    db.expire_all()
    return {ex.name: (ex.db_exercise, ex.db_exercise_confirmed) for ex in db.get(WorkoutPlan, plan.id).days[0].exercises}


def test_saving_a_plan_matches_confident_names_and_leaves_the_rest_open(client, db):
    plan = make_plan(client, db, ["Barbell Bench Press", "Squats", "Running", "Zorvex Lift"], "Match Save Plan")
    assert stored(db, plan) == {"Barbell Bench Press": ("Bench Press", False), "Squats": (None, False),
                                "Running": ("Treadmill Run", False), "Zorvex Lift": (None, False)}


def test_renaming_resets_the_match_and_unrelated_edits_keep_a_confirmed_one(client, db):
    plan = make_plan(client, db, ["Squats"], "Match Rename Plan")
    ex = plan.days[0].exercises[0]
    assert client.post(f"/workouts/exercises/{ex.id}/match", data={"suggested": "Back Squat"}, follow_redirects=False).status_code == 303
    assert stored(db, plan) == {"Squats": ("Back Squat", True)}
    form = _form_from_plan(plan)
    form["exercise_sets[0][]"] = ["5"]                                        # not a rename
    client.post(f"/workouts/{plan.id}", data=form)
    assert stored(db, plan) == {"Squats": ("Back Squat", True)}
    db.expire_all()
    form = _form_from_plan(db.get(WorkoutPlan, plan.id))
    form["exercise_name[0][]"] = ["Bench Press"]                              # a rename: matched afresh
    client.post(f"/workouts/{plan.id}", data=form)
    assert stored(db, plan) == {"Bench Press": ("Bench Press", False)}


def test_the_confirm_endpoint_accepts_typed_names_rejects_unknown_ones_and_clears_on_blank(client, db):
    plan = make_plan(client, db, ["Zorvex Lift"], "Match Confirm Plan")
    ex = plan.days[0].exercises[0]
    url = f"/workouts/exercises/{ex.id}/match"
    assert client.post(url, data={"db_exercise": "db incline press"}, follow_redirects=False).status_code == 303
    assert stored(db, plan) == {"Zorvex Lift": ("Dumbbell Incline Press", True)}
    assert client.post(url, data={"db_exercise": "no such exercise anywhere"}, follow_redirects=False).status_code == 422
    assert stored(db, plan) == {"Zorvex Lift": ("Dumbbell Incline Press", True)}
    assert client.post(url, data={"db_exercise": ""}, follow_redirects=False).status_code == 303
    assert stored(db, plan) == {"Zorvex Lift": (None, True)}                     # "no estimate", and it stays that way
    form = _form_from_plan(db.get(WorkoutPlan, plan.id))
    client.post(f"/workouts/{plan.id}", data=form)
    assert stored(db, plan) == {"Zorvex Lift": (None, True)}


def test_an_unknown_exercise_id_is_a_404(client):
    assert client.post("/workouts/exercises/999999/match", data={"db_exercise": "Bench Press"}).status_code == 404


def test_the_edit_page_shows_matches_and_offers_suggestions(client, db):
    plan = make_plan(client, db, ["Barbell Bench Press", "Squats"], "Match Page Plan")
    page = client.get(f"/workouts/{plan.id}/edit").text
    bench, squats = plan.days[0].exercises
    assert "Bench Press" in page and f'id="match-{bench.id}"' in page
    assert re.search(rf'form="match-{squats.id}"[^>]*name="suggested"[^>]*value="Back Squat"', page) or \
        re.search(rf'name="suggested"[^>]*value="Back Squat"[^>]*form="match-{squats.id}"', page)
    assert 'id="exercise-names"' in page


def test_the_log_uses_the_confirmed_match(client, db, weigh_in):
    plan = make_plan(client, db, ["Squats"], "Match Log Plan")
    ex = plan.days[0].exercises[0]
    client.post(f"/workouts/exercises/{ex.id}/match", data={"suggested": "Back Squat"})
    post_log(client, plan.days[0], **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "5"})
    from app.models import WorkoutExerciseLog
    db.expire_all()
    row = db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.name == "Squats"))
    assert row.db_exercise == "Back Squat" and row.net_kcal > 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_matching.py -q -p no:warnings`
Expected: FAIL (no `db_exercise` is ever set; no `/match` route).

- [ ] **Step 3: Match on save and add the endpoint**

In `app/routers/workouts.py` add `from app.workouts.exercise_match import match_exercise` and `from app.workouts import exercise_db`, and replace `_reconcile_exercises` with:

```python
def _reconcile_exercises(day: WorkoutPlanDay, exercises_data: list[dict]) -> None:
    """save_workout_plan's per-day counterpart: same update-by-id / create / remove rules. Each exercise is also matched
    to the exercise database (see app/workouts/exercise_match.py) unless a person already confirmed its match and has
    not renamed it; a renamed or new exercise starts unconfirmed."""
    existing = {ex.id: ex for ex in day.exercises if ex.id is not None}
    new_exercises = []
    for j, data in enumerate(exercises_data):
        ex = existing.pop(data.get("id"), None) or WorkoutExercise()
        renamed = ex.name != data["name"]
        ex.position = j
        ex.name = data["name"]
        ex.sets_text = data.get("sets_text")
        ex.reps_text = data.get("reps_text")
        ex.rest_text = data.get("rest_text")
        if renamed or not ex.db_exercise_confirmed:
            match = match_exercise(ex.name)
            ex.db_exercise = match.exercise.name if match.confident else None
            ex.db_exercise_confirmed = False
        new_exercises.append(ex)
    day.exercises = new_exercises  # any existing exercise not in this list is delete-orphaned
```

Add the lookup helper next to `_get_own_day`, the endpoint after `workouts_schedule`, and the edit-page context:

```python
def _get_own_exercise(session: Session, exercise_id: int, uid: int) -> WorkoutExercise:
    ex = session.get(WorkoutExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercise not found")
    day = session.get(WorkoutPlanDay, ex.day_id)
    if day is None or day.plan.owner_id != uid:
        raise HTTPException(404, "Exercise not found")
    return ex
```

```python
@router.post("/workouts/exercises/{exercise_id}/match")
async def workouts_match_exercise(exercise_id: int, request: Request, session: Session = Depends(get_session),
                                  uid: int = Depends(current_user_id)):
    """Confirms which database exercise a plan exercise is, so its calories are estimated from it. A suggestion button
    posts `suggested`; the text box posts `db_exercise` (a database name or a common name). Blank clears the match
    and keeps it cleared ("no estimate for this one")."""
    ex = _get_own_exercise(session, exercise_id, uid)
    raw = await request.form()
    typed = str(raw.get("suggested") or raw.get("db_exercise") or "").strip()
    if typed:
        match = match_exercise(typed)
        if not match.confident:
            raise HTTPException(422, f'"{typed}" is not in the exercise database.')
        ex.db_exercise = match.exercise.name
    else:
        ex.db_exercise = None
    ex.db_exercise_confirmed = True
    plan_id = session.get(WorkoutPlanDay, ex.day_id).plan_id
    session.commit()
    return RedirectResponse(f"/workouts/{plan_id}/edit", status_code=303)
```

In `workouts_edit` add to the context:

```python
        "suggestions": {ex.id: match_exercise(ex.name).suggestions
                        for day in plan.days for ex in day.exercises if not ex.db_exercise},
        "exercise_names": [e.name for e in exercise_db.all_exercises()],
```

- [ ] **Step 4: Show the match in the editor**

In `app/templates/workouts/edit.html`: add an `Estimator` header cell (`<th>Estimator</th>`) before the blank last header; add this cell to the `exercise_row` macro before the remove button cell (it uses `suggestions` from the page context; unsaved rows show a hint):

```jinja
  <td class="match-cell">
    {% if ex and ex.id %}
      {% if ex.db_exercise %}<span class="tag" title="Calories are estimated from this exercise">{{ ex.db_exercise }}</span>{% if ex.db_exercise_confirmed %} <span class="muted small">confirmed</span>{% endif %}
      {% else %}<span class="muted small">No estimate yet.</span>
        {% for s in suggestions.get(ex.id, ()) %}
        <button type="submit" form="match-{{ ex.id }}" name="suggested" value="{{ s.name }}" class="btn btn-ghost btn-small">Use {{ s.name }}</button>
        {% endfor %}
      {% endif %}
      <input list="exercise-names" form="match-{{ ex.id }}" name="db_exercise" placeholder="Pick another&hellip;" aria-label="Database exercise" autocomplete="off" style="width: 11em">
      <button type="submit" form="match-{{ ex.id }}" class="btn btn-ghost btn-small">Set</button>
    {% else %}<span class="muted small">Matched when saved.</span>{% endif %}
  </td>
```

Below the plan form (outside it, so forms never nest) add one small form per saved exercise and the shared datalist:

```jinja
{% if plan %}
{% for day in plan.days %}{% for ex in day.exercises %}
<form id="match-{{ ex.id }}" method="post" action="/workouts/exercises/{{ ex.id }}/match"></form>
{% endfor %}{% endfor %}
<datalist id="exercise-names">{% for n in exercise_names %}<option value="{{ n }}">{% endfor %}</datalist>
{% endif %}
```

Append to `app/static/css/app.css`: `.match-cell { min-width: 14rem; } .btn-small { padding: .15rem .5rem; font-size: .8rem; }`.

- [ ] **Step 5: Run the matching tests, the logging tests and the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_matching.py tests/test_workout_logging.py tests/test_workouts.py -q -p no:warnings` then the full suite.
Expected: all pass. (Edit-page tests in `tests/test_workouts.py` that count table cells or columns, if any fail, update them for the new column.)

- [ ] **Step 6: Commit**

```bash
git add app/routers/workouts.py app/templates/workouts/edit.html app/static/css/app.css tests/test_workout_matching.py
git commit -m "feat: match plan exercises to the exercise database, with suggestions and confirmation

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Journal: Log Workout button, workout-only days, kcal on workout lines

**Files:**
- Modify: `app/routers/journal.py` (`workouts_for`, `journal_tab_context`, new helper), `app/routers/workouts.py` (new `GET /workouts/log`), `app/templates/measurements/index.html` (button, dialog, workout line), `app/static/js/journal.js`, `app/static/css/app.css` (none needed)
- Test: `tests/test_journal_workouts.py`

**Interfaces:**
- Consumes: Task 4 snapshots, Task 5 estimates, existing `journal_tab_context`.
- Produces: `workouts_for` rows gain `net_kcal` (int or None) and `gross_kcal`; `journal_tab_context` adds `workout_day_options` (list of `{id, label, plan_name}`, active plan first) and `today_iso`; the Journal list also shows dates with a workout but no journal entry; `GET /workouts/log?plan_day_id=&log_date=` redirects to that day's log form.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_journal_workouts.py`:

```python
import html
from datetime import date

from sqlalchemy import select

from app.models import JournalEntry, User, WorkoutLog, WorkoutPlan
from app.routers.journal import workouts_for
from test_workout_logging import DAY, plan_with, post_log, weigh_in  # noqa: F401


def journal(client):
    return html.unescape(client.get("/measurements?tab=journal").text)


def test_the_journal_tab_has_a_log_workout_button_and_a_day_picker(client, db):
    plan = plan_with(client, db, ["Bench Press"], name="Journal Button Plan")
    text = journal(client)
    assert 'data-action="open-workout-log"' in text and "Log Workout" in text
    assert 'id="workout-log-dialog"' in text and f'<option value="{plan.days[0].id}">' in text
    assert 'name="log_date"' in text


def test_without_a_plan_the_dialog_points_to_creating_one(client, db):
    db.query(WorkoutPlan).delete()
    db.commit()
    text = journal(client)
    assert "no workout plan yet" in text.lower() and 'data-action="open-workout-log"' in text


def test_the_picker_redirects_to_that_days_log_form(client, db):
    plan = plan_with(client, db, ["Bench Press"], name="Journal Redirect Plan")
    day = plan.days[0]
    r = client.get("/workouts/log", params={"plan_day_id": day.id, "log_date": "2026-02-03"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/workouts/day/{day.id}/log?log_date=2026-02-03"
    assert client.get("/workouts/log", params={"plan_day_id": 999999}, follow_redirects=False).status_code == 404


def test_a_logged_workout_shows_in_the_journal_with_its_estimated_calories(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Journal Kcal Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    rows = workouts_for(db, plan.owner_id, date.fromisoformat(DAY))
    assert rows[0]["net_kcal"] > 0 and rows[0]["gross_kcal"] > rows[0]["net_kcal"]
    text = journal(client)
    assert f"about {rows[0]['net_kcal']} kcal" in text and "estimated" in text


def test_a_workout_with_no_journal_entry_still_appears_as_its_own_row(client, db, weigh_in):
    plan = plan_with(client, db, ["Push-up"], name="Journal Orphan Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "3", f"reps_value[{ex.id}]": "10"})
    assert db.scalar(select(JournalEntry).where(JournalEntry.entry_date == date.fromisoformat(DAY))) is None
    text = journal(client)
    assert "Feb 2, 2026" in text and day.label in text


def test_other_peoples_workouts_never_appear_in_my_journal(client, db):
    other = User(username="journalother", username_key="journalother", password_hash="x")
    db.add(other)
    db.commit()
    try:
        db.add(WorkoutLog(owner_id=other.id, plan_day_id=None, day_label="Secret Day", plan_name="Their Plan",
                          log_date=date(2026, 2, 4)))
        db.commit()
        assert "Secret Day" not in journal(client)
    finally:
        db.query(User).filter(User.id == other.id).delete()
        db.commit()


def test_a_workout_without_estimates_shows_no_kcal(client, db):
    plan = plan_with(client, db, ["Zorvex Lift"], name="Journal No Kcal Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, **{f"completed[{ex.id}]": "on"})
    row = workouts_for(db, plan.owner_id, date.fromisoformat(DAY))[0]
    assert row["net_kcal"] is None
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (no button, no `/workouts/log`, no kcal keys, workout-only dates missing).

- [ ] **Step 3: Journal data**

In `app/routers/journal.py` replace `workouts_for` and add the helper:

```python
def workouts_for(session: Session, owner_id: int, entry_date: date) -> list[dict]:
    """That owner's completed workouts on that date, with their estimated calories (None when nothing was estimated).
    Query-time only, same ownership-check reasoning as doses_for: for a shared entry, owner_id must be the entry's own
    owner, not the viewer -- the viewer's access is already gated by the sharing query."""
    from app.models import WorkoutLog  # deferred: avoid a module-load cycle with app.routers.workouts
    rows = session.scalars(
        select(WorkoutLog).where(WorkoutLog.owner_id == owner_id, WorkoutLog.log_date == entry_date)
    ).all()
    out = []
    for r in rows:
        net = sum(el.net_kcal for el in r.exercise_logs if el.net_kcal)
        gross = sum(el.gross_kcal for el in r.exercise_logs if el.gross_kcal)
        out.append({
            "label": r.day_label or (r.plan_day.label if r.plan_day else "Workout"),
            "completed_count": sum(1 for el in r.exercise_logs if el.completed),
            "total_count": len(r.exercise_logs),
            "net_kcal": round(net) if net else None,
            "gross_kcal": round(gross) if gross else None,
        })
    return out


def _workout_only_views(session: Session, uid: int, entry_dates: set[date]) -> list[dict]:
    """Rows for the viewer's own days that have a logged workout but no journal entry, so a workout logged from the
    Journal always appears there. Shaped like _entry_view's output."""
    from app.models import WorkoutLog
    days = {d for d in session.scalars(select(WorkoutLog.log_date).where(WorkoutLog.owner_id == uid))
            if d not in entry_dates}
    return [{
        "date": d, "mood": None, "energy": None, "sleep_quality": None, "side_effects": [],
        "side_effects_other": None, "notes": None, "owner_name": None, "quick_notes": [],
        "doses": doses_for(session, uid, d), "workouts": workouts_for(session, uid, d),
    } for d in days]


def _workout_day_options(session: Session, uid: int) -> list[dict]:
    """The viewer's plan days for the Log Workout picker, active plans first."""
    from app.models import WorkoutPlan, WorkoutPlanDay
    rows = session.execute(
        select(WorkoutPlanDay.id, WorkoutPlanDay.label, WorkoutPlan.name)
        .join(WorkoutPlan, WorkoutPlanDay.plan_id == WorkoutPlan.id)
        .where(WorkoutPlan.owner_id == uid)
        .order_by(WorkoutPlan.ended_on.is_(None).desc(), WorkoutPlan.created_at.desc(), WorkoutPlanDay.position)).all()
    return [{"id": r[0], "label": r[1], "plan_name": r[2]} for r in rows]
```

In `journal_tab_context`, after `views.sort(...)`'s preceding `views += [...]` lines and before the sort, add `views += _workout_only_views(session, viewer_uid, {e.entry_date for e in own_entries})`, and add to the returned dict:

```python
        "workout_day_options": _workout_day_options(session, viewer_uid),
        "today_iso": date.today().isoformat(),
```

- [ ] **Step 4: The redirect route**

In `app/routers/workouts.py` add (before the `/workouts/day/...` routes):

```python
@router.get("/workouts/log")
def workouts_log_picker(plan_day_id: int, log_date: date_type | None = None, session: Session = Depends(get_session),
                        uid: int = Depends(current_user_id)):
    """The Journal's Log Workout dialog posts here: validate the day is the person's own, then open its log form."""
    day = _get_own_day(session, plan_day_id, uid)
    target = f"/workouts/day/{day.id}/log"
    return RedirectResponse(f"{target}?log_date={log_date.isoformat()}" if log_date else target, status_code=303)
```

- [ ] **Step 5: Journal template**

In `app/templates/measurements/index.html`, in the journal section next to the New Entry button add (inside the same `<section>`):

```jinja
  <button type="button" class="btn" data-action="open-workout-log">Log Workout</button>
```

and after the `journal-dialog` closes add the dialog (same dialog classes as the entry dialog):

```jinja
<dialog id="workout-log-dialog" class="dialog">
  <form method="get" action="/workouts/log" class="settings-form">
    <header class="dialog-head">
      <h2>Log a workout</h2>
      <button type="button" class="btn btn-ghost btn-icon" data-action="close-workout-log" aria-label="Close">&times;</button>
    </header>
    {% if workout_day_options %}
    <label class="field"><span>Workout day</span>
      <select name="plan_day_id">
        {% for o in workout_day_options %}<option value="{{ o.id }}">{{ o.plan_name }} &middot; {{ o.label }}</option>{% endfor %}
      </select>
    </label>
    <label class="field"><span>Date</span><input type="date" name="log_date" value="{{ today_iso }}" max="{{ today_iso }}"></label>
    <p class="muted small">Next you enter each exercise's weight, sets and reps; the calories are estimated for you.</p>
    <footer class="dialog-foot">
      <button type="button" class="btn" data-action="close-workout-log">Cancel</button>
      <button type="submit" class="btn btn-primary">Continue</button>
    </footer>
    {% else %}
    <p>You have no workout plan yet. <a href="/workouts/new">Create one</a>, or import a PDF on the <a href="/workouts">Workouts</a> page.</p>
    {% endif %}
  </form>
</dialog>
```

Change the workout line (the `<li>{{ w.label }} — ...` inside `e.workouts`) to:

```jinja
              <li>{{ w.label }} — {{ w.completed_count }}/{{ w.total_count }} exercises completed{% if w.net_kcal %}, about {{ w.net_kcal }} kcal (net, estimated){% endif %}</li>
```

In `app/static/js/journal.js` extend the click handler (the dialog lookup must not bail out when only the new dialog exists, so keep the early return on the entry dialog and add a second small block at the end of the file):

```js
// Log Workout dialog (Journal tab): open/close wiring; the form is a plain GET that redirects to the day's log form.
(() => {
  const dialog = document.getElementById("workout-log-dialog");
  if (!dialog) return;
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    if (btn.dataset.action === "open-workout-log") dialog.showModal();
    else if (btn.dataset.action === "close-workout-log" && dialog.contains(btn)) dialog.close();
  });
})();
```

- [ ] **Step 6: Run the Journal tests, the whole suite, and a browser check**

Run: `.venv/Scripts/python.exe -m pytest tests/test_journal_workouts.py tests/test_journal.py tests/test_measurements_page.py -q -p no:warnings` then the full suite.
Expected: all pass. In the browser (scratch data): open Journal, click Log Workout, pick a day and a date, continue to the log form, save, and see the workout row with its kcal.

- [ ] **Step 7: Commit**

```bash
git add app/routers/journal.py app/routers/workouts.py app/templates/measurements/index.html app/static/js/journal.js tests/test_journal_workouts.py
git commit -m "feat: Log Workout on the Journal page, with calories on workout lines

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 9: TDEE module and the life-stage profile field

**Files:**
- Create: `app/measurements/tdee.py`, `migrations/versions/0035_user_life_stage.py`, `tests/test_tdee.py`, `tests/test_settings_life_stage.py`
- Modify: `app/models.py` (`User.life_stage`), `app/routers/settings.py` (`change_body_profile`, `_render` context), `app/templates/settings/settings.html` (Body profile form)

**Interfaces:**
- Produces (Task 11): `tdee.report(*, male, age, height_in, weight_lb, activity_factor, life_stage=None) -> Report` (frozen dataclass: `bmr, bmr_formulas, bmr_average, activity_factor, tdee_unadjusted, tdee, life_stage, life_stage_note, split, split_pct, bmi, bmi_category, lbm_kg, lbm_lb, min_calories, goals, levels, math`), `tdee.macro_cell(goal, carb, tdee) -> dict`, `tdee.MACRO_GRID`, `tdee.LIFE_STAGES` (key to `LifeStage(key, label, delta_kcal, bmr_share, floor_kcal, source)`), `tdee.round_half_up`, `tdee.bmi_category`; `User.life_stage: str | None`.

The module reproduces tdeecalculator.org: the tests below use numbers read from the site for 11 profiles. Known difference, stated in the module docstring and shown on the page: the site labels PCOS "-6% BMR" but applies no change; Amide applies the rule it states.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tdee.py`:

```python
"""The expected numbers below were read from tdeecalculator.org's own output for these profiles (numbers only)."""

import pytest

from app.measurements.tdee import LIFE_STAGES, MACRO_GRID, bmi_category, macro_cell, report, round_half_up

# (male, age, height_in, weight_lb, activity_factor, life_stage) -> the site's figures
MODERATE_MAN = dict(male=True, age=30, height_in=71, weight_lb=176, activity_factor=1.55)
LIGHT_WOMAN = dict(male=False, age=28, height_in=64, weight_lb=140, activity_factor=1.375)
SEDENTARY_PERI = dict(male=False, age=45, height_in=66, weight_lb=170, activity_factor=1.2, life_stage="perimenopause")
ATHLETE_MAN = dict(male=True, age=55, height_in=70, weight_lb=220, activity_factor=1.9)
BREASTFEEDING = dict(male=False, age=32, height_in=65, weight_lb=150, activity_factor=1.55, life_stage="breastfeeding")


def test_moderate_man():
    r = report(**MODERATE_MAN)
    assert (r.bmr, r.tdee_unadjusted, r.tdee) == (1780, 2760, 2760)
    assert [f[1] for f in r.bmr_formulas] == [1780, 1853, 1697, 1852] and r.bmr_average == 1796
    assert r.split == (1780, 704, 276) and r.split_pct == (64, 26, 10)
    assert (r.bmi, r.bmi_category, r.lbm_kg, r.lbm_lb, r.min_calories) == (24.5, "Normal", 61.4, 135.4, 1500)
    assert [g[2] for g in r.goals] == [1760, 2010, 2260, 2760, 3010, 3260]
    assert [lv[2] for lv in r.levels] == [2136, 2448, 2759, 3071, 3382]
    assert r.life_stage is None and r.life_stage_note == "No life-stage adjustment applied."


def test_light_woman():
    r = report(**LIGHT_WOMAN)
    assert (r.bmr, r.tdee) == (1350, 1856)
    assert [f[1] for f in r.bmr_formulas] == [1350, 1417, 1333, 1481] and r.bmr_average == 1395
    assert r.split == (1350, 320, 186) and r.split_pct == (73, 17, 10)
    assert (r.bmi, r.bmi_category, r.lbm_kg, r.lbm_lb, r.min_calories) == (24.0, "Normal", 44.6, 98.3, 1200)
    assert [g[2] for g in r.goals] == [856, 1106, 1356, 1856, 2106, 2356]      # the ladder is not floored
    assert [lv[2] for lv in r.levels] == [1620, 1856, 2093, 2329, 2565]


def test_sedentary_perimenopause_clips_activity_at_zero():
    r = report(**SEDENTARY_PERI)
    assert (r.bmr, r.tdee_unadjusted, r.tdee) == (1433, 1719, 1544)
    assert [f[1] for f in r.bmr_formulas] == [1433, 1485, 1459, 1609] and r.bmr_average == 1497
    assert r.split == (1433, 0, 154) and r.split_pct == (90, 0, 10)
    assert (r.bmi, r.bmi_category, r.lbm_kg, r.lbm_lb) == (27.4, "Overweight", 50.4, 111.1)
    assert [lv[2] for lv in r.levels] == [1545, 1795, 2046, 2297, 2548]
    assert [g[2] for g in r.goals] == [544, 794, 1044, 1544, 1794, 2044]
    assert r.life_stage_note == "Perimenopause -175 kcal/day: adjusted TDEE 1,544 kcal/day."


def test_athlete_man():
    r = report(**ATHLETE_MAN)
    assert (r.bmr, r.tdee, r.split, r.split_pct) == (1839, 3494, (1839, 1306, 349), (53, 37, 10))
    assert [f[1] for f in r.bmr_formulas] == [1839, 1966, 1858, 2016] and r.bmr_average == 1920
    assert (r.bmi, r.bmi_category) == (31.6, "Obese Class I")
    assert [lv[2] for lv in r.levels] == [2207, 2529, 2850, 3172, 3494]


def test_breastfeeding_adds_400():
    r = report(**BREASTFEEDING)
    assert (r.bmr, r.tdee_unadjusted, r.tdee) == (1391, 2156, 2556)
    assert r.split == (1391, 909, 256) and r.split_pct == (54, 36, 10)
    assert [lv[2] for lv in r.levels] == [2069, 2313, 2556, 2799, 3043]
    assert (r.lbm_kg, r.lbm_lb) == (46.9, 103.4)


@pytest.mark.parametrize("stage,tdee,levels,pct", [
    ("luteal", 2006, [1770, 2006, 2243, 2479, 2715], (67, 23, 10)),
    ("pregnancy_1", 1856, [1620, 1856, 2093, 2329, 2565], (73, 17, 10)),
    ("pregnancy_2", 2196, [1960, 2196, 2433, 2669, 2905], (61, 29, 10)),
    ("pregnancy_3", 2308, [2072, 2308, 2545, 2781, 3017], (58, 31, 10)),
    ("breastfeeding", 2256, [2020, 2256, 2493, 2729, 2965], (60, 30, 10)),
    ("perimenopause", 1681, [1445, 1681, 1918, 2154, 2390], (80, 10, 10)),
])
def test_each_life_stage_against_the_site(stage, tdee, levels, pct):
    r = report(male=False, age=28, height_in=64, weight_lb=140, activity_factor=1.375, life_stage=stage)
    assert r.tdee == tdee and [lv[2] for lv in r.levels] == levels and r.split_pct == pct


def test_pcos_applies_the_stated_six_percent_of_bmr_unlike_the_site():
    r = report(male=False, age=28, height_in=64, weight_lb=140, activity_factor=1.375, life_stage="pcos")
    assert r.tdee_unadjusted == 1856 and r.tdee == round_half_up(1856.03 - 0.06 * 1350.03)


def test_life_stages_are_female_only_and_unknown_ones_are_ignored():
    man = report(**MODERATE_MAN, life_stage="luteal")
    assert man.tdee == 2760 and man.life_stage is None
    assert report(**{**LIGHT_WOMAN, "life_stage": "nonsense"}).tdee == 1856
    assert set(LIFE_STAGES) == {"luteal", "pregnancy_1", "pregnancy_2", "pregnancy_3", "breastfeeding",
                                "perimenopause", "pcos"}


def test_the_breastfeeding_floor_holds_for_a_small_person():
    r = report(male=False, age=60, height_in=58, weight_lb=95, activity_factor=1.2, life_stage="breastfeeding")
    assert r.tdee == 1800 and all(lv[2] >= 1800 for lv in r.levels)


@pytest.mark.parametrize("kwargs,bmi,category,bmr", [
    (dict(male=False, age=25, height_in=70, weight_lb=118, activity_factor=1.2), 16.9, "Underweight", 1360),
    (dict(male=True, age=40, height_in=66, weight_lb=205, activity_factor=1.2), 33.1, "Obese Class I", 1783),
    (dict(male=True, age=40, height_in=66, weight_lb=224, activity_factor=1.2), 36.2, "Obese Class II", 1869),
    (dict(male=True, age=40, height_in=66, weight_lb=265, activity_factor=1.2), 42.8, "Obese Class III", 2055),
])
def test_bmi_categories_and_bmr_against_the_site(kwargs, bmi, category, bmr):
    r = report(**kwargs)
    assert (r.bmi, r.bmi_category, r.bmr) == (bmi, category, bmr)


def test_bmi_category_boundaries():
    assert [bmi_category(x) for x in (18.49, 18.5, 24.99, 25.0, 29.99, 30.0, 34.99, 35.0, 39.99, 40.0)] == [
        "Underweight", "Normal", "Normal", "Overweight", "Overweight", "Obese Class I", "Obese Class I",
        "Obese Class II", "Obese Class II", "Obese Class III"]


def test_the_macro_grid_matches_the_sites_percentages_and_grams():
    assert {k: (v["delta"], v["low"], v["moderate"], v["high"]) for k, v in MACRO_GRID.items()} == {
        "cut": (-500, (40, 20, 40), (40, 30, 30), (35, 45, 20)),
        "maintain": (0, (30, 30, 40), (30, 40, 30), (25, 50, 25)),
        "bulk": (500, (30, 30, 40), (25, 50, 25), (20, 60, 20))}
    cell = macro_cell("maintain", "moderate", 2760)
    assert cell["grams"] == (207, 276, 92) and cell["per_meal"] == (52, 69, 23) and cell["kcal_per_meal"] == 690
    assert macro_cell("maintain", "moderate", 1856)["grams"] == (139, 186, 62)
    assert macro_cell("cut", "low", 2000)["calories"] == 1500
```

Create `tests/test_settings_life_stage.py`:

```python
import pytest

from app.models import User


@pytest.fixture
def reset_profile(client):
    yield
    client.post("/settings/body-profile", data={})


def saved(db, me):
    db.expire_all()
    return db.get(User, me).life_stage


def test_life_stage_saves_and_clears(client, db, me, reset_profile):
    assert client.post("/settings/body-profile", data={"sex": "Female", "life_stage": "luteal"},
                       follow_redirects=False).status_code == 303
    assert saved(db, me) == "luteal"
    client.post("/settings/body-profile", data={"sex": "Female"})
    assert saved(db, me) is None


def test_an_unknown_life_stage_is_refused(client, db, me, reset_profile):
    r = client.post("/settings/body-profile", data={"life_stage": "nonsense"}, follow_redirects=False)
    assert r.status_code == 422 and saved(db, me) is None


def test_the_settings_page_lists_every_stage_with_its_label(client, reset_profile):
    page = client.get("/settings").text
    for key in ("luteal", "pregnancy_1", "pregnancy_2", "pregnancy_3", "breastfeeding", "perimenopause", "pcos"):
        assert f'value="{key}"' in page
    assert "Perimenopause (-175)" in page and "name=\"life_stage\"" in page
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (`ModuleNotFoundError: app.measurements.tdee`; no `life_stage` field).

- [ ] **Step 3: Implement the TDEE module**

Create `app/measurements/tdee.py` (proven against all 20 test cases in a scratch copy first):

```python
"""A full TDEE breakdown: BMR by four formulas, TDEE by activity level, the BMR / activity / food-digestion split,
BMI, lean body mass, the goal ladder, the activity-level comparison and the macro grid, plus life-stage adjustments.

The numbers reproduce tdeecalculator.org (checked against its output for several profiles). Pure functions: numbers
in, numbers out, no database. The body-composition rows that need a body-fat percentage (fat body mass,
waist-to-height) are filled only when the caller has one.

Known difference: the site lists PCOS as "-6% BMR" but applies no change; here the stated rule is applied."""

import math
from dataclasses import dataclass

KG_PER_LB = 0.453592
CM_PER_IN = 2.54
TEF_SHARE = 0.10
ACTIVITY_FACTORS = (("Sedentary", 1.2), ("Light", 1.375), ("Moderate", 1.55), ("Heavy", 1.725), ("Athlete", 1.9))
GOAL_LADDER = (("Aggressive cut", -1000), ("Faster cut", -750), ("Cut", -500), ("Maintain", 0),
               ("Lean gain", 250), ("Bulk", 500))
SAFETY_FLOOR = {"male": 1500, "female": 1200}
MEALS = 4


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


@dataclass(frozen=True)
class LifeStage:
    key: str
    label: str
    delta_kcal: float          # added to TDEE
    bmr_share: float           # added as a share of BMR (PCOS), negative to subtract
    floor_kcal: int | None     # adjusted TDEE is never below this
    source: str | None


LIFE_STAGES = {s.key: s for s in (
    LifeStage("luteal", "Luteal phase (+150 kcal/day)", 150, 0, None, None),
    LifeStage("pregnancy_1", "Pregnancy, trimester 1 (+0)", 0, 0, None, None),
    LifeStage("pregnancy_2", "Pregnancy, trimester 2 (+340)", 340, 0, None, None),
    LifeStage("pregnancy_3", "Pregnancy, trimester 3 (+452)", 452, 0, None, None),
    LifeStage("breastfeeding", "Breastfeeding (+400, floor 1,800)", 400, 0, 1800, "WHO/IOM (2002), 330-500 kcal/day"),
    LifeStage("perimenopause", "Perimenopause (-175)", -175, 0, None, "Lovejoy et al., 2008 (Int J Obes)"),
    LifeStage("pcos", "PCOS (-6% BMR)", 0, -0.06, None, None),
)}

# Rows: goal, then (protein, carb, fat) percentages for low / moderate / high carb.
MACRO_GRID = {
    "cut": {"label": "Cut", "delta": -500, "low": (40, 20, 40), "moderate": (40, 30, 30), "high": (35, 45, 20)},
    "maintain": {"label": "Maintain", "delta": 0, "low": (30, 30, 40), "moderate": (30, 40, 30), "high": (25, 50, 25)},
    "bulk": {"label": "Bulk", "delta": 500, "low": (30, 30, 40), "moderate": (25, 50, 25), "high": (20, 60, 20)},
}
_KCAL_PER_GRAM = (4, 4, 9)


@dataclass(frozen=True)
class Report:
    bmr: int
    bmr_formulas: tuple[tuple[str, int, str], ...]     # (name, kcal, source)
    bmr_average: int
    activity_factor: float
    tdee_unadjusted: int
    tdee: int                                           # after the life-stage adjustment
    life_stage: LifeStage | None
    life_stage_note: str
    split: tuple[int, int, int]                         # BMR, activity, food digestion (kcal)
    split_pct: tuple[int, int, int]
    bmi: float
    bmi_category: str
    lbm_kg: float
    lbm_lb: float
    min_calories: int
    goals: tuple[tuple[str, int, int], ...]             # (label, delta, kcal)
    levels: tuple[tuple[str, float, int], ...]          # (label, factor, kcal)
    math: tuple[str, ...]


def bmi_category(bmi: float) -> str:
    for limit, name in ((18.5, "Underweight"), (25, "Normal"), (30, "Overweight"), (35, "Obese Class I"),
                        (40, "Obese Class II")):
        if bmi < limit:
            return name
    return "Obese Class III"


def lean_body_mass_kg(weight_kg: float, height_cm: float, male: bool) -> float:
    """Boer formula."""
    return 0.407 * weight_kg + 0.267 * height_cm - 19.2 if male else 0.252 * weight_kg + 0.473 * height_cm - 48.3


def adjust(tdee: float, stage: LifeStage | None, bmr: float) -> float:
    if stage is None:
        return tdee
    adjusted = tdee + stage.delta_kcal + stage.bmr_share * bmr
    return max(adjusted, stage.floor_kcal) if stage.floor_kcal else adjusted


def report(*, male: bool, age: int, height_in: float, weight_lb: float, activity_factor: float,
           life_stage: str | None = None) -> Report:
    kg, cm = weight_lb * KG_PER_LB, height_in * CM_PER_IN
    stage = LIFE_STAGES.get(life_stage or "") if not male else None   # life stages are female-only
    mifflin = 10 * kg + 6.25 * cm - 5 * age + (5 if male else -161)
    harris = (88.362 + 13.397 * kg + 4.799 * cm - 5.677 * age) if male else (447.593 + 9.247 * kg + 3.098 * cm - 4.330 * age)
    lbm = lean_body_mass_kg(kg, cm, male)
    katch, cunningham = 370 + 21.6 * lbm, 500 + 22 * lbm
    average = (mifflin + harris + katch + cunningham) / 4

    unadjusted = mifflin * activity_factor
    adjusted = adjust(unadjusted, stage, mifflin)
    bmr_r, tdee_r = round_half_up(mifflin), round_half_up(adjusted)
    tef_r = round_half_up(adjusted * TEF_SHARE)
    activity_r = max(0, tdee_r - bmr_r - tef_r)
    parts = (bmr_r, activity_r, tef_r)
    total = sum(parts)
    pct = tuple(round_half_up(p / total * 100) if total else 0 for p in parts)

    floor = SAFETY_FLOOR["male" if male else "female"]
    levels = tuple((name, factor, round_half_up(adjust(bmr_r * factor, stage, bmr_r))) for name, factor in ACTIVITY_FACTORS)
    goals = tuple((name, delta, round_half_up(adjusted + delta)) for name, delta in GOAL_LADDER)
    bmi = 703 * weight_lb / height_in ** 2
    note = "No life-stage adjustment applied."
    if stage is not None:
        change = round_half_up(tdee_r - unadjusted)
        note = f"{stage.label.split(' (')[0]} {change:+d} kcal/day: adjusted TDEE {tdee_r:,} kcal/day."
    sex_word = "MALE" if male else "FEMALE"
    lines = (
        f"BMR (Mifflin-St Jeor, {sex_word}) = 10 x {kg:.1f} + 6.25 x {cm:.0f} - 5 x {age} {'+ 5' if male else '- 161'} = {bmr_r}",
        f"TDEE = {bmr_r} x {activity_factor:g} = {round_half_up(unadjusted)}",
    )
    return Report(
        bmr=bmr_r,
        bmr_formulas=(("Mifflin-St Jeor", bmr_r, "Mifflin et al., 1990, PMID 2305711"),
                      ("Harris-Benedict (revised)", round_half_up(harris), "Roza and Shizgal, 1984, PMID 6741850"),
                      ("Katch-McArdle", round_half_up(katch), "Katch and McArdle"),
                      ("Cunningham", round_half_up(cunningham), "Cunningham, 1991, PMID 1985388")),
        bmr_average=round_half_up(average), activity_factor=activity_factor,
        tdee_unadjusted=round_half_up(unadjusted), tdee=tdee_r, life_stage=stage, life_stage_note=note,
        split=parts, split_pct=pct, bmi=round(bmi, 1), bmi_category=bmi_category(bmi),
        lbm_kg=round(lbm, 1), lbm_lb=round(round(lbm, 1) * 2.20462, 1),
        min_calories=floor, goals=goals, levels=levels, math=lines)


def macro_cell(goal: str, carb: str, tdee: int) -> dict:
    """Protein / carb / fat for one cell of the grid: percentages, grams and grams per meal at tdee + the goal's delta."""
    row = MACRO_GRID[goal]
    calories = tdee + row["delta"]
    pcts = row[carb]
    grams = [calories * p / 100 / k for p, k in zip(pcts, _KCAL_PER_GRAM)]
    return {"goal": goal, "carb": carb, "calories": calories, "percents": pcts,
            "grams": tuple(round_half_up(g) for g in grams),
            "per_meal": tuple(round_half_up(g / MEALS) for g in grams), "kcal_per_meal": round_half_up(calories / MEALS)}
```

- [ ] **Step 4: Add the profile field**

In `app/models.py` add to `User` after `diet_preset`: `life_stage: Mapped[str | None] = mapped_column(String(20))  # a key of app.measurements.tdee.LIFE_STAGES; women only`.

Create `migrations/versions/0035_user_life_stage.py`:

```python
"""users.life_stage: an optional life stage (luteal, pregnancy, breastfeeding, ...) that adjusts the TDEE estimate

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0035'
down_revision: Union[str, None] = '0034'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('life_stage', sa.String(20)))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('life_stage')
```

In `app/routers/settings.py`: import `from app.measurements.tdee import LIFE_STAGES`; add `"life_stages": LIFE_STAGES,` to the `_render` context dict; in `change_body_profile` after the `diet_preset = ...` line add:

```python
    life_stage = _raw("life_stage") or None
    if life_stage is not None and life_stage not in LIFE_STAGES:
        errors["life_stage"] = "Pick a value from the list."
```

and after `me.diet_preset = diet_preset` add `me.life_stage = life_stage`.

In `app/templates/settings/settings.html`, inside the Body profile `<div class="grid">`, after the Sex label add:

```jinja
          <label class="field {{ 'has-error' if errors.life_stage }}">
            <span>Life stage (optional, women)</span>
            <select name="life_stage">
              <option value="">None</option>
              {% for key, stage in life_stages.items() %}
              <option value="{{ key }}" {{ 'selected' if me.life_stage == key }}>{{ stage.label }}</option>
              {% endfor %}
            </select>
            {{ err('life_stage') }}
          </label>
```

- [ ] **Step 5: Run the tests and the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tdee.py tests/test_settings_life_stage.py -q -p no:warnings` then the full suite; `alembic heads` shows only `0035`; round trip `alembic downgrade -1` then `upgrade head` on a scratch database URL.
Expected: 20 + 3 tests pass; full suite green.

- [ ] **Step 6: Commit**

```bash
git add app/measurements/tdee.py migrations/versions/0035_user_life_stage.py app/models.py app/routers/settings.py app/templates/settings/settings.html tests/test_tdee.py tests/test_settings_life_stage.py
git commit -m "feat: TDEE report matching tdeecalculator.org, and a life-stage profile field

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Chart geometry and progress aggregation (pure)

**Files:**
- Create: `app/workouts/charts.py`, `app/workouts/progress.py`
- Test: `tests/test_workout_charts.py`

**Interfaces:**
- Produces (Tasks 11-12): `charts.nice_ceiling(value, ticks=4) -> float`; `charts.stacked_bars(labels, stacks, *, line=None, width=640, height=240, pad_l=52, pad_r=14, pad_t=14, pad_b=30, gap=0.25) -> dict | None` (keys `width, height, top, bars, line, y_ticks, x_ticks, plot`; each bar `{label, total, x, w, segments[{name, value, x, w, y, h}]}`); `charts.ring(slices, *, size=200, outer=90, inner=56) -> dict | None` (`size, total, slices[{name, value, share, path}]`); `progress.LoggedExercise(log_date, name, area, equipment, load_lb, sets, reps, volume_lb, net_kcal, gross_kcal)`, `SessionPoint`, `Record`, `RANGES`, `range_start(key, today)`, `in_range(rows, start, end)`, `exercises_by_recency(rows)`, `exercise_history(rows, name)`, `personal_records(rows)`, `week_start(day)`, `weekly_volume_by_area(rows) -> (weeks, {area: [volume per week]})`, `burn_by_equipment(rows)`, `top_exercises_by_kcal(rows, limit=10)`, `daily_net_kcal(rows)`.

Line charts (exercise progression) reuse `app.library.price_lists.chart.multi_series_chart`, whose value axis fits the data with margin.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workout_charts.py`:

```python
from datetime import date

import pytest

from app.workouts import progress
from app.workouts.charts import nice_ceiling, ring, stacked_bars
from app.workouts.progress import LoggedExercise as L

D = date


def row(day, name, *, area="Chest", equipment="Barbell", load=None, sets=None, reps=None, volume=None, net=None):
    return L(day, name, area, equipment, load, sets, reps, volume, net, None if net is None else net * 1.4)


@pytest.mark.parametrize("value,top", [(0, 4), (3, 3), (950, 1000), (1010, 1200), (2763, 3000), (320, 320), (87, 100), (5, 5)])
def test_nice_ceiling_gives_round_axis_maxima(value, top):
    assert nice_ceiling(value) == top


def test_stacked_bars_stack_in_order_and_start_at_zero():
    chart = stacked_bars([D(2026, 10, 1), D(2026, 10, 2)], {"BMR": [1800, 1800], "Workout": [300, 0]}, line=[2300, 2300])
    first, second = chart["bars"]
    assert [s["name"] for s in first["segments"]] == ["BMR", "Workout"] and [s["name"] for s in second["segments"]] == ["BMR"]
    bmr, workout = first["segments"]
    assert workout["y"] + workout["h"] == pytest.approx(bmr["y"], abs=0.2)           # the workout sits on the BMR segment
    assert bmr["y"] + bmr["h"] == pytest.approx(chart["plot"]["bottom"], abs=0.2)    # and the stack starts at the axis
    assert chart["top"] >= 2300 and chart["y_ticks"][0]["value"] == 0
    assert chart["line"]["points"][0]["value"] == 2300 and chart["line"]["poly"].count(",") == 2


def test_the_axis_top_covers_the_tallest_stack_or_the_target_line():
    low = stacked_bars([D(2026, 10, 1)], {"A": [100]}, line=[900])
    assert low["top"] >= 900
    assert stacked_bars([D(2026, 10, 1)], {"A": [100]})["line"] is None


def test_stacked_bars_with_no_data_draw_nothing():
    assert stacked_bars([], {"A": []}) is None and stacked_bars([D(2026, 10, 1)], {}) is None


def test_negative_values_never_draw_below_the_axis():
    chart = stacked_bars([D(2026, 10, 1)], {"A": [-5], "B": [10]})
    assert [s["name"] for s in chart["bars"][0]["segments"]] == ["B"] and chart["bars"][0]["total"] == 10


def test_a_ring_shares_sum_to_one_and_one_slice_is_still_drawable():
    chart = ring([("Barbell", 300), ("Dumbbell", 100), ("Cable", 0), ("Machine", None)])
    assert [s["name"] for s in chart["slices"]] == ["Barbell", "Dumbbell"]
    assert sum(s["share"] for s in chart["slices"]) == pytest.approx(1.0) and chart["total"] == 400
    assert all(s["path"].startswith("M ") and s["path"].endswith("Z") for s in chart["slices"])
    only = ring([("Barbell", 5)])
    assert only["slices"][0]["share"] == 1.0 and "A 90 90 0 1 1" in only["slices"][0]["path"]
    assert ring([]) is None and ring([("A", 0)]) is None


ROWS = [
    row(D(2026, 9, 1), "Bench Press", load=135, sets=3, reps=8, volume=3240, net=30),
    row(D(2026, 9, 1), "Bench Press", load=155, sets=2, reps=5, volume=1550, net=18),
    row(D(2026, 9, 8), "Bench Press", load=145, sets=3, reps=10, volume=4350, net=34),
    row(D(2026, 9, 9), "Back Squat", area="Quads/Glutes", load=185, sets=3, reps=5, volume=2775, net=40),
    row(D(2026, 9, 22), "Dumbbell Curl", area="Biceps", equipment="Dumbbell", load=30, sets=3, reps=12, volume=1080, net=12),
    row(D(2026, 9, 22), "Plank", area="Core", equipment="Bodyweight", net=9),
]


def test_exercise_history_has_one_point_per_day_with_the_top_load_and_total_volume():
    points = progress.exercise_history(ROWS, "Bench Press")
    assert [(p.log_date, p.top_load_lb, p.volume_lb, p.net_kcal) for p in points] == [
        (D(2026, 9, 1), 155, 4790, 48), (D(2026, 9, 8), 145, 4350, 34)]
    assert progress.exercise_history(ROWS, "Plank")[0].top_load_lb is None
    assert progress.exercise_history(ROWS, "Nothing") == []


def test_exercises_are_listed_most_recent_first():
    assert progress.exercises_by_recency(ROWS) == ["Dumbbell Curl", "Plank", "Back Squat", "Bench Press"]


def test_personal_records_per_exercise_with_dates():
    bench = next(r for r in progress.personal_records(ROWS) if r.name == "Bench Press")
    assert bench.heaviest == (155, D(2026, 9, 1)) and bench.most_reps == (10, D(2026, 9, 8))
    assert bench.biggest_volume == (4790, D(2026, 9, 1))
    plank = next(r for r in progress.personal_records(ROWS) if r.name == "Plank")
    assert (plank.heaviest, plank.most_reps, plank.biggest_volume) == (None, None, None)


def test_weekly_volume_by_area_includes_empty_weeks_and_orders_by_total():
    weeks, areas = progress.weekly_volume_by_area(ROWS)
    assert weeks[0] == D(2026, 8, 31) and weeks[-1] == D(2026, 9, 21) and len(weeks) == 4
    assert list(areas) == ["Chest", "Quads/Glutes", "Biceps"]
    assert areas["Chest"] == [4790, 4350, 0, 0] and areas["Biceps"] == [0, 0, 0, 1080]
    assert progress.weekly_volume_by_area([row(D(2026, 9, 1), "Plank", net=5)]) == ([], {})


def test_burn_breakdowns_and_daily_totals():
    assert progress.burn_by_equipment(ROWS) == [("Barbell", 122), ("Dumbbell", 12), ("Bodyweight", 9)]
    assert progress.top_exercises_by_kcal(ROWS, 2) == [("Bench Press", 82), ("Back Squat", 40)]
    assert progress.daily_net_kcal(ROWS)[D(2026, 9, 22)] == 21 and len(progress.daily_net_kcal(ROWS)) == 4
    assert progress.burn_by_equipment([row(D(2026, 9, 1), "X", equipment=None, net=7)]) == [("Other", 7)]


def test_ranges_start_where_the_label_says():
    today = D(2026, 10, 6)
    assert progress.range_start("30", today) == D(2026, 9, 7) and progress.range_start("all", today) is None
    assert progress.range_start("bogus", today) == D(2026, 7, 9)       # falls back to 90 days
    assert [r.name for r in progress.in_range(ROWS, D(2026, 9, 9), D(2026, 9, 22))] == [
        "Back Squat", "Dumbbell Curl", "Plank"]
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (`ModuleNotFoundError: app.workouts.charts`).

- [ ] **Step 3: Implement the chart geometry**

Create `app/workouts/charts.py`:

```python
"""Geometry for the workout charts: stacked bars (daily burn against TDEE, weekly volume by body area) and a ring
(share of burn by equipment). Pure: numbers in, pixel coordinates and SVG path data out; templates draw the SVG.
Bars start at zero, as stacked bars must; line charts (exercise progression) reuse the price chart's
`multi_series_chart`, whose value axis fits the data."""

import math
from datetime import date

_NICE = (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7.5, 8, 10)


def nice_ceiling(value: float, ticks: int = 4) -> float:
    """A round axis maximum at or above `value`, chosen so `ticks` equal steps land on round numbers."""
    if value <= 0:
        return float(ticks)
    raw_step = value / ticks
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for factor in _NICE:
        step = factor * magnitude
        if step * ticks >= value:
            return step * ticks
    return 10 * magnitude * ticks


def stacked_bars(labels: list[date | str], stacks: dict[str, list[float]], *, line: list[float | None] | None = None,
                 width: int = 640, height: int = 240, pad_l: int = 52, pad_r: int = 14, pad_t: int = 14,
                 pad_b: int = 30, gap: float = 0.25) -> dict | None:
    """One bar per label; `stacks` maps a segment name to one value per label, drawn bottom to top in the order
    given. `line` is an optional value per label drawn as a polyline (the goal target). None when there is nothing."""
    if not labels or not stacks:
        return None
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    totals = [sum(max(stack[i], 0) for stack in stacks.values()) for i in range(len(labels))]
    line_values = [v for v in (line or []) if v is not None]
    top = nice_ceiling(max([*totals, *line_values, 0]))
    slot = plot_w / len(labels)
    bar_w = max(slot * (1 - gap), 1.0)

    def y_of(v: float) -> float:
        return pad_t + plot_h - v / top * plot_h

    bars = []
    for i, label in enumerate(labels):
        x = pad_l + i * slot + (slot - bar_w) / 2
        base, segments = 0.0, []
        for name, values in stacks.items():
            value = max(values[i], 0)
            if value <= 0:
                continue
            segments.append({"name": name, "value": value, "x": round(x, 1), "w": round(bar_w, 1),
                             "y": round(y_of(base + value), 1), "h": round(value / top * plot_h, 1)})
            base += value
        bars.append({"label": label, "total": totals[i], "x": round(x, 1), "w": round(bar_w, 1), "segments": segments})
    line_points = []
    if line:
        for i, value in enumerate(line):
            if value is not None:
                line_points.append({"x": round(pad_l + i * slot + slot / 2, 1), "y": round(y_of(value), 1), "value": value})
    steps = 4
    last = len(labels) - 1
    x_ticks = sorted({0, last // 2, last})
    return {
        "width": width, "height": height, "top": top, "bars": bars,
        "line": {"points": line_points, "poly": " ".join(f"{p['x']},{p['y']}" for p in line_points)} if line_points else None,
        "y_ticks": [{"value": top * k / steps, "y": round(y_of(top * k / steps), 1)} for k in range(steps + 1)],
        "x_ticks": [{"label": labels[i], "x": round(pad_l + i * slot + slot / 2, 1)} for i in x_ticks],
        "plot": {"left": pad_l, "right": width - pad_r, "top": pad_t, "bottom": height - pad_b},
    }


def ring(slices: list[tuple[str, float]], *, size: int = 200, outer: float = 90, inner: float = 56) -> dict | None:
    """Donut geometry: each slice with its share, an SVG path for its arc and the angle midpoint for a label.
    Slices with no value are dropped; None when nothing is left."""
    kept = [(name, value) for name, value in slices if value and value > 0]
    total = sum(value for _, value in kept)
    if not kept:
        return None
    cx = cy = size / 2
    out, start = [], -math.pi / 2
    for name, value in kept:
        share = value / total
        sweep = min(share * 2 * math.pi, 2 * math.pi - 1e-4)   # a full circle is not drawable as one arc
        end = start + sweep
        large = 1 if sweep > math.pi else 0
        def point(r, a):
            return f"{cx + r * math.cos(a):.2f},{cy + r * math.sin(a):.2f}"
        path = (f"M {point(outer, start)} A {outer} {outer} 0 {large} 1 {point(outer, end)} "
                f"L {point(inner, end)} A {inner} {inner} 0 {large} 0 {point(inner, start)} Z")
        out.append({"name": name, "value": value, "share": share, "path": path})
        start = end
    return {"size": size, "total": total, "slices": out}
```

- [ ] **Step 4: Implement the aggregation**

Create `app/workouts/progress.py`:

```python
"""Aggregations over a person's logged exercises for the Progress tab: a single exercise's history, personal
records, weekly volume by body area, where the burn comes from, and the daily burn for the TDEE chart.

Pure functions over plain `LoggedExercise` rows (the router builds them from the stored snapshots), so changing a
plan or the exercise database never alters what these report."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

RANGES = {"30": 30, "90": 90, "365": 365, "all": None}


@dataclass(frozen=True)
class LoggedExercise:
    log_date: date
    name: str                    # the canonical exercise when matched, else the name as logged
    area: str | None
    equipment: str | None
    load_lb: float | None
    sets: int | None
    reps: int | None
    volume_lb: float | None
    net_kcal: float | None
    gross_kcal: float | None


@dataclass(frozen=True)
class SessionPoint:
    log_date: date
    top_load_lb: float | None
    volume_lb: float
    net_kcal: float


@dataclass(frozen=True)
class Record:
    name: str
    heaviest: tuple[float, date] | None
    most_reps: tuple[int, date] | None
    biggest_volume: tuple[float, date] | None


def range_start(key: str, today: date) -> date | None:
    days = RANGES.get(key, RANGES["90"])
    return today - timedelta(days=days - 1) if days else None


def in_range(rows: list[LoggedExercise], start: date | None, end: date) -> list[LoggedExercise]:
    return [r for r in rows if r.log_date <= end and (start is None or r.log_date >= start)]


def exercises_by_recency(rows: list[LoggedExercise]) -> list[str]:
    """Every exercise name logged, most recently used first (then A-Z)."""
    last: dict[str, date] = {}
    for r in rows:
        if r.name not in last or r.log_date > last[r.name]:
            last[r.name] = r.log_date
    return [n for n, _ in sorted(last.items(), key=lambda kv: (-kv[1].toordinal(), kv[0].casefold()))]


def exercise_history(rows: list[LoggedExercise], name: str) -> list[SessionPoint]:
    """One point per day this exercise was logged: the heaviest load, the total volume and the net kcal."""
    by_day: dict[date, list[LoggedExercise]] = defaultdict(list)
    for r in rows:
        if r.name == name:
            by_day[r.log_date].append(r)
    points = []
    for day in sorted(by_day):
        day_rows = by_day[day]
        loads = [r.load_lb for r in day_rows if r.load_lb]
        points.append(SessionPoint(day, max(loads) if loads else None,
                                   sum(r.volume_lb or 0 for r in day_rows), sum(r.net_kcal or 0 for r in day_rows)))
    return points


def personal_records(rows: list[LoggedExercise]) -> list[Record]:
    """Heaviest load, most reps in a set and biggest single-day volume per exercise, with the date each happened;
    ordered by name."""
    best: dict[str, dict] = defaultdict(dict)
    day_volume: dict[tuple[str, date], float] = defaultdict(float)
    for r in rows:
        slot = best[r.name]
        if r.load_lb and r.load_lb > slot.get("load", (0, None))[0]:
            slot["load"] = (r.load_lb, r.log_date)
        if r.reps and r.reps > slot.get("reps", (0, None))[0]:
            slot["reps"] = (r.reps, r.log_date)
        day_volume[(r.name, r.log_date)] += r.volume_lb or 0
    for (name, day), volume in day_volume.items():
        if volume > best[name].get("volume", (0, None))[0]:
            best[name]["volume"] = (volume, day)
    return [Record(n, s.get("load"), s.get("reps"), s.get("volume")) for n, s in sorted(best.items(), key=lambda kv: kv[0].casefold())]


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_volume_by_area(rows: list[LoggedExercise]) -> tuple[list[date], dict[str, list[float]]]:
    """Every Monday from the first logged week to the last (empty weeks included) and, per body area, that week's
    volume. Rows with no area or no volume are skipped."""
    usable = [r for r in rows if r.volume_lb and r.area]
    if not usable:
        return [], {}
    first, last = week_start(min(r.log_date for r in usable)), week_start(max(r.log_date for r in usable))
    weeks = [first + timedelta(weeks=i) for i in range((last - first).days // 7 + 1)]
    index = {w: i for i, w in enumerate(weeks)}
    by_area: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(weeks))
    for r in usable:
        by_area[r.area][index[week_start(r.log_date)]] += r.volume_lb
    totals = {area: sum(v) for area, v in by_area.items()}
    return weeks, {area: by_area[area] for area in sorted(by_area, key=lambda a: (-totals[a], a))}


def burn_by_equipment(rows: list[LoggedExercise]) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.net_kcal:
            totals[r.equipment or "Other"] += r.net_kcal
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def top_exercises_by_kcal(rows: list[LoggedExercise], limit: int = 10) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.net_kcal:
            totals[r.name] += r.net_kcal
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:limit]


def daily_net_kcal(rows: list[LoggedExercise]) -> dict[date, float]:
    totals: dict[date, float] = defaultdict(float)
    for r in rows:
        if r.net_kcal:
            totals[r.log_date] += r.net_kcal
    return dict(totals)
```

- [ ] **Step 5: Run the tests and the whole suite**

Expected: 19 passed in the file; full suite green.

- [ ] **Step 6: Commit**

```bash
git add app/workouts/charts.py app/workouts/progress.py tests/test_workout_charts.py
git commit -m "feat: chart geometry and progress aggregation for workout history

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 11: The Energy tab (TDEE breakdown and the daily burn chart)

**Files:**
- Create: `app/routers/workout_insights.py`, `app/templates/workouts/_tabs.html`, `app/templates/workouts/energy.html`, `tests/test_workout_energy.py`
- Modify: `app/main.py` (register the router), `app/templates/workouts/list.html` (page tabs), `app/static/css/app.css` (append)

**Interfaces:**
- Consumes: `tdee.report/macro_cell/MACRO_GRID`, `charts.stacked_bars`, `progress.*`, `workout_logging.latest_body_weight`, `calculations.target_calories`, `body_fat_pct`, the `User` profile fields (`sex, birth_date, height_in, activity_level, macro_goal, life_stage`).
- Produces (Task 12): `workout_insights.router`, `workout_insights.load_logged(session, uid) -> list[progress.LoggedExercise]` (completed exercise logs of the owner, loads converted to lb), `workout_insights.color_map(names) -> dict[str, str]` (names to palette colors, cycling), `GET /workouts/energy?goal=cut|maintain|bulk&carb=low|moderate|high&range=7|30|90`, the `_tabs.html` partial (variable `active_tab`: `plans | energy | progress`).

The page shows, for the signed-in owner only: the TDEE headline and life-stage note; the BMR / activity / food-digestion split; the six-step goal ladder; the macro grid with the selected cell's grams; BMR by four formulas with sources; TDEE at every activity level (current highlighted); the advanced-metrics table; the math lines; and the daily stacked burn chart against TDEE. Missing profile data produces a plain prompt, never a calculation. Estimates are labeled as estimates.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workout_energy.py`:

```python
import html
import re
from datetime import date, timedelta

import pytest

from app.models import BodyMeasurement
from test_workout_logging import plan_with, post_log


@pytest.fixture
def profile(client, db, me):
    """Sets the test user's body profile and a single weigh-in; resets both afterwards."""
    def set_profile(sex="Male", age=30, height="71", activity="1.55", life_stage="", weight=176.0):
        today = date.today()
        birth = date(today.year - age, today.month, min(today.day, 28))
        client.post("/settings/body-profile", data={"sex": sex, "birth_date": birth.isoformat(), "height_in": height,
                                                    "activity_level": activity, "life_stage": life_stage})
        db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
        if weight:
            db.add(BodyMeasurement(owner_id=me, measured_at=today - timedelta(days=1), weight_lbs=weight))
        db.commit()
    yield set_profile
    client.post("/settings/body-profile", data={})
    db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
    db.commit()


def energy(client, **params):
    return html.unescape(client.get("/workouts/energy", params=params).text)


def test_the_energy_tab_is_linked_from_the_workouts_page(client):
    page = client.get("/workouts").text
    assert 'href="/workouts/energy"' in page and 'href="/workouts/progress"' in page


def test_missing_profile_data_is_named_instead_of_calculated(client, db, me, profile):
    profile(sex="", age=30, height="", activity="", weight=None)
    page = energy(client)
    assert "Missing:" in page and "sex" in page and "height" in page and "activity level" in page and "weigh-in" in page
    assert "BMR" not in page


def test_the_numbers_match_the_reference_calculator_for_a_moderate_man(client, profile):
    profile()                                                  # man, 30, 71 in, 176 lb, 1.55
    page = energy(client)
    for expected in ("2,760", "1,780", "24.5", "Normal", "135.4", "1,500"):
        assert expected in page
    ladder = re.findall(r"<td>([\d,]+) kcal</td>", page.split("Goal ladder", 1)[1].split("</table>", 1)[0])
    assert ladder == ["1,760", "2,010", "2,260", "2,760", "3,010", "3,260"]
    for formula, value in (("Mifflin-St Jeor", "1,780"), ("Harris-Benedict", "1,853"), ("Katch-McArdle", "1,697"),
                           ("Cunningham", "1,852")):
        assert re.search(rf"{formula}[^<]*</td>\s*<td[^>]*>{value}", page)
    assert "64%" in page and "26%" in page and "10%" in page          # BMR / activity / food digestion
    for level in ("2,136", "2,448", "2,759", "3,071", "3,382"):
        assert level in page


def test_a_woman_with_a_life_stage_gets_the_adjusted_tdee(client, profile):
    profile(sex="Female", age=45, height="66", activity="1.2", life_stage="perimenopause", weight=170.0)
    page = energy(client)
    assert "1,544" in page and "1,719" in page and "Perimenopause" in page and "1,200" in page


def test_pcos_says_it_applies_the_stated_six_percent(client, profile):
    profile(sex="Female", age=28, height="64", activity="1.375", life_stage="pcos", weight=140.0)
    page = energy(client)
    assert "-6%" in page and "applies no change" in page


def test_the_macro_grid_selection_changes_the_grams_and_bad_values_fall_back(client, profile):
    profile()
    default = energy(client)
    assert "207 g" in default and "276 g" in default and "92 g" in default           # maintain, moderate carb
    cut = energy(client, goal="cut", carb="low")                                      # 2,260 kcal at 40/20/40
    assert "226 g" in cut and "113 g" in cut and "100 g" in cut
    assert "207 g" in energy(client, goal="nonsense", carb="also nonsense")


def test_the_burn_chart_stacks_the_days_workout_on_the_tdee_baseline(client, db, profile):
    profile()
    plan = plan_with(client, db, ["Push-up"], name="Energy Chart Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, log_date=date.today().isoformat(),
             **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "4", f"reps_value[{ex.id}]": "15"})
    page = energy(client, range="7")
    assert 'class="price-chart wk-chart"' in page and page.count('class="wk-bar"') == 7
    assert re.search(r"workout about \d+ kcal net", page)
    for legend in ("BMR", "Activity", "Food digestion", "Workout", "Goal target"):
        assert legend in page


def test_a_period_without_workouts_still_draws_the_baseline_and_says_so(client, profile):
    profile()
    page = energy(client, range="30")
    assert page.count('class="wk-bar"') == 30 and "No workouts logged in this period" in page


def test_unknown_ranges_fall_back_to_thirty_days(client, profile):
    profile()
    assert energy(client, range="9999").count('class="wk-bar"') == 30
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_energy.py -q -p no:warnings`
Expected: FAIL (404: no `/workouts/energy`).

- [ ] **Step 3: The router**

Create `app/routers/workout_insights.py`:

```python
"""The Energy and Progress tabs of the Workouts page: a TDEE breakdown with a daily burn chart, and charts derived
from the logged workout history. Everything here is read-only and scoped to the signed-in owner."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.library.price_lists.chart import multi_series_chart
from app.library.price_lists.vendor_view import PALETTE
from app.measurements import tdee
from app.measurements.calculations import body_fat_pct, target_calories
from app.models import BiologicalSex, BodyMeasurement, MacroGoal, User, WeightUnit, WorkoutExerciseLog, WorkoutLog
from app.templating import templates
from app.workouts import charts, progress
from app.workouts import logging as workout_logging
from app.workouts.calories import LB_PER_KG

router = APIRouter()

WINDOWS = {"7": 7, "30": 30, "90": 90}
STACK_COLORS = {"BMR": "#0d9488", "Activity": "#2563eb", "Food digestion": "#ca8a04", "Workout": "#db2777"}
MACRO_COLORS = ("#2563eb", "#ca8a04", "#db2777")      # protein, carbs, fat


def color_map(names) -> dict[str, str]:
    """A stable color per name, in the order given, cycling the shared palette."""
    return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(names)}


def load_logged(session: Session, uid: int) -> list[progress.LoggedExercise]:
    """The owner's completed exercise logs, oldest first, from the stored snapshots (loads converted to pounds)."""
    rows = session.execute(
        select(WorkoutLog.log_date, WorkoutExerciseLog)
        .join(WorkoutExerciseLog, WorkoutExerciseLog.workout_log_id == WorkoutLog.id)
        .where(WorkoutLog.owner_id == uid, WorkoutExerciseLog.completed.is_(True))
        .order_by(WorkoutLog.log_date, WorkoutExerciseLog.id)).all()
    out = []
    for day, el in rows:
        load = None
        if el.weight_value:
            load = el.weight_value * (LB_PER_KG if el.weight_unit == WeightUnit.KG else 1)
        out.append(progress.LoggedExercise(
            day, el.db_exercise or el.name or "Exercise", el.area, el.equipment, load, el.sets, el.reps_value,
            el.volume_lb, el.net_kcal, el.gross_kcal))
    return out


def _age(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _body_composition(session: Session, uid: int, user: User, weight_lb: float) -> dict | None:
    """Fat mass and waist-to-height from the owner's latest tape measurements, when they have them."""
    m = session.scalar(
        select(BodyMeasurement).where(BodyMeasurement.owner_id == uid, BodyMeasurement.neck_in.is_not(None),
                                      BodyMeasurement.waist_in.is_not(None))
        .order_by(BodyMeasurement.measured_at.desc(), BodyMeasurement.id.desc()).limit(1))
    if m is None:
        return None
    fat = body_fat_pct(user.sex, user.height_in, m.neck_in, m.waist_in, m.hips_in)
    return {"body_fat_pct": round(fat, 1) if fat is not None else None,
            "fat_mass_lb": round(weight_lb * fat / 100, 1) if fat is not None else None,
            "waist_height": round(m.waist_in / user.height_in, 2)}


def _energy_context(session: Session, uid: int, goal: str, carb: str, range_key: str) -> dict:
    user = session.get(User, uid)
    weight = workout_logging.latest_body_weight(session, uid)
    missing = [label for label, value in (("sex", user.sex), ("birth date", user.birth_date),
                                          ("height", user.height_in), ("activity level", user.activity_level))
               if value is None]
    if weight is None:
        missing.append("a weigh-in")
    if missing:
        return {"status": "missing", "missing": missing}

    today = date.today()
    male = user.sex == BiologicalSex.MALE
    report = tdee.report(male=male, age=_age(user.birth_date, today), height_in=user.height_in, weight_lb=weight,
                         activity_factor=float(user.activity_level.value), life_stage=user.life_stage)
    goal = goal if goal in tdee.MACRO_GRID else "maintain"
    carb = carb if carb in ("low", "moderate", "high") else "moderate"
    top = max(level[2] for level in report.levels)
    level_bars = [{"label": name, "factor": factor, "kcal": kcal, "pct": round(kcal / top * 100),
                   "current": abs(factor - report.activity_factor) < 1e-9} for name, factor, kcal in report.levels]

    window = WINDOWS.get(range_key, 30)
    start = today - timedelta(days=window - 1)
    per_day = progress.daily_net_kcal(progress.in_range(load_logged(session, uid), start, today))
    days = [start + timedelta(days=i) for i in range(window)]
    bmr, activity, food = report.split
    target, _ = target_calories(report.tdee, user.macro_goal or MacroGoal.MAINTAIN, user.sex)
    chart = charts.stacked_bars(
        days, {"BMR": [bmr] * window, "Activity": [activity] * window, "Food digestion": [food] * window,
               "Workout": [per_day.get(d, 0) for d in days]}, line=[target] * window, width=720, height=260)
    for bar in chart["bars"]:
        burned = per_day.get(bar["label"], 0)
        bar["tip"] = (f"{bar['label']:%a %b %d}: TDEE {report.tdee:,} kcal; workout about {round(burned)} kcal net "
                      f"({burned / report.tdee * 100:.0f}% of TDEE, estimated)")
    return {
        "status": "ok", "r": report, "goal": goal, "carb": carb, "cell": tdee.macro_cell(goal, carb, report.tdee),
        "macro_grid": tdee.MACRO_GRID, "macro_colors": MACRO_COLORS, "level_bars": level_bars,
        "composition": _body_composition(session, uid, user, weight), "weight_lb": weight, "target": round(target),
        "chart": chart, "stack_colors": STACK_COLORS, "range": str(window), "ranges": list(WINDOWS),
        "workout_total": round(sum(per_day.values())), "workout_days": len(per_day),
        "pcos": report.life_stage is not None and report.life_stage.key == "pcos",
    }


@router.get("/workouts/energy")
def energy(request: Request, goal: str = "maintain", carb: str = "moderate",
           range_key: str = Query("30", alias="range"), session: Session = Depends(get_session),
           uid: int = Depends(current_user_id)):
    return templates.TemplateResponse(request, "workouts/energy.html",
                                      _energy_context(session, uid, goal, carb, range_key))
```

In `app/main.py` import `workout_insights` with the other routers and add `app.include_router(workout_insights.router)` after the workouts router.

- [ ] **Step 4: Tabs partial and the plans page header**

Create `app/templates/workouts/_tabs.html`:

```jinja
<nav class="wk-tabs" role="tablist" aria-label="Workout sections">
  <a href="/workouts" class="wk-tab {{ 'active' if active_tab == 'plans' }}" role="tab" aria-selected="{{ 'true' if active_tab == 'plans' else 'false' }}">Plans</a>
  <a href="/workouts/energy" class="wk-tab {{ 'active' if active_tab == 'energy' }}" role="tab" aria-selected="{{ 'true' if active_tab == 'energy' else 'false' }}">Energy</a>
  <a href="/workouts/progress" class="wk-tab {{ 'active' if active_tab == 'progress' }}" role="tab" aria-selected="{{ 'true' if active_tab == 'progress' else 'false' }}">Progress</a>
</nav>
```

In `app/templates/workouts/list.html`, directly under the `page-head` div add `{% with active_tab='plans' %}{% include "workouts/_tabs.html" %}{% endwith %}`.

- [ ] **Step 5: The Energy template**

Create `app/templates/workouts/energy.html`:

```jinja
{% extends "base.html" %}
{% set active_nav = "workouts" %}
{% block title %}Energy{% endblock %}

{% macro n(x) %}{{ "{:,}".format(x) }}{% endmacro %}

{% block content %}
<div class="page-head"><h1>Workouts</h1></div>
{% with active_tab='energy' %}{% include "workouts/_tabs.html" %}{% endwith %}

{% if status == 'missing' %}
<div class="empty">
  <p><strong>Add a few details to see your energy numbers.</strong></p>
  <p class="muted">Missing: {{ missing | join(', ') }}. Set your body profile in <a href="/settings#user">Settings</a> and log a weigh-in under <a href="/measurements">Measurements</a>.</p>
</div>
{% else %}
{% set total = r.split_pct %}

<section class="side-box">
  <div class="energy-hero">
    <div><div class="big">{{ n(r.tdee) }}</div><div class="muted">estimated calories burned per day (TDEE)</div></div>
    <div class="energy-stats">
      <div class="stat-block"><div class="label">BMR</div><strong>{{ n(r.bmr) }}</strong> <span class="muted small">kcal at rest</span></div>
      <div class="stat-block"><div class="label">BMI</div><strong>{{ r.bmi }}</strong> <span class="muted small">{{ r.bmi_category }}</span></div>
      <div class="stat-block"><div class="label">Lean body mass</div><strong>{{ r.lbm_kg }}</strong> <span class="muted small">kg ({{ r.lbm_lb }} lb)</span></div>
    </div>
  </div>
  <p class="muted small">{{ r.life_stage_note }}{% if pcos %} The reference calculator lists PCOS as -6% of BMR but applies no change; Amide applies the -6% it states.{% endif %}
    These are estimates: eat near your target for two to three weeks, track your weight, and adjust by about 100 kcal a day if it does not match.</p>

  <h2 class="section-title">Where it goes</h2>
  <div class="split-bar" role="img" aria-label="BMR {{ total[0] }}%, activity {{ total[1] }}%, food digestion {{ total[2] }}%">
    {% for name, pct in (('BMR', total[0]), ('Activity', total[1]), ('Food digestion', total[2])) %}
    {% if pct %}<span style="width: {{ pct }}%; background: {{ stack_colors[name] }}" title="{{ name }}: {{ pct }}%"></span>{% endif %}
    {% endfor %}
  </div>
  <ul class="price-legend">
    {% for name, kcal, pct in (('BMR', r.split[0], total[0]), ('Activity', r.split[1], total[1]), ('Food digestion', r.split[2], total[2])) %}
    <li><span class="swatch" style="background: {{ stack_colors[name] }}"></span>{{ name }} {{ n(kcal) }} kcal ({{ pct }}%)</li>
    {% endfor %}
  </ul>
</section>

<section class="side-box">
  <h2 class="section-title">Goal ladder</h2>
  <table class="lib-table">
    <thead><tr><th>Goal</th><th>Change</th><th>Calories per day</th></tr></thead>
    <tbody>
      {% for name, delta, kcal in r.goals %}
      <tr{% if delta == 0 %} class="current"{% endif %}><td>{{ name }}</td><td>{{ '%+d' | format(delta) if delta else '—' }}</td><td>{{ n(kcal) }} kcal</td></tr>
      {% endfor %}
    </tbody>
  </table>
  <p class="muted small">Your saved goal sets the dashed target line on the chart below ({{ n(target) }} kcal).</p>
</section>

<section class="side-box">
  <h2 class="section-title">Macros</h2>
  <div class="macro-grid" role="group" aria-label="Macro split by goal and carb level">
    <span></span><span class="muted small">Low carb</span><span class="muted small">Moderate</span><span class="muted small">High carb</span>
    {% for g, row in macro_grid.items() %}
    <span class="muted small">{{ row.label }} {{ '%+d' | format(row.delta) if row.delta else '' }}</span>
    {% for c in ('low', 'moderate', 'high') %}
    <a href="/workouts/energy?goal={{ g }}&carb={{ c }}&range={{ range }}" class="macro-cell{% if g == goal and c == carb %} selected{% endif %}" aria-current="{{ 'true' if g == goal and c == carb else 'false' }}">
      {{ row[c][0] }} / {{ row[c][1] }} / {{ row[c][2] }}<small class="muted"> P / C / F</small></a>
    {% endfor %}
    {% endfor %}
  </div>
  <div class="energy-stats macro-result">
    {% for label, i in (('Protein', 0), ('Carbs', 1), ('Fat', 2)) %}
    <div class="stat-block"><div class="label"><span class="swatch" style="background: {{ macro_colors[i] }}"></span>{{ label }}</div>
      <strong>{{ cell.grams[i] }} g</strong> <span class="muted small">{{ cell.percents[i] }}% of calories &middot; about {{ cell.per_meal[i] }} g per meal</span></div>
    {% endfor %}
    <div class="stat-block"><div class="label">Daily total</div><strong>{{ n(cell.calories) }} kcal</strong> <span class="muted small">across 4 meals, about {{ n(cell.kcal_per_meal) }} each</span></div>
  </div>
</section>

<section class="side-box">
  <h2 class="section-title">BMR by formula</h2>
  <table class="lib-table">
    <thead><tr><th>Formula</th><th>BMR</th><th>Source</th></tr></thead>
    <tbody>
      {% for name, kcal, source in r.bmr_formulas %}<tr><td>{{ name }}</td><td>{{ n(kcal) }}</td><td class="muted small">{{ source }}</td></tr>{% endfor %}
      <tr><td>Average of the four</td><td>{{ n(r.bmr_average) }}</td><td class="muted small"></td></tr>
    </tbody>
  </table>
</section>

<section class="side-box">
  <h2 class="section-title">TDEE by activity level</h2>
  <div class="level-bars">
    {% for b in level_bars %}
    <div class="level-bar{% if b.current %} current{% endif %}">
      <span>{{ b.label }}</span>
      <span class="track"><span class="fill" style="width: {{ b.pct }}%"></span></span>
      <strong>{{ n(b.kcal) }}</strong>
    </div>
    {% endfor %}
  </div>
  <p class="muted small">Your level is highlighted. Most people choose one level too high, which overstates TDEE by about 250 kcal.</p>
</section>

<section class="side-box">
  <h2 class="section-title">All metrics</h2>
  <table class="lib-table">
    <tbody>
      <tr><td>BMR</td><td>{{ n(r.bmr) }} kcal/day</td></tr>
      <tr><td>TDEE (unadjusted)</td><td>{{ n(r.tdee_unadjusted) }} kcal/day</td></tr>
      <tr><td>TDEE (adjusted)</td><td>{{ n(r.tdee) }} kcal/day</td></tr>
      <tr><td>BMI</td><td>{{ r.bmi }} &middot; {{ r.bmi_category }}</td></tr>
      <tr><td>Lean body mass</td><td>{{ r.lbm_kg }} kg ({{ r.lbm_lb }} lb)</td></tr>
      <tr><td>Fat body mass</td><td>{% if composition and composition.fat_mass_lb is not none %}{{ composition.fat_mass_lb }} lb ({{ composition.body_fat_pct }}%){% else %}<span class="muted">needs neck and waist measurements</span>{% endif %}</td></tr>
      <tr><td>Waist : height</td><td>{% if composition %}{{ composition.waist_height }} <span class="muted small">(ideal under 0.50)</span>{% else %}<span class="muted">needs a waist measurement</span>{% endif %}</td></tr>
      <tr><td>Max fat metabolism</td><td><span class="muted">not available</span></td></tr>
      <tr><td>Minimum daily calories</td><td>{{ n(r.min_calories) }} kcal/day <span class="muted small">safety floor</span></td></tr>
      <tr><td>Activity factor</td><td>{{ '%.3f' | format(r.activity_factor) }} &times;</td></tr>
    </tbody>
  </table>
  <h3 class="small muted">The math</h3>
  <pre class="math-lines">{% for line in r.math %}{{ line }}
{% endfor %}</pre>
</section>

<section class="side-box">
  <h2 class="section-title">Estimated workout burn against TDEE</h2>
  <div class="chips" role="group" aria-label="Range">
    {% for k in ranges %}<a class="tag{{ ' active' if k == range }}" href="/workouts/energy?goal={{ goal }}&carb={{ carb }}&range={{ k }}">{{ k }} days</a>{% endfor %}
  </div>
  <ul class="price-legend">
    {% for name, color in stack_colors.items() %}<li><span class="swatch" style="background: {{ color }}"></span>{{ name }}</li>{% endfor %}
    <li><span class="swatch dashed" style="background: #dc2626"></span>Goal target</li>
  </ul>
  <svg class="price-chart wk-chart" viewBox="0 0 {{ chart.width }} {{ chart.height }}" role="img" aria-label="Daily TDEE with the day's estimated workout burn stacked on top">
    {% for t in chart.y_ticks %}
    <line class="grid" x1="{{ chart.plot.left }}" x2="{{ chart.plot.right }}" y1="{{ t.y }}" y2="{{ t.y }}"></line>
    <text x="{{ chart.plot.left - 6 }}" y="{{ t.y + 4 }}" text-anchor="end">{{ n(t.value | round | int) }}</text>
    {% endfor %}
    {% for t in chart.x_ticks %}
    <text x="{{ t.x }}" y="{{ chart.plot.bottom + 16 }}" text-anchor="{{ 'start' if loop.first and not loop.last else ('end' if loop.last and not loop.first else 'middle') }}">{{ t.label | shortdate }}</text>
    {% endfor %}
    {% for bar in chart.bars %}
    <g class="wk-bar"><title>{{ bar.tip }}</title>
      {% for s in bar.segments %}<rect x="{{ s.x }}" y="{{ s.y }}" width="{{ s.w }}" height="{{ s.h }}" fill="{{ stack_colors[s.name] }}"></rect>{% endfor %}
    </g>
    {% endfor %}
    {% if chart.line %}<polyline class="wk-target" points="{{ chart.line.poly }}"></polyline>{% endif %}
  </svg>
  {% if workout_days %}
  <p class="muted small">{{ workout_days }} workout day{{ '' if workout_days == 1 else 's' }} in this period, about {{ n(workout_total) }} kcal net in total (estimated).</p>
  {% else %}
  <p class="muted small">No workouts logged in this period.</p>
  {% endif %}
  <p class="muted small">TDEE already includes your typical exercise, so the stack compares each day's logged workout with that baseline; it is not a strict total.</p>
</section>
{% endif %}
{% endblock %}
```

Append to `app/static/css/app.css`:

```css
/* Workouts: tabs, energy, progress */
.wk-tabs { display: flex; gap: .25rem; border-bottom: 1px solid var(--border); margin: 0 0 1rem; overflow-x: auto; }
.wk-tab { padding: .5rem .9rem; border-bottom: 3px solid transparent; color: var(--muted); text-decoration: none; white-space: nowrap; }
.wk-tab.active { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
.energy-hero { display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: flex-end; }
.energy-hero .big { font-size: 2.6rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.energy-stats { display: flex; flex-wrap: wrap; gap: 1.25rem; }
.stat-block .label { font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.macro-result { margin-top: .75rem; }
.split-bar { display: flex; height: 18px; border-radius: 9px; overflow: hidden; margin: .5rem 0; background: var(--border); }
.split-bar span { display: block; height: 100%; }
.level-bars { display: grid; gap: .45rem; }
.level-bar { display: grid; grid-template-columns: 5.5rem 1fr 4rem; gap: .5rem; align-items: center; }
.level-bar .track { display: block; height: 14px; border-radius: 7px; background: var(--border); overflow: hidden; }
.level-bar .fill { display: block; height: 100%; background: color-mix(in srgb, var(--accent) 45%, var(--border)); }
.level-bar.current .fill { background: var(--accent); }
.level-bar strong { text-align: right; font-variant-numeric: tabular-nums; }
.macro-grid { display: grid; grid-template-columns: auto repeat(3, minmax(0, 1fr)); gap: 6px; align-items: center; }
.macro-cell { display: block; padding: .5rem; border: 1px solid var(--border); border-radius: 8px; text-align: center; text-decoration: none; color: inherit; }
.macro-cell.selected { background: var(--accent-soft); border-color: var(--accent); font-weight: 600; }
.math-lines { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg); padding: .6rem .8rem; border-radius: 6px; }
tr.current td { font-weight: 600; background: color-mix(in srgb, var(--accent-soft) 40%, transparent); }
.wk-chart .wk-target { fill: none; stroke: #dc2626; stroke-width: 2; stroke-dasharray: 6 4; }
.wk-chart .wk-bar:hover rect { opacity: .85; }
.chips .tag.active { background: var(--accent); color: #fff; }
```

- [ ] **Step 6: Run the tests and the whole suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_workout_energy.py -q -p no:warnings` then the full suite.
Expected: all pass. (If the `Goal ladder` regex test fails on whitespace, adjust the template's markup, not the numbers.)

- [ ] **Step 7: Browser check**

Scratch database, profile set to the moderate-man numbers, a few logged workouts: verify the page at desktop and 375 px, light and dark; the macro grid links; range chips; the chart tooltips; compare the headline numbers with tdeecalculator.org for the same inputs. Delete the scratch data.

- [ ] **Step 8: Commit**

```bash
git add app/routers/workout_insights.py app/main.py app/templates/workouts/_tabs.html app/templates/workouts/energy.html app/templates/workouts/list.html app/static/css/app.css tests/test_workout_energy.py
git commit -m "feat: Energy tab with the full TDEE breakdown and a daily burn chart against TDEE

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The Progress tab (charts derived from the logged data)

**Files:**
- Create: `app/templates/workouts/progress.html`, `app/static/js/workout-progress.js`, `tests/test_workout_progress.py`
- Modify: `app/routers/workout_insights.py` (add the route and context), `app/static/css/app.css` (append)

**Interfaces:**
- Consumes: Task 10 (`charts`, `progress`), Task 11 (`load_logged`, `color_map`, `_tabs.html`), `app.library.price_lists.chart.multi_series_chart`.
- Produces: `GET /workouts/progress?exercise=NAME&range=30|90|365|all`.

The page: an exercise picker (every exercise ever logged, most recent first) with a top-load line chart and a total-volume line chart for the chosen exercise (hover shows the day's volume and estimated kcal); personal records for every exercise (all time); weekly volume stacked by body area (one color per area); a ring of net kcal by equipment; the ten exercises with the most estimated burn. All from stored snapshots, so editing a plan or the database never changes them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workout_progress.py`:

```python
import html
import re
from datetime import date, timedelta

from app.models import WeightUnit, WorkoutExerciseLog, WorkoutLog

TODAY = date.today()


def add_log(db, me, days_ago, *rows):
    """rows: (name, area, equipment, load_lb, sets, reps, volume, net_kcal)"""
    day = TODAY - timedelta(days=days_ago)
    log = WorkoutLog(owner_id=me, plan_day_id=None, day_label="Push", plan_name="Plan", log_date=day)
    for name, area, equipment, load, sets, reps, volume, net in rows:
        log.exercise_logs.append(WorkoutExerciseLog(
            name=name, db_exercise=name, area=area, equipment=equipment, completed=True, weight_value=load,
            weight_unit=WeightUnit.LB, sets=sets, reps_value=reps, volume_lb=volume, net_kcal=net, gross_kcal=net * 1.4 if net else None))
    db.add(log)
    db.commit()


def progress(client, **params):
    return html.unescape(client.get("/workouts/progress", params=params).text)


def test_with_no_history_the_page_explains_how_to_start(client, db):
    page = progress(client)
    assert "Log a workout" in page and "<svg" not in page


def test_the_picker_lists_exercises_most_recent_first_and_defaults_to_the_latest(client, db, me):
    add_log(db, me, 20, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    add_log(db, me, 5, ("Back Squat", "Quads/Glutes", "Barbell", 185, 3, 5, 2775, 40))
    page = progress(client)
    options = re.findall(r'<option value="([^"]+)"[^>]*>', page.split('id="progress-exercise"', 1)[1].split("</select>", 1)[0])
    assert options == ["Back Squat", "Bench Press"]
    assert 'value="Back Squat" selected' in page              # the default chart is for the latest exercise


def test_an_exercise_chart_has_a_point_per_session_with_tips(client, db, me):
    add_log(db, me, 30, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    add_log(db, me, 15, ("Bench Press", "Chest", "Barbell", 145, 3, 8, 3480, 32), ("Bench Press", "Chest", "Barbell", 155, 1, 3, 465, 6))
    page = progress(client, exercise="Bench Press")
    top = page.split("Top load", 1)[1].split("</svg>", 1)[0]
    assert top.count("<circle") == 2 and "155 lb" in top
    volume = page.split("Total volume", 1)[1].split("</svg>", 1)[0]
    assert "3,945 lb" in volume and "kcal" in volume


def test_records_are_all_time_even_when_the_range_is_short(client, db, me):
    add_log(db, me, 200, ("Bench Press", "Chest", "Barbell", 225, 1, 1, 225, 3))
    add_log(db, me, 3, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    page = progress(client, range="30")
    records = page.split("Personal records", 1)[1]
    assert "225 lb" in records and "Bench Press" in records


def test_weekly_volume_is_stacked_by_area_with_a_legend(client, db, me):
    add_log(db, me, 10, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30), ("Dumbbell Curl", "Biceps", "Dumbbell", 30, 3, 10, 900, 10))
    page = progress(client)
    weekly = page.split("Weekly volume by body area", 1)[1].split("</section>", 1)[0]
    assert "Chest" in weekly and "Biceps" in weekly and weekly.count("<rect") >= 2


def test_the_equipment_ring_and_top_exercises_list_the_burn(client, db, me):
    add_log(db, me, 4, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30), ("Dumbbell Curl", "Biceps", "Dumbbell", 30, 3, 10, 900, 10))
    page = progress(client)
    ring = page.split("Where the burn comes from", 1)[1]
    assert "Barbell" in ring and "Dumbbell" in ring and "<path" in ring
    assert "Bench Press" in ring and "30 kcal" in ring


def test_unknown_exercises_and_ranges_fall_back(client, db, me):
    add_log(db, me, 4, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    page = progress(client, exercise="No Such Exercise", range="nonsense")
    assert 'value="Bench Press" selected' in page and "<svg" in page


def test_the_range_narrows_the_weekly_chart(client, db, me):
    add_log(db, me, 120, ("Back Squat", "Quads/Glutes", "Barbell", 185, 3, 5, 2775, 40))
    add_log(db, me, 3, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    short = progress(client, range="30").split("Weekly volume by body area", 1)[1].split("</section>", 1)[0]
    everything = progress(client, range="all").split("Weekly volume by body area", 1)[1].split("</section>", 1)[0]
    assert "Quads/Glutes" not in short and "Quads/Glutes" in everything
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL (404 on `/workouts/progress`).

- [ ] **Step 3: The route and chart context**

Append to `app/routers/workout_insights.py`:

```python
PROGRESS_RANGES = ("30", "90", "365", "all")


def _line(points: list[tuple[date, float]], label: str, color: str, tips: list[str]) -> dict | None:
    """One-series line chart from the price chart's geometry, with a color and a tip per point."""
    chart = multi_series_chart({label: points})
    if chart is None:
        return None
    series = chart["series"][label]
    series["color"] = color
    for dot, tip in zip(series["points"], tips):
        dot["tip"] = tip
    return chart


def _progress_context(session: Session, uid: int, exercise: str, range_key: str) -> dict:
    today = date.today()
    range_key = range_key if range_key in PROGRESS_RANGES else "90"
    everything = load_logged(session, uid)
    names = progress.exercises_by_recency(everything)
    if not names:
        return {"empty": True, "range": range_key, "ranges": PROGRESS_RANGES}
    chosen = exercise if exercise in names else names[0]
    rows = progress.in_range(everything, progress.range_start(range_key, today), today)

    history = progress.exercise_history(rows, chosen)
    load_points = [(p.log_date, p.top_load_lb) for p in history if p.top_load_lb]
    load_tips = [f"{p.log_date:%b %d, %Y}: top load {p.top_load_lb:,.0f} lb; about {p.net_kcal:,.0f} kcal net (estimated)"
                 for p in history if p.top_load_lb]
    volume_points = [(p.log_date, p.volume_lb) for p in history if p.volume_lb]
    volume_tips = [f"{p.log_date:%b %d, %Y}: {p.volume_lb:,.0f} lb total volume; about {p.net_kcal:,.0f} kcal net (estimated)"
                   for p in history if p.volume_lb]

    weeks, by_area = progress.weekly_volume_by_area(rows)
    area_colors = color_map(by_area)
    weekly = charts.stacked_bars(weeks, by_area, width=720, height=260) if weeks else None
    if weekly:
        for bar in weekly["bars"]:
            index = weeks.index(bar["label"])
            parts = ", ".join(f"{area} {values[index]:,.0f} lb" for area, values in by_area.items() if values[index])
            bar["tip"] = f"Week of {bar['label']:%b %d}: {parts or 'no volume'}"

    equipment = progress.burn_by_equipment(rows)
    equipment_colors = color_map([name for name, _ in equipment])
    top = progress.top_exercises_by_kcal(rows)
    return {
        "empty": False, "names": names, "chosen": chosen, "range": range_key, "ranges": PROGRESS_RANGES,
        "load_chart": _line(load_points, "Top load (lb)", PALETTE[0], load_tips),
        "volume_chart": _line(volume_points, "Volume (lb)", PALETTE[2], volume_tips),
        "records": progress.personal_records(everything), "weekly": weekly, "area_colors": area_colors,
        "ring": charts.ring(equipment), "equipment_colors": equipment_colors,
        "top": [(name, kcal, round(kcal / top[0][1] * 100)) for name, kcal in top],
    }


@router.get("/workouts/progress")
def progress_tab(request: Request, exercise: str = "", range_key: str = Query("90", alias="range"),
                 session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return templates.TemplateResponse(request, "workouts/progress.html",
                                      _progress_context(session, uid, exercise, range_key))
```

- [ ] **Step 4: The template and script**

Create `app/templates/workouts/progress.html`:

```jinja
{% extends "base.html" %}
{% set active_nav = "workouts" %}
{% block title %}Progress{% endblock %}

{% macro n(x) %}{{ "{:,}".format(x) }}{% endmacro %}

{% macro line_chart(chart, title, fmt) %}
<svg class="price-chart wk-chart" viewBox="0 0 {{ chart.width }} {{ chart.height }}" role="img" aria-label="{{ title }}">
  {% for t in chart.y_ticks %}
  <line class="grid" x1="{{ chart.plot.left }}" x2="{{ chart.plot.right }}" y1="{{ t.y }}" y2="{{ t.y }}"></line>
  <text x="{{ chart.plot.left - 6 }}" y="{{ t.y + 4 }}" text-anchor="end">{{ fmt % t.value }}</text>
  {% endfor %}
  {% for t in chart.x_ticks %}
  <text x="{{ t.x }}" y="{{ chart.plot.bottom + 16 }}" text-anchor="{{ 'start' if loop.first and not loop.last else ('end' if loop.last and not loop.first else 'middle') }}">{{ t.date | shortdate }}</text>
  {% endfor %}
  {% for label, s in chart.series.items() %}
  <polyline points="{{ s.poly }}" fill="none" stroke="{{ s.color }}" stroke-width="2"></polyline>
  {% for pt in s.points %}<circle cx="{{ pt.x }}" cy="{{ pt.y }}" r="4" fill="{{ s.color }}"><title>{{ pt.tip }}</title></circle>{% endfor %}
  {% endfor %}
</svg>
{% endmacro %}

{% block content %}
<div class="page-head"><h1>Workouts</h1></div>
{% with active_tab='progress' %}{% include "workouts/_tabs.html" %}{% endwith %}

{% if empty %}
<div class="empty">
  <p><strong>Log a workout to see your progress.</strong></p>
  <p class="muted">Charts for each exercise, weekly volume by body area and where your burn comes from build up as you log sessions. Start from the Log Workout button on the Journal tab or a plan day on the Plans tab.</p>
</div>
{% else %}
<form method="get" action="/workouts/progress" class="progress-controls">
  <label class="field"><span>Exercise</span>
    <select name="exercise" id="progress-exercise" data-autosubmit>
      {% for name in names %}<option value="{{ name }}"{% if name == chosen %} selected{% endif %}>{{ name }}</option>{% endfor %}
    </select>
  </label>
  <input type="hidden" name="range" value="{{ range }}">
  <noscript><button type="submit" class="btn">Show</button></noscript>
  <div class="chips" role="group" aria-label="Range">
    {% for k in ranges %}<a class="tag{{ ' active' if k == range }}" href="/workouts/progress?exercise={{ chosen | urlencode }}&range={{ k }}">{{ 'All time' if k == 'all' else (k ~ ' days') }}</a>{% endfor %}
  </div>
</form>

<section class="side-box">
  <h2 class="section-title">{{ chosen }}</h2>
  {% if load_chart %}
  <h3 class="small muted">Top load</h3>
  {{ line_chart(load_chart, 'Top load of ' ~ chosen, '%.0f lb') }}
  {% endif %}
  {% if volume_chart %}
  <h3 class="small muted">Total volume per session</h3>
  {{ line_chart(volume_chart, 'Total volume of ' ~ chosen, '%.0f') }}
  {% endif %}
  {% if not load_chart and not volume_chart %}<p class="muted">No loads or volume logged for {{ chosen }} in this range.</p>{% endif %}
</section>

<section class="side-box">
  <h2 class="section-title">Weekly volume by body area</h2>
  {% if weekly %}
  <ul class="price-legend">{% for area, color in area_colors.items() %}<li><span class="swatch" style="background: {{ color }}"></span>{{ area }}</li>{% endfor %}</ul>
  <svg class="price-chart wk-chart" viewBox="0 0 {{ weekly.width }} {{ weekly.height }}" role="img" aria-label="Weekly training volume by body area">
    {% for t in weekly.y_ticks %}
    <line class="grid" x1="{{ weekly.plot.left }}" x2="{{ weekly.plot.right }}" y1="{{ t.y }}" y2="{{ t.y }}"></line>
    <text x="{{ weekly.plot.left - 6 }}" y="{{ t.y + 4 }}" text-anchor="end">{{ n(t.value | round | int) }}</text>
    {% endfor %}
    {% for t in weekly.x_ticks %}<text x="{{ t.x }}" y="{{ weekly.plot.bottom + 16 }}" text-anchor="middle">{{ t.label | shortdate }}</text>{% endfor %}
    {% for bar in weekly.bars %}
    <g class="wk-bar"><title>{{ bar.tip }}</title>
      {% for s in bar.segments %}<rect x="{{ s.x }}" y="{{ s.y }}" width="{{ s.w }}" height="{{ s.h }}" fill="{{ area_colors[s.name] }}"></rect>{% endfor %}
    </g>
    {% endfor %}
  </svg>
  <p class="muted small">Volume is load &times; reps &times; sets in pounds, by the body area the database gives each exercise.</p>
  {% else %}<p class="muted">No loaded sets in this range.</p>{% endif %}
</section>

<section class="side-box">
  <h2 class="section-title">Where the burn comes from</h2>
  {% if ring %}
  <div class="burn-split">
    <svg viewBox="0 0 {{ ring.size }} {{ ring.size }}" class="burn-ring" role="img" aria-label="Estimated net calories by equipment">
      {% for s in ring.slices %}<path d="{{ s.path }}" fill="{{ equipment_colors[s.name] }}"><title>{{ s.name }}: about {{ n(s.value | round | int) }} kcal ({{ (s.share * 100) | round | int }}%)</title></path>{% endfor %}
      <text x="{{ ring.size / 2 }}" y="{{ ring.size / 2 + 5 }}" text-anchor="middle" class="ring-total">{{ n(ring.total | round | int) }} kcal</text>
    </svg>
    <ul class="price-legend ring-legend">
      {% for s in ring.slices %}<li><span class="swatch" style="background: {{ equipment_colors[s.name] }}"></span>{{ s.name }} &middot; {{ n(s.value | round | int) }} kcal ({{ (s.share * 100) | round | int }}%)</li>{% endfor %}
    </ul>
  </div>
  <h3 class="small muted">Most estimated burn</h3>
  <ol class="hbars">
    {% for name, kcal, pct in top %}
    <li><span class="hbar-name">{{ name }}</span><span class="hbar-track"><span class="hbar-fill" style="width: {{ pct }}%"></span></span><strong>{{ n(kcal | round | int) }} kcal</strong></li>
    {% endfor %}
  </ol>
  <p class="muted small">Net calories above resting, estimated from the stored sets, reps and body weight.</p>
  {% else %}<p class="muted">No estimated calories in this range yet. Estimates need sets, reps and a body weight.</p>{% endif %}
</section>

<section class="side-box">
  <h2 class="section-title">Personal records</h2>
  <div class="table-wrap">
    <table class="inv-table">
      <thead><tr><th>Exercise</th><th>Heaviest load</th><th>Most reps in a set</th><th>Biggest session volume</th></tr></thead>
      <tbody>
        {% for r in records %}
        <tr>
          <td data-label="Exercise">{{ r.name }}</td>
          <td data-label="Heaviest load">{% if r.heaviest %}{{ '%g' | format(r.heaviest[0]) }} lb <span class="muted small">{{ r.heaviest[1] | shortdate }}</span>{% else %}—{% endif %}</td>
          <td data-label="Most reps in a set">{% if r.most_reps %}{{ r.most_reps[0] }} <span class="muted small">{{ r.most_reps[1] | shortdate }}</span>{% else %}—{% endif %}</td>
          <td data-label="Biggest session volume">{% if r.biggest_volume %}{{ n(r.biggest_volume[0] | round | int) }} lb <span class="muted small">{{ r.biggest_volume[1] | shortdate }}</span>{% else %}—{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
{% endif %}
{% endblock %}

{% block scripts %}
<script src="{{ static_url('js/workout-progress.js') }}" defer></script>
{% endblock %}
```

Create `app/static/js/workout-progress.js`:

```js
// Progress tab: changing the exercise picker re-loads the page for that exercise (no state is kept in the browser).
(() => {
  const select = document.querySelector("select[data-autosubmit]");
  if (select) select.addEventListener("change", () => select.form.submit());
})();
```

Append to `app/static/css/app.css`:

```css
.progress-controls { display: flex; flex-wrap: wrap; gap: 1rem; align-items: flex-end; margin-bottom: 1rem; }
.burn-split { display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: center; }
.burn-ring { width: 200px; height: 200px; flex: none; }
.burn-ring .ring-total { fill: var(--text); font-size: 13px; font-weight: 600; }
.ring-legend { flex-direction: column; }
.hbars { list-style: none; padding: 0; margin: .5rem 0; display: grid; gap: .4rem; }
.hbars li { display: grid; grid-template-columns: minmax(8rem, 14rem) 1fr 6rem; gap: .5rem; align-items: center; }
.hbar-track { display: block; height: 12px; border-radius: 6px; background: var(--border); overflow: hidden; }
.hbar-fill { display: block; height: 100%; background: var(--accent); }
.hbars strong { text-align: right; font-variant-numeric: tabular-nums; }
```

- [ ] **Step 5: Run the tests and the whole suite**

Expected: all pass. (The `Where the burn comes from` assertions rely on the ring `<title>` text; if the hbars list renders names in a different order than the test expects, fix the template, not the aggregation.)

- [ ] **Step 6: Browser check**

Scratch database with a few weeks of logged workouts across areas and equipment (use the log form, not hand-written rows, for at least some): verify the picker, both line charts, the weekly stacked chart and legend, the ring, the top-ten bars, records, desktop and 375 px, light and dark, and the empty state. Delete the scratch data.

- [ ] **Step 7: Commit**

```bash
git add app/routers/workout_insights.py app/templates/workouts/progress.html app/static/js/workout-progress.js app/static/css/app.css tests/test_workout_progress.py
git commit -m "feat: Progress tab with charts derived from logged workouts and the exercise database

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Roadmap, full verification and review

**Files:**
- Modify: `docs/ROADMAP.md`
- Test: the whole suite

- [ ] **Step 1: Roadmap**

Add a section under the Workouts/Phase notes of `docs/ROADMAP.md` (keep the file's existing style; no vendor or price-list data anywhere): built items (calorie estimates on every logged workout, Log Workout on the Journal, history that survives plan changes, the exercise database and its generator, the Energy tab, the Progress tab, the life-stage profile field); rules worth remembering (MET follows the style for rep-based exercises; walk/jog/run use the Compendium tables by speed and grade; reps and sets are exact whole numbers; calorie numbers are estimates); known differences (PCOS: the reference site applies no change, Amide applies -6% of BMR; max fat metabolism unavailable); and follow-ups (a body-weight-per-day TDEE history; an xlsx export of the workout log; OCR is unrelated).

- [ ] **Step 2: Full suite, migrations, scan**

Run the whole suite; `alembic heads` shows one head `0035`; on a scratch database run `alembic upgrade head`, `alembic downgrade 0033`, `alembic upgrade head`. Run the staged-diff vendor-name scan for the final commit and check that no tracked file contains a personal path (`git grep -n "Users\\\\"` should find nothing new).

- [ ] **Step 3: Independent review**

Dispatch one reviewer (most capable model) over `git log --oneline <base>..HEAD` with this plan, the spec and the Review Focus list. Fix every Critical and Important finding test-first, one pass; record Minor findings in the final message.

- [ ] **Step 4: Commit and report**

```bash
git add docs/ROADMAP.md
git commit -m "docs: roadmap notes for workout calories, TDEE and progress charts

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

Report: what was built, the rulings made (anything deviating from this plan), the deferred minors, and the two known differences from the reference calculator.

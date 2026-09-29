# Phase 7 (Exercise): Workout Plans + Fitness Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user import a workout PDF (or build one manually), schedule it onto real days of the week, log completion with weight/reps, see it surfaced on Today/Calendar/Journal, and separately run a standalone Fitness Test with trend charts.

**Architecture:** New SQLAlchemy models (`WorkoutPlan` → `WorkoutPlanDay` → `WorkoutExercise`, plus `WorkoutLog`/`WorkoutExerciseLog` and standalone `FitnessTestResult`) follow this codebase's existing patterns exactly (`Protocol`'s derived Active/Ended state, `save_protocol`'s clear-and-rebuild idiom, `doses_for`'s query-time Journal join, `measurements.py`'s `_chart()` geometry helper). A new `app/workouts/` package holds the PDF parser (pure function, no DB access, modeled on `app/library/sheet_parser.py`) and a `app/routers/workouts.py` holds all the routes.

**Tech Stack:** FastAPI + Jinja2 + SQLAlchemy/Alembic (existing stack). New dependency: `pypdf` (text-only PDF extraction).

**Spec:** docs/superpowers/specs/2026-09-29-exercise-phase7-design.md

## Global Constraints

- Journal never gets a stored relationship to workout data — `workouts_for(session, owner_id, date)` reads query-time, exactly like the existing `doses_for` in `app/routers/journal.py:46-55`.
- Only one `WorkoutPlan` may be Active (`ended_on is None`) at a time. Activating a new plan sets the previous Active plan's `ended_on` in the same transaction.
- The PDF parser (`app/workouts/pdf_parser.py`) never raises on an unrecognized format — worst case, zero days are returned and the upload still succeeds into an empty editable plan.
- No calorie-burn data anywhere in this build.
- Fitness Test and trend charts reuse `app.routers.measurements._chart(points, ...) -> dict | None` via a deferred import (the same pattern `app/routers/labs.py:91` already uses to avoid a module-load cycle) — never a second chart-geometry implementation.
- `WorkoutPlanDay.weekdays` reuses `ProtocolItem.weekdays`'s exact existing convention: a `String(7)` subset of `WEEKDAY_LETTERS = "MTWRFSU"` (`app/models.py:544-546`), Monday-first.
- New dependency `pypdf` goes in `requirements.txt` pinned to a specific version, matching every other entry in that file.

## Review Focus

1. A PDF with zero recognized day headers still creates a plan (0 days, editable), never a rejected upload. → Task 2.
2. A workout day already logged today must not reappear in "Workouts due today" on a second page load. → Task 6.
3. Saving a completion log with some exercises checked and some not, and weight/reps present on only some, must persist that exact partial state — never all-or-nothing. → Task 5.
4. Activating a second plan while one is already Active must end the first one in the same transaction, never leaving two Active at once. → Task 3.
5. The Fitness Test retest suggestion must be computed per-exercise from that exercise's own last `tested_at`, never a single shared "last tested" date across all four exercises. → Task 7.

---

### Task 1: Data model + migration

**Files:**
- Modify: `app/models.py` (add new classes near the end, after `LabPanel`/`LabResult`)
- Create: `migrations/versions/0024_workouts.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `WorkoutSource`, `WeightUnit`, `WorkoutPlan`, `WorkoutPlanDay`, `WorkoutExercise`, `WorkoutLog`, `WorkoutExerciseLog`, `FitnessTestExerciseName`, `FitnessTestResult` — all consumed by every later task.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_migrations.py` (open the file first to match its `_cfg`/`command` import style used by e.g. `test_0023_adds_sheet_sections_simple`):

```python
def test_0024_creates_workout_tables(tmp_path):
    db = tmp_path / "l.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0023")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")}
        for t in ("workout_plans", "workout_plan_days", "workout_exercises",
                  "workout_logs", "workout_exercise_logs", "fitness_test_results"):
            assert t in tables, t
        c.execute("insert into users(id, username, password_hash) values (1, 'tester', 'x')")
        c.execute(
            "insert into workout_plans(id, owner_id, name, source, started_on) "
            "values (1, 1, 'Test Plan', 'manual', '2026-01-01')")
        c.execute(
            "insert into workout_plan_days(id, plan_id, position, label) "
            "values (1, 1, 0, 'Day 1')")
        c.execute(
            "insert into workout_exercises(id, day_id, position, name) "
            "values (1, 1, 0, 'Push-up')")
        c.execute(
            "insert into workout_logs(id, owner_id, plan_day_id, log_date) "
            "values (1, 1, 1, '2026-01-08')")
        c.execute(
            "insert into workout_exercise_logs(id, workout_log_id, exercise_id, completed) "
            "values (1, 1, 1, 1)")
        c.execute(
            "insert into fitness_test_results(id, owner_id, exercise, value, tested_at) "
            "values (1, 1, 'max_pushups', 20, '2026-01-01')")
    command.downgrade(cfg, "0023")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")}
        for t in ("workout_plans", "workout_plan_days", "workout_exercises",
                  "workout_logs", "workout_exercise_logs", "fitness_test_results"):
            assert t not in tables, t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_migrations.py::test_0024_creates_workout_tables -v`
Expected: FAIL with `alembic.util.exc.CommandError` (no such revision `0023` -> `head` gap, or table missing) since neither the models nor the migration exist yet. (If `0023` isn't the current head by the time you run this, check `migrations/versions/` for the actual latest revision and adjust the `command.upgrade(cfg, "0023")` line and `down_revision` below to match — the exact prior revision id matters more than its number.)

- [ ] **Step 3: Add the models**

Add to `app/models.py`, after the last existing class:

```python
class WorkoutSource(str, enum.Enum):
    PDF = "pdf"
    MANUAL = "manual"


class WeightUnit(LabeledEnum):
    LB = ("lb", "lb")
    KG = ("kg", "kg")


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[WorkoutSource] = mapped_column(_enum_column(WorkoutSource))
    source_pdf_filename: Mapped[str | None] = mapped_column(String(100))
    started_on: Mapped[date] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    days: Mapped[list["WorkoutPlanDay"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", passive_deletes=True,
        order_by="WorkoutPlanDay.position")


class WorkoutPlanDay(Base):
    __tablename__ = "workout_plan_days"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("workout_plans.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))
    weekdays: Mapped[str | None] = mapped_column(String(7))  # subset of WEEKDAY_LETTERS, e.g. "MWF"

    plan: Mapped["WorkoutPlan"] = relationship(back_populates="days")
    exercises: Mapped[list["WorkoutExercise"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="WorkoutExercise.position")


class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_id: Mapped[int] = mapped_column(ForeignKey("workout_plan_days.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    sets_text: Mapped[str | None] = mapped_column(String(50))
    reps_text: Mapped[str | None] = mapped_column(String(50))
    rest_text: Mapped[str | None] = mapped_column(String(50))


class WorkoutLog(Base):
    """One completed instance of a WorkoutPlanDay, on a specific calendar date."""
    __tablename__ = "workout_logs"
    __table_args__ = (UniqueConstraint("plan_day_id", "log_date", name="uq_workout_log_day_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_day_id: Mapped[int] = mapped_column(ForeignKey("workout_plan_days.id", ondelete="CASCADE"))
    log_date: Mapped[date] = mapped_column(Date, index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    plan_day: Mapped["WorkoutPlanDay"] = relationship()
    exercise_logs: Mapped[list["WorkoutExerciseLog"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True)


class WorkoutExerciseLog(Base):
    __tablename__ = "workout_exercise_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_log_id: Mapped[int] = mapped_column(ForeignKey("workout_logs.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("workout_exercises.id", ondelete="CASCADE"))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    weight_value: Mapped[float | None] = mapped_column(Float)
    weight_unit: Mapped[WeightUnit | None] = mapped_column(_enum_column(WeightUnit))
    reps_value: Mapped[int | None] = mapped_column(Integer)


class FitnessTestExerciseName(str, enum.Enum):
    MAX_PUSHUPS = "max_pushups"
    MAX_SITUPS = "max_situps"
    MAX_BODYWEIGHT_SQUATS = "max_bodyweight_squats"
    PLANK_HOLD_SECONDS = "plank_hold_seconds"


class FitnessTestResult(Base):
    __tablename__ = "fitness_test_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exercise: Mapped[FitnessTestExerciseName] = mapped_column(_enum_column(FitnessTestExerciseName))
    value: Mapped[float] = mapped_column(Float)
    tested_at: Mapped[date] = mapped_column(Date, index=True)
```

Compute each new enum's `_enum_column` length by hand the same way every prior enum in this file was: `max(20, longest_member_value_length + 5)`. `WorkoutSource` values are `"pdf"`/`"manual"` (max len 6). `FitnessTestExerciseName` values are `"max_pushups"`/`"max_situps"`/`"max_bodyweight_squats"`/`"plank_hold_seconds"` (max len 21, from `"max_bodyweight_squats"`). `_enum_column` already computes this at import time from the enum's own members — you don't hardcode a length anywhere, just confirm by reading `_enum_column`'s definition (`app/models.py`, search for `def _enum_column`) that it does this automatically; no manual step needed beyond defining the members correctly above.

- [ ] **Step 4: Write the migration**

First find the actual current head revision:

```bash
py -c "import sys; sys.path.insert(0, '.'); from alembic.config import Config; from alembic.script import ScriptDirectory; s = ScriptDirectory.from_config(Config('alembic.ini')); print(s.get_current_head())"
```

Create `migrations/versions/0024_workouts.py` with `down_revision` set to whatever that command printed (the brief above assumes `'0023'`; adjust both this file's `down_revision` and Step 1's `command.upgrade(cfg, "0023")` line together if it's different):

```python
"""workouts: WorkoutPlan/Day/Exercise, WorkoutLog/ExerciseLog, FitnessTestResult

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0024'
down_revision: Union[str, None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workout_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('source', sa.Enum('pdf', 'manual', name='workoutsource', native_enum=False, length=20),
                  nullable=False),
        sa.Column('source_pdf_filename', sa.String(100)),
        sa.Column('started_on', sa.Date(), nullable=False),
        sa.Column('ended_on', sa.Date()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_workout_plans_owner_id', 'workout_plans', ['owner_id'])

    op.create_table(
        'workout_plan_days',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('plan_id', sa.Integer(), sa.ForeignKey('workout_plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('weekdays', sa.String(7)),
    )
    op.create_index('ix_workout_plan_days_plan_id', 'workout_plan_days', ['plan_id'])

    op.create_table(
        'workout_exercises',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('day_id', sa.Integer(), sa.ForeignKey('workout_plan_days.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('sets_text', sa.String(50)),
        sa.Column('reps_text', sa.String(50)),
        sa.Column('rest_text', sa.String(50)),
    )
    op.create_index('ix_workout_exercises_day_id', 'workout_exercises', ['day_id'])

    op.create_table(
        'workout_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('plan_day_id', sa.Integer(), sa.ForeignKey('workout_plan_days.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('log_date', sa.Date(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('plan_day_id', 'log_date', name='uq_workout_log_day_date'),
    )
    op.create_index('ix_workout_logs_owner_id', 'workout_logs', ['owner_id'])
    op.create_index('ix_workout_logs_log_date', 'workout_logs', ['log_date'])

    op.create_table(
        'workout_exercise_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workout_log_id', sa.Integer(), sa.ForeignKey('workout_logs.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('exercise_id', sa.Integer(), sa.ForeignKey('workout_exercises.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('weight_value', sa.Float()),
        sa.Column('weight_unit', sa.Enum('lb', 'kg', name='weightunit', native_enum=False, length=20)),
        sa.Column('reps_value', sa.Integer()),
    )
    op.create_index('ix_workout_exercise_logs_workout_log_id', 'workout_exercise_logs', ['workout_log_id'])

    op.create_table(
        'fitness_test_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('exercise', sa.Enum(
            'max_pushups', 'max_situps', 'max_bodyweight_squats', 'plank_hold_seconds',
            name='fitnesstestexercisename', native_enum=False, length=25), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('tested_at', sa.Date(), nullable=False),
    )
    op.create_index('ix_fitness_test_results_owner_id', 'fitness_test_results', ['owner_id'])
    op.create_index('ix_fitness_test_results_tested_at', 'fitness_test_results', ['tested_at'])


def downgrade() -> None:
    op.drop_table('fitness_test_results')
    op.drop_table('workout_exercise_logs')
    op.drop_table('workout_logs')
    op.drop_table('workout_exercises')
    op.drop_table('workout_plan_days')
    op.drop_table('workout_plans')
```

None of these are new columns on an *existing* table, so none of this needs the raw-SQL rebuild approach `0020`/`0016` used to preserve `peptides.name`'s `COLLATE NOCASE` — these are brand-new tables, plain `op.create_table` is safe.

- [ ] **Step 5: Run test to verify it passes**

Run: `py -m pytest tests/test_migrations.py::test_0024_creates_workout_tables -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/models.py migrations/versions/0024_workouts.py tests/test_migrations.py
git commit -m "feat: add Workout Plan and Fitness Test data model"
```

---

### Task 2: PDF text extraction + parsing

**Files:**
- Create: `app/workouts/__init__.py` (empty)
- Create: `app/workouts/pdf_parser.py`
- Test: `tests/test_workouts_pdf_parser.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `extract_text(pdf_bytes: bytes) -> str`, `parse_workout_pdf(text: str) -> dict` (shape:
  `{"name": str, "days": [{"label": str, "exercises": [{"name": str, "sets_text": str | None,
  "reps_text": str | None, "rest_text": str | None}, ...]}, ...]}`). Consumed by Task 4.

- [ ] **Step 1: Add the dependency**

Add to `requirements.txt` (matching the file's existing `package==version` style):

```
pypdf==5.1.0
```

Run: `pip install -r requirements.txt`
Expected: `pypdf` installs alongside the existing packages.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_workouts_pdf_parser.py`. Note on how these fixtures are built: a naive
attempt to draw PDF text with `pypdf`'s writer (no `/Font` resource registered on the page)
produces bytes that `extract_text` decodes back as garbage, not the original text — confirmed by
direct experiment while writing this plan. The helper below registers a real `/Font` resource
(standard Helvetica, WinAnsi encoding) before writing the content stream, which round-trips
correctly (also confirmed directly):

```python
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.workouts.pdf_parser import extract_text, parse_workout_pdf


def _pdf_bytes(text: str) -> bytes:
    """Builds a minimal real PDF whose extracted text is exactly `text` (one line per `\\n`-split
    line), for round-tripping extract_text/parse_workout_pdf without a binary fixture file
    checked into the repo. Registers a real Helvetica /Font resource -- without one, pypdf's own
    text extraction of a hand-built content stream comes back garbled, not the original text."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font[NameObject("/Encoding")] = NameObject("/WinAnsiEncoding")
    font_ref = writer._add_object(font)
    resources = DictionaryObject()
    font_dict = DictionaryObject()
    font_dict[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = font_dict
    page[NameObject("/Resources")] = resources

    ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for line in text.split("\n"):
        safe = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"({safe}) Tj")
        ops.append("0 -14 Td")
    ops.append("ET")
    content = DecodedStreamObject()
    content.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)

    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_text_round_trips_real_pdf_content():
    pdf_bytes = _pdf_bytes("Hello Workout Test")
    assert "Hello Workout Test" in extract_text(pdf_bytes)


# Real Muscle & Strength PDFs list every table ("Exercise Sets Reps[ Rest]" header + its rows)
# BEFORE any of the day/workout labels -- the labels appear later, in a separate "Workout
# Summary" block, in the same order as their tables but not adjacent to them. Confirmed directly
# against all 3 of the owner's sample PDFs while writing this plan. So the parser must find every
# table and every label independently, then zip them by position -- never by textual adjacency.

def test_parse_zips_tables_and_labels_by_position_workout_hash_style():
    text = (
        "Exercise Sets Reps Rest\n"
        "Dumbbell Bench Press 2 10 45 Sec\n"
        "Exercise Sets Reps Rest\n"
        "Goblet Squat 2 10 45 Sec\n"
        "Workout #1 - Upper Body Workout A\n"
        "Workout #2 - Lower Body Workout A\n"
    )
    result = parse_workout_pdf(text)
    assert len(result["days"]) == 2
    assert result["days"][0]["label"] == "Upper Body Workout A"
    assert result["days"][1]["label"] == "Lower Body Workout A"
    ex = result["days"][0]["exercises"][0]
    assert ex["name"] == "Dumbbell Bench Press"
    assert ex["sets_text"] == "2" and ex["reps_text"] == "10" and ex["rest_text"] == "45 Sec"


def test_parse_recognizes_day_colon_header_style_and_no_rest_column():
    text = (
        "Exercise Sets Reps\n"
        "Bent Over Dumbbell Row 2 - 3 10 - 12\n"
        "Day 1: Upper Body\n"
    )
    result = parse_workout_pdf(text)
    ex = result["days"][0]["exercises"][0]
    assert result["days"][0]["label"] == "Upper Body"
    assert ex["name"] == "Bent Over Dumbbell Row"
    assert ex["sets_text"] == "2 - 3" and ex["reps_text"] == "10 - 12"
    assert ex["rest_text"] is None  # this style has no Rest column


def test_parse_recognizes_bare_workout_number_header_style():
    text = (
        "Exercise Sets Reps Rest\n"
        "Goblet Squat 3 10 - 12 2 Min\n"
        "Workout 1\n"
    )
    result = parse_workout_pdf(text)
    assert result["days"][0]["label"] == "Day 1"  # no label text in this header style


def test_parse_recognizes_each_leg_and_each_arm_reps_qualifiers():
    text = (
        "Exercise Sets Reps\n"
        "Walking Lunge 2 - 3 10 - 12 Each Leg\n"
        "One Arm Dumbbell Row 2 - 3 10 - 12 Each Arm\n"
        "Day 1: Full Body\n"
    )
    result = parse_workout_pdf(text)
    exercises = result["days"][0]["exercises"]
    assert exercises[0]["reps_text"] == "10 - 12 Each Leg"
    assert exercises[1]["reps_text"] == "10 - 12 Each Arm"


def test_parse_more_tables_than_labels_falls_back_to_day_n():
    """A table with no corresponding label found (fewer labels than tables, or a table whose
    zip-position label came back empty) must still get a usable default label."""
    text = "Exercise Sets Reps\nPush-up 3 10\n"  # zero day/workout labels anywhere
    result = parse_workout_pdf(text)
    assert result["days"][0]["label"] == "Day 1"


def test_parse_unrecognized_format_returns_zero_days_never_raises():
    result = parse_workout_pdf("Some random PDF text\nwith no day headers at all.\n")
    assert result["days"] == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `py -m pytest tests/test_workouts_pdf_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.workouts'`

- [ ] **Step 4: Create the package and implement**

Create `app/workouts/__init__.py` (empty file).

Create `app/workouts/pdf_parser.py`:

```python
"""Pure-function parser for workout-plan PDFs (e.g. Muscle & Strength downloads).

Mirrors app/library/sheet_parser.py's philosophy: real-world PDFs vary in header style and
column presence, so every lookup here is positional and defensive. A row or day this can't
confidently parse is simply left out or blank -- never a raised exception. The caller (the
review/edit screen) is always the backstop for anything this gets wrong.

Confirmed directly against 3 real Muscle & Strength sample PDFs: every table ("Exercise Sets
Reps[ Rest]" header, then its rows) appears BEFORE any of the day/workout labels in the
extracted text -- the labels live in a separate "Workout Summary" block later in the document,
in the same order as their tables but not textually adjacent to them. So tables and labels are
found independently and zipped together by POSITION, never by "the label right before/after a
table" (there is no such adjacency in the real files).
"""

from __future__ import annotations

import re
from io import BytesIO

from pypdf import PdfReader

# The literal table-header row, with or without a trailing Rest column. This is the one thing
# that's exactly consistent across every observed real file -- unlike the day/workout labels,
# which vary in style (see _DAY_LABEL_PATTERNS).
_TABLE_HEADER = re.compile(r"^Exercise\s+Sets\s+Reps(\s+Rest)?$")

# Three known day/workout label shapes, tried in order. Each captures a label when the style has
# one; the bare "Workout N" style has none, so its label defaults to "Day N" by the caller.
_DAY_LABEL_PATTERNS = [
    re.compile(r"^Workout #(\d+) - (.+)$"),
    re.compile(r"^Day (\d+): (.+)$"),
    re.compile(r"^Workout (\d+)$"),
]

# One exercise row, anchored from the RIGHT: real rows are single-space-separated with no
# reliable delimiter between the (possibly multi-word) exercise name and its numeric columns, so
# splitting from the left is ambiguous. Anchoring on the trailing Rest ("45 Sec"/"2 Min"), then
# the Reps (a number or range, optionally with a trailing "*" footnote marker or an "Each
# Leg"/"Each Arm"/"Each Side"/bare "Each" qualifier), then the Sets (a number or range), and
# treating everything left over as the name, correctly parses every real row shape confirmed
# directly against all 3 sample PDFs.
_ROW_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?P<sets>\d+(?:\s*-\s*\d+)?)\s+"
    r"(?P<reps>\d+(?:\s*-\s*\d+)?\*?(?:,?\s*Each(?:\s+\w+)?)?)"
    r"(?:\s+(?P<rest>\d+\s*(?:Min|Sec)))?$"
)


def extract_text(pdf_bytes: bytes) -> str:
    """Every page's text, joined with newlines. Never raises on a page with no extractable text
    (an image-only PDF) -- that page just contributes nothing."""
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _find_table_starts(lines: list[str]) -> list[int]:
    """Line index of every table's header row, in document order."""
    return [i for i, line in enumerate(lines) if _TABLE_HEADER.match(line.strip())]


def _find_day_labels(lines: list[str]) -> list[str | None]:
    """One label per recognized day/workout header found ANYWHERE in the document, in the order
    they appear -- None for a recognized-but-labelless header (the bare "Workout N" style), so
    the caller can tell "found but blank" apart from "not found at all" if it ever needs to."""
    labels: list[str | None] = []
    for line in lines:
        stripped = line.strip()
        for pattern in _DAY_LABEL_PATTERNS:
            m = pattern.match(stripped)
            if m:
                labels.append(m.group(2) if m.lastindex and m.lastindex >= 2 else None)
                break
    return labels


def _parse_exercise_row(line: str) -> dict | None:
    """One exercise row from its raw line, or None if it doesn't match the known row shape at
    all (e.g. it's blank, or a stray line from something else entirely)."""
    m = _ROW_PATTERN.match(line.strip())
    if not m:
        return None
    return {
        "name": m.group("name"),
        "sets_text": m.group("sets"),
        "reps_text": m.group("reps"),
        "rest_text": m.group("rest"),
    }


def parse_workout_pdf(text: str) -> dict:
    """Parse one workout-plan PDF's extracted text into {"name": str, "days": [...]}.

    Never raises on an unrecognized format -- worst case, "days" is an empty list, and the
    caller (the create/review/edit screen) opens with nothing pre-filled rather than rejecting
    the upload."""
    lines = text.split("\n")
    table_starts = _find_table_starts(lines)
    if not table_starts:
        return {"name": "", "days": []}
    day_labels = _find_day_labels(lines)
    days = []
    for i, start in enumerate(table_starts):
        end = table_starts[i + 1] if i + 1 < len(table_starts) else len(lines)
        exercises = []
        for line in lines[start + 1:end]:
            row = _parse_exercise_row(line)
            if row:
                exercises.append(row)
        label = day_labels[i] if i < len(day_labels) and day_labels[i] else f"Day {i + 1}"
        days.append({"label": label, "exercises": exercises})
    return {"name": "", "days": days}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_workouts_pdf_parser.py -v`
Expected: PASS (7/7)

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/workouts/ requirements.txt tests/test_workouts_pdf_parser.py
git commit -m "feat: add workout-plan PDF text extraction and parsing"
```

---

### Task 3: Manual creation, shared review/edit screen, scheduling, one-Active-plan rule

**Files:**
- Create: `app/routers/workouts.py`
- Create: `app/templates/workouts/list.html`
- Create: `app/templates/workouts/edit.html`
- Modify: `app/main.py` (register the new router — find where other routers like `library` are
  included and add `workouts` the same way)
- Test: `tests/test_workouts.py`

**Interfaces:**
- Consumes: `WorkoutPlan`, `WorkoutPlanDay`, `WorkoutExercise`, `WorkoutSource` (Task 1).
- Produces: `save_workout_plan(session, plan, name, days_data) -> WorkoutPlan` (the shared
  clear-and-rebuild save function, `days_data` shaped like the parser's own
  `[{"label", "exercises": [{"name", "sets_text", "reps_text", "rest_text"}]}]`), routes
  `GET/POST /workouts`, `GET /workouts/new`, `POST /workouts`, `GET /workouts/{id}/edit`,
  `POST /workouts/{id}`, `POST /workouts/{id}/schedule`, `POST /workouts/{id}/activate`.
  Consumed by Task 4 (PDF upload reuses `save_workout_plan` and `edit.html`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workouts.py`:

```python
from datetime import date

from sqlalchemy import select

from app.models import WorkoutPlan, WorkoutSource


def _plan_form(**overrides):
    fields = {
        "name": "My Manual Plan",
        "day_label[]": ["Day 1"],
        "exercise_name[0][]": ["Push-up"],
        "exercise_sets[0][]": ["3"],
        "exercise_reps[0][]": ["10 - 12"],
        "exercise_rest[0][]": [""],
    }
    return {**fields, **overrides}


def test_create_manual_plan(client, db):
    r = client.post("/workouts", data=_plan_form(), follow_redirects=False)
    assert r.status_code == 303
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    assert plan is not None
    assert plan.source == WorkoutSource.MANUAL
    assert plan.ended_on is None  # Active
    assert len(plan.days) == 1
    assert plan.days[0].label == "Day 1"
    assert plan.days[0].exercises[0].name == "Push-up"
    assert plan.days[0].exercises[0].reps_text == "10 - 12"


def test_activating_a_plan_ends_the_previous_active_one(client, db):
    client.post("/workouts", data=_plan_form(name="Plan A"))
    client.post("/workouts", data=_plan_form(name="Plan B"))
    plan_a = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Plan A"))
    plan_b = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Plan B"))
    db.refresh(plan_a)
    assert plan_a.ended_on is not None  # ended when Plan B was created as the new Active plan
    assert plan_b.ended_on is None


def test_schedule_sets_weekdays_on_a_plan_day(client, db):
    client.post("/workouts", data=_plan_form())
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    day_id = plan.days[0].id
    r = client.post(f"/workouts/{plan.id}/schedule", data={f"weekdays[{day_id}]": "MWF"})
    assert r.status_code == 303
    db.refresh(plan)
    assert plan.days[0].weekdays == "MWF"


def test_edit_replaces_days_and_exercises(client, db):
    client.post("/workouts", data=_plan_form())
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    r = client.post(f"/workouts/{plan.id}", data=_plan_form(
        **{"exercise_name[0][]": ["Sit-up"]}), follow_redirects=False)
    assert r.status_code == 303
    db.refresh(plan)
    assert len(plan.days[0].exercises) == 1
    assert plan.days[0].exercises[0].name == "Sit-up"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_workouts.py -v`
Expected: FAIL — `/workouts` routes don't exist yet (404s).

- [ ] **Step 3: Implement the router**

Create `app/routers/workouts.py`:

```python
"""Workout Plans: manual/PDF creation, the shared review/edit screen, day-of-week scheduling,
and the one-Active-plan-at-a-time rule."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.models import WorkoutExercise, WorkoutPlan, WorkoutPlanDay, WorkoutSource
from app.templating import templates

router = APIRouter()


def _get_own_plan(session: Session, plan_id: int, uid: int) -> WorkoutPlan:
    p = session.get(WorkoutPlan, plan_id)
    if p is None or p.owner_id != uid:
        raise HTTPException(404, "Workout plan not found")
    return p


def save_workout_plan(session: Session, plan: WorkoutPlan, name: str, days_data: list[dict]) -> WorkoutPlan:
    """Write `name` and fully replace `plan`'s days/exercises from `days_data` (the parser's own
    output shape). Mirrors save_protocol's clear-and-rebuild idiom -- simplest way to reconcile
    arbitrary add/remove/reorder from either the manual editor or a re-parsed PDF."""
    plan.name = name
    plan.days.clear()
    session.flush()
    plan.days = [
        WorkoutPlanDay(
            position=i, label=d["label"] or f"Day {i + 1}",
            exercises=[
                WorkoutExercise(
                    position=j, name=ex["name"], sets_text=ex.get("sets_text"),
                    reps_text=ex.get("reps_text"), rest_text=ex.get("rest_text"))
                for j, ex in enumerate(d["exercises"])
            ],
        )
        for i, d in enumerate(days_data)
    ]
    return plan


def _activate(session: Session, plan: WorkoutPlan, uid: int) -> None:
    """Ends whichever other plan of this owner's is currently Active -- never more than one
    Active plan at a time."""
    previously_active = session.scalar(
        select(WorkoutPlan).where(WorkoutPlan.owner_id == uid, WorkoutPlan.id != plan.id,
                                  WorkoutPlan.ended_on.is_(None)))
    if previously_active is not None:
        previously_active.ended_on = date.today()


def _days_from_form(form: dict) -> tuple[str, list[dict]]:
    """Parses the editor form's bracketed-array field names (day_label[], exercise_name[N][],
    etc.) into (name, days_data) matching save_workout_plan's expected shape."""
    name = form.get("name", ["Untitled Plan"])[0]
    labels = form.get("day_label[]", [])
    days = []
    for i, label in enumerate(labels):
        ex_names = form.get(f"exercise_name[{i}][]", [])
        ex_sets = form.get(f"exercise_sets[{i}][]", [])
        ex_reps = form.get(f"exercise_reps[{i}][]", [])
        ex_rest = form.get(f"exercise_rest[{i}][]", [])
        exercises = [
            {"name": ex_names[j], "sets_text": ex_sets[j] or None,
             "reps_text": ex_reps[j] or None, "rest_text": ex_rest[j] or None}
            for j in range(len(ex_names)) if ex_names[j].strip()
        ]
        days.append({"label": label, "exercises": exercises})
    return name, days


@router.get("/workouts")
def workouts_list(request: Request, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    plans = session.scalars(
        select(WorkoutPlan).where(WorkoutPlan.owner_id == uid).order_by(WorkoutPlan.created_at.desc())).all()
    return templates.TemplateResponse(request, "workouts/list.html", {"plans": plans})


@router.get("/workouts/new")
def workouts_new(request: Request):
    return templates.TemplateResponse(request, "workouts/edit.html", {"plan": None, "days": []})


@router.post("/workouts")
async def workouts_create(request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    raw = await request.form()
    form = {k: raw.getlist(k) for k in raw.keys()}
    name, days_data = _days_from_form(form)
    plan = WorkoutPlan(owner_id=uid, source=WorkoutSource.MANUAL, started_on=date.today())
    session.add(plan)
    save_workout_plan(session, plan, name, days_data)
    session.flush()
    _activate(session, plan, uid)
    session.commit()
    return RedirectResponse(f"/workouts/{plan.id}/edit", status_code=303)


@router.get("/workouts/{plan_id}/edit")
def workouts_edit(plan_id: int, request: Request, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    return templates.TemplateResponse(request, "workouts/edit.html", {"plan": plan, "days": plan.days})


@router.post("/workouts/{plan_id}")
async def workouts_update(plan_id: int, request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    raw = await request.form()
    form = {k: raw.getlist(k) for k in raw.keys()}
    name, days_data = _days_from_form(form)
    save_workout_plan(session, plan, name, days_data)
    session.commit()
    return RedirectResponse(f"/workouts/{plan.id}/edit", status_code=303)


@router.post("/workouts/{plan_id}/schedule")
async def workouts_schedule(plan_id: int, request: Request, session: Session = Depends(get_session),
                            uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    raw = await request.form()
    for day in plan.days:
        key = f"weekdays[{day.id}]"
        day.weekdays = raw.get(key) or None
    session.commit()
    return RedirectResponse(f"/workouts/{plan.id}/edit", status_code=303)


@router.post("/workouts/{plan_id}/activate")
def workouts_activate(plan_id: int, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    plan.ended_on = None
    _activate(session, plan, uid)
    session.commit()
    return RedirectResponse("/workouts", status_code=303)
```

Modify `app/main.py`: find the line that does `app.include_router(library.router)` (or similar
for another feature) and add, alongside it:

```python
from app.routers import workouts
app.include_router(workouts.router)
```

- [ ] **Step 4: Create the templates**

Create `app/templates/workouts/list.html`:

```html
{% extends "base.html" %}
{% set active_nav = "workouts" %}
{% block title %}Workouts{% endblock %}

{% block content %}
<div class="page-head">
  <div><h1>Workouts</h1></div>
  <a class="btn btn-primary" href="/workouts/new">New plan</a>
</div>

<div class="plain-list">
  {% for p in plans %}
  <div class="side-box">
    <div class="side-head">
      <h2 class="section-title"><a href="/workouts/{{ p.id }}/edit">{{ p.name }}</a></h2>
      {% if p.ended_on %}<span class="tag tag-plain">Ended</span>{% else %}<span class="status status-active">Active</span>{% endif %}
    </div>
    <p class="small muted">{{ p.days | length }} day(s) · {{ p.source.value }}</p>
    {% if p.ended_on %}
    <form method="post" action="/workouts/{{ p.id }}/activate"><button type="submit" class="btn btn-ghost">Make Active</button></form>
    {% endif %}
  </div>
  {% else %}
  <p class="muted">No workout plans yet.</p>
  {% endfor %}
</div>
{% endblock %}
```

Create `app/templates/workouts/edit.html`:

```html
{% extends "base.html" %}
{% set active_nav = "workouts" %}
{% block title %}{{ plan.name if plan else "New Workout Plan" }}{% endblock %}

{% block content %}
<div class="page-head"><div><h1>{{ plan.name if plan else "New Workout Plan" }}</h1></div></div>

<form method="post" action="{{ '/workouts/' ~ plan.id if plan else '/workouts' }}">
  <label>Plan name <input type="text" name="name" value="{{ plan.name if plan else '' }}" required></label>

  <div id="days">
    {% for day in days %}
    {% set day_idx = loop.index0 %}
    <fieldset class="lib-section">
      <legend>Day {{ loop.index }}</legend>
      <input type="hidden" name="day_label[]" value="{{ day.label }}">
      <table class="lib-table">
        <thead><tr><th>Exercise</th><th>Sets</th><th>Reps</th><th>Rest</th></tr></thead>
        <tbody>
          {% for ex in day.exercises %}
          <tr>
            <td><input type="text" name="exercise_name[{{ day_idx }}][]" value="{{ ex.name }}"></td>
            <td><input type="text" name="exercise_sets[{{ day_idx }}][]" value="{{ ex.sets_text or '' }}"></td>
            <td><input type="text" name="exercise_reps[{{ day_idx }}][]" value="{{ ex.reps_text or '' }}"></td>
            <td><input type="text" name="exercise_rest[{{ day_idx }}][]" value="{{ ex.rest_text or '' }}"></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </fieldset>
    {% endfor %}
  </div>

  <button type="submit" class="btn btn-primary">Save</button>
</form>

{% if plan %}
<form method="post" action="/workouts/{{ plan.id }}/schedule">
  <h2 class="section-title">Schedule</h2>
  {% for day in plan.days %}
  <label>{{ day.label }}
    <input type="text" name="weekdays[{{ day.id }}]" value="{{ day.weekdays or '' }}" placeholder="e.g. MWF">
  </label>
  {% endfor %}
  <button type="submit" class="btn">Save schedule</button>
</form>
{% endif %}
{% endblock %}
```

This is a minimal, functional editor (static day/exercise rows, no JS-driven add/remove row yet
— that's a reasonable fast-follow, not required for the data flow this task tests). Every
exercise row within a given day shares that day's own `day_idx` (captured once per day, via
`{% set day_idx = loop.index0 %}`, before the inner exercise loop shadows the outer `loop`
variable) in its field name — e.g. all of Day 2's exercises post as repeated values under
`exercise_name[1][]`, `exercise_sets[1][]`, etc. This matches exactly what `_days_from_form`
parses: `form.get(f"exercise_name[{i}][]", [])` for the same `i` as that day's position in
`day_label[]`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_workouts.py -v`
Expected: PASS (4/4)

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/workouts.py app/templates/workouts/ app/main.py tests/test_workouts.py
git commit -m "feat: add manual Workout Plan creation, editing, and scheduling"
```

---

### Task 4: PDF upload integration

**Files:**
- Modify: `app/uploads.py` (add a workout-PDF save function)
- Modify: `app/routers/workouts.py`
- Modify: `app/templates/workouts/list.html`
- Test: `tests/test_workouts.py`

**Interfaces:**
- Consumes: `extract_text`, `parse_workout_pdf` (Task 2); `save_workout_plan` (Task 3).
- Produces: `POST /workouts/upload`. Nothing consumed by a later task.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_workouts.py`:

```python
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def _minimal_workout_pdf(text: str) -> bytes:
    """Same verified approach as tests/test_workouts_pdf_parser.py's _pdf_bytes (duplicated here
    since these are separate test files) -- a page with no /Font resource extracts back as
    garbled text, not the original, so a real Helvetica font must be registered first."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font[NameObject("/Encoding")] = NameObject("/WinAnsiEncoding")
    font_ref = writer._add_object(font)
    resources = DictionaryObject()
    font_dict = DictionaryObject()
    font_dict[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = font_dict
    page[NameObject("/Resources")] = resources
    ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for line in text.split("\n"):
        safe = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"({safe}) Tj")
        ops.append("0 -14 Td")
    ops.append("ET")
    content = DecodedStreamObject()
    content.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_upload_pdf_creates_a_prefilled_plan(client, db):
    # Real Muscle & Strength PDFs list every table BEFORE any day/workout label -- see Task 2's
    # pdf_parser.py docstring. This fixture matches that real order.
    pdf_text = "Exercise Sets Reps\nPush-up 3 10 - 12\nDay 1: Upper Body\n"
    r = client.post(
        "/workouts/upload",
        files={"pdf": ("plan.pdf", _minimal_workout_pdf(pdf_text), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    from app.models import WorkoutPlan, WorkoutSource
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.source == WorkoutSource.PDF))
    assert plan is not None
    assert plan.days[0].label == "Upper Body"
    assert plan.days[0].exercises[0].name == "Push-up"


def test_upload_unparseable_pdf_still_creates_an_empty_editable_plan(client, db):
    r = client.post(
        "/workouts/upload",
        files={"pdf": ("blank.pdf", _minimal_workout_pdf("Not a workout sheet at all."), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303  # never a rejected upload
```

(Add `from sqlalchemy import select` at the top of the test file if not already imported from
Task 3.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_workouts.py -k upload -v`
Expected: FAIL — `/workouts/upload` doesn't exist (404).

- [ ] **Step 3: Add the upload save function**

Add to `app/uploads.py`, following the existing `save_coa`/`save_lab_report` pattern exactly:

```python
async def save_workout_pdf(upload: UploadFile) -> str:
    """Validate and store an uploaded workout-plan PDF. Returns the stored filename."""
    ext = Path(upload.filename or "").suffix.lower()
    if ext != ".pdf":
        raise UploadError("Workout plan must be a PDF.")

    data = await upload.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError(f"PDF is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not _sniff_ok(ext, data[:16]):
        raise UploadError("File contents don't match a PDF.")

    config.ensure_dirs()
    filename = f"{uuid.uuid4().hex}{ext}"
    (config.WORKOUT_PDF_DIR / filename).write_bytes(data)
    return filename
```

Add `WORKOUT_PDF_DIR` to `app/config.py` alongside the existing `COA_DIR`/`LAB_REPORT_DIR`
definitions (same pattern: a subdirectory under the app's data directory), and to
`ensure_dirs()`'s list of directories it creates.

- [ ] **Step 4: Add the upload route**

Add to `app/routers/workouts.py`:

```python
from fastapi import UploadFile, File
from app import config
from app.uploads import save_workout_pdf, UploadError
from app.workouts.pdf_parser import extract_text, parse_workout_pdf


@router.post("/workouts/upload")
async def workouts_upload(pdf: UploadFile = File(...), session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    try:
        filename = await save_workout_pdf(pdf)
    except UploadError as e:
        raise HTTPException(422, str(e))
    raw_bytes = (config.WORKOUT_PDF_DIR / filename).read_bytes()
    text = extract_text(raw_bytes)
    parsed = parse_workout_pdf(text)
    plan = WorkoutPlan(owner_id=uid, source=WorkoutSource.PDF, source_pdf_filename=filename,
                       started_on=date.today())
    session.add(plan)
    name = parsed["name"] or pdf.filename or "Imported Plan"
    save_workout_plan(session, plan, name, parsed["days"])
    session.flush()
    _activate(session, plan, uid)
    session.commit()
    return RedirectResponse(f"/workouts/{plan.id}/edit", status_code=303)
```

- [ ] **Step 5: Add the upload form to the list page**

Modify `app/templates/workouts/list.html` — add near the "New plan" button:

```html
<form method="post" action="/workouts/upload" enctype="multipart/form-data">
  <label>Import a PDF <input type="file" name="pdf" accept="application/pdf" required></label>
  <button type="submit" class="btn">Upload</button>
</form>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `py -m pytest tests/test_workouts.py -v`
Expected: All PASS (6/6).

- [ ] **Step 7: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/uploads.py app/config.py app/routers/workouts.py app/templates/workouts/list.html tests/test_workouts.py
git commit -m "feat: import a workout plan from an uploaded PDF"
```

---

### Task 5: Completion logging

**Files:**
- Modify: `app/routers/workouts.py`
- Create: `app/templates/workouts/log.html`
- Test: `tests/test_workouts.py`

**Interfaces:**
- Consumes: `WorkoutLog`, `WorkoutExerciseLog`, `WeightUnit` (Task 1).
- Produces: `GET /workouts/day/{plan_day_id}/log?date=YYYY-MM-DD`, `POST
  /workouts/day/{plan_day_id}/log`. Consumed by Task 6 (the Today-view link target).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workouts.py`:

```python
def test_log_completion_persists_partial_state(client, db):
    client.post("/workouts", data=_plan_form(
        **{"exercise_name[0][]": ["Push-up", "Sit-up"],
           "exercise_sets[0][]": ["3", "3"], "exercise_reps[0][]": ["10", "10"],
           "exercise_rest[0][]": ["", ""]}))
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    day = plan.days[0]
    ex0, ex1 = day.exercises[0], day.exercises[1]

    r = client.post(f"/workouts/day/{day.id}/log", data={
        "log_date": "2026-01-08",
        f"completed[{ex0.id}]": "on",
        f"weight_value[{ex0.id}]": "25",
        f"weight_unit[{ex0.id}]": "lb",
        f"reps_value[{ex0.id}]": "12",
        # ex1 deliberately left unchecked and blank
    }, follow_redirects=False)
    assert r.status_code == 303

    from app.models import WorkoutLog
    log = db.scalar(select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id))
    by_exercise = {el.exercise_id: el for el in log.exercise_logs}
    assert by_exercise[ex0.id].completed is True
    assert by_exercise[ex0.id].weight_value == 25.0
    assert by_exercise[ex0.id].reps_value == 12
    assert by_exercise[ex1.id].completed is False
    assert by_exercise[ex1.id].weight_value is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_workouts.py -k log_completion -v`
Expected: FAIL — the route doesn't exist (404).

- [ ] **Step 3: Implement**

Add to `app/routers/workouts.py`:

```python
from datetime import date as date_type
from app.models import WorkoutExerciseLog, WorkoutLog, WeightUnit


def _get_own_day(session: Session, plan_day_id: int, uid: int) -> WorkoutPlanDay:
    day = session.get(WorkoutPlanDay, plan_day_id)
    if day is None or day.plan.owner_id != uid:
        raise HTTPException(404, "Workout day not found")
    return day


@router.get("/workouts/day/{plan_day_id}/log")
def workouts_log_form(plan_day_id: int, request: Request, log_date: date_type | None = None,
                      session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    day = _get_own_day(session, plan_day_id, uid)
    return templates.TemplateResponse(request, "workouts/log.html", {
        "day": day, "log_date": log_date or date_type.today(), "units": list(WeightUnit),
    })


@router.post("/workouts/day/{plan_day_id}/log")
async def workouts_log_save(plan_day_id: int, request: Request, session: Session = Depends(get_session),
                            uid: int = Depends(current_user_id)):
    day = _get_own_day(session, plan_day_id, uid)
    raw = await request.form()
    log_date = date_type.fromisoformat(raw["log_date"])

    existing = session.scalar(
        select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id, WorkoutLog.log_date == log_date))
    if existing is not None:
        session.delete(existing)
        session.flush()

    log = WorkoutLog(owner_id=uid, plan_day_id=day.id, log_date=log_date)
    session.add(log)
    for ex in day.exercises:
        weight_value = raw.get(f"weight_value[{ex.id}]")
        weight_unit = raw.get(f"weight_unit[{ex.id}]")
        reps_value = raw.get(f"reps_value[{ex.id}]")
        log.exercise_logs.append(WorkoutExerciseLog(
            exercise_id=ex.id,
            completed=raw.get(f"completed[{ex.id}]") == "on",
            weight_value=float(weight_value) if weight_value else None,
            weight_unit=WeightUnit(weight_unit) if weight_unit else None,
            reps_value=int(reps_value) if reps_value else None,
        ))
    session.commit()
    return RedirectResponse("/today", status_code=303)
```

- [ ] **Step 4: Create the template**

Create `app/templates/workouts/log.html`:

```html
{% extends "base.html" %}
{% set active_nav = "workouts" %}
{% block title %}Log: {{ day.label }}{% endblock %}

{% block content %}
<div class="page-head"><div><h1>{{ day.label }}</h1><p class="muted">{{ log_date }}</p></div></div>

<form method="post" action="/workouts/day/{{ day.id }}/log">
  <input type="hidden" name="log_date" value="{{ log_date }}">
  <table class="lib-table">
    <thead><tr><th>Done</th><th>Exercise</th><th>Sets/Reps</th><th>Weight</th><th>Reps done</th></tr></thead>
    <tbody>
      {% for ex in day.exercises %}
      <tr>
        <td><input type="checkbox" name="completed[{{ ex.id }}]"></td>
        <td>{{ ex.name }}</td>
        <td>{{ ex.sets_text or '—' }} × {{ ex.reps_text or '—' }}</td>
        <td>
          <input type="number" step="0.1" name="weight_value[{{ ex.id }}]" style="width: 5em">
          <select name="weight_unit[{{ ex.id }}]">
            {% for u in units %}<option value="{{ u.value }}">{{ u.label }}</option>{% endfor %}
          </select>
        </td>
        <td><input type="number" name="reps_value[{{ ex.id }}]" style="width: 4em"></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  <button type="submit" class="btn btn-primary">Save</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_workouts.py -v`
Expected: All PASS.

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/workouts.py app/templates/workouts/log.html tests/test_workouts.py
git commit -m "feat: log workout completion with per-exercise weight and reps"
```

---

### Task 6: Today + Calendar surfacing

**Files:**
- Modify: `app/routers/dosing.py`
- Modify: `app/templates/dosing/today.html`
- Modify: `app/templates/calendar/_day.html` (or wherever a single day cell renders — check
  `app/templates/calendar/_month.html`/`_week.html` for the actual per-day include used)
- Test: `tests/test_dosing.py`, `tests/test_calendar_page.py`

**Interfaces:**
- Consumes: `WorkoutPlan`, `WorkoutPlanDay`, `WorkoutLog`, `WEEKDAY_LETTERS` (Task 1, existing).
- Produces: `workouts_due_today(session, uid, today) -> list[WorkoutPlanDay]` (Today's "still
  pending" list) and `scheduled_workout_dates(session, uid, start, end) -> set[date]` (Calendar's
  plain marker, regardless of completion). Nothing consumed by a later task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dosing.py` (open the file first to match its `client`/`db` fixture usage and
whatever helper builds a logged-in test peptide/protocol, for consistency of style):

```python
def test_today_shows_a_due_workout_and_hides_it_once_logged(client, db):
    from datetime import date
    from app.models import WorkoutPlan, WorkoutPlanDay, WorkoutSource, WEEKDAY_LETTERS
    today_letter = WEEKDAY_LETTERS[date.today().weekday()]
    plan = WorkoutPlan(owner_id=1, source=WorkoutSource.MANUAL, started_on=date.today())
    plan.days = [WorkoutPlanDay(position=0, label="Leg Day", weekdays=today_letter)]
    db.add(plan)
    db.commit()

    body = client.get("/today").text
    assert "Leg Day" in body

    day = plan.days[0]
    client.post(f"/workouts/day/{day.id}/log", data={"log_date": date.today().isoformat()})
    body_after = client.get("/today").text
    assert "Leg Day" not in body_after
```

(Adjust `owner_id=1` if this test file's convention for "the logged-in test user's id" differs —
check an existing test in the same file for how it identifies the current test user.)

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_dosing.py -k today_shows_a_due_workout -v`
Expected: FAIL — `"Leg Day"` never appears (Today doesn't know about workouts yet).

- [ ] **Step 3: Implement the helper and wire it into Today**

Add to `app/routers/workouts.py` (reused by both `dosing.py` and calendar code):

```python
from app.models import WEEKDAY_LETTERS


def workouts_due_today(session: Session, uid: int, today: date_type) -> list[WorkoutPlanDay]:
    """Active plans' days scheduled for today's weekday, excluding any already logged today."""
    letter = WEEKDAY_LETTERS[today.weekday()]
    days = session.scalars(
        select(WorkoutPlanDay).join(WorkoutPlan)
        .where(WorkoutPlan.owner_id == uid, WorkoutPlan.ended_on.is_(None),
              WorkoutPlanDay.weekdays.isnot(None))
    ).all()
    due = [d for d in days if letter in d.weekdays]
    logged_day_ids = {
        wl.plan_day_id for wl in session.scalars(
            select(WorkoutLog).where(WorkoutLog.owner_id == uid, WorkoutLog.log_date == today))
    }
    return [d for d in due if d.id not in logged_day_ids]
```

Modify `app/routers/dosing.py`'s `today_page` — add, right after the existing `due = [...]`
line (the dose-due list):

```python
    from app.routers.workouts import workouts_due_today
    workout_days_due = workouts_due_today(session, uid, today)
```

and add `"workout_days_due": workout_days_due,` to the `TemplateResponse` context dict at the
bottom of `today_page`.

- [ ] **Step 4: Add the section to the Today template**

Modify `app/templates/dosing/today.html` — add a new section (placement: anywhere alongside the
existing doses-due list, following that list's own markup style for a `<ul>`/tile layout):

```html
{% if workout_days_due %}
<section class="lib-section">
  <h2 class="section-title">Workouts due today</h2>
  <ul class="plain-list">
    {% for day in workout_days_due %}
    <li><a href="/workouts/day/{{ day.id }}/log">{{ day.label }}</a></li>
    {% endfor %}
  </ul>
</section>
{% endif %}
```

- [ ] **Step 5: Add a plain Calendar marker**

`app/templates/calendar/_month.html` renders protocol doses through a complex per-lane
color-bar system (`row.bars`, built by `month_rows()`) — reusing that machinery for a plain
workout marker would mean adopting its whole lane/color system, exactly what the spec says not
to do here. Instead, add a separate, self-contained "is a workout scheduled this day" set,
independent of the bars.

Add to `app/routers/workouts.py` (alongside `workouts_due_today`):

```python
def scheduled_workout_dates(session: Session, uid: int, start: date_type, end: date_type) -> set[date_type]:
    """Every date in [start, end] on which an Active plan has a day scheduled -- regardless of
    whether it's already been logged (unlike workouts_due_today, which is specifically "still
    pending today"). Used only for a plain calendar marker, not for the Today list."""
    days = session.scalars(
        select(WorkoutPlanDay).join(WorkoutPlan)
        .where(WorkoutPlan.owner_id == uid, WorkoutPlan.ended_on.is_(None),
              WorkoutPlanDay.weekdays.isnot(None))
    ).all()
    dates = set()
    d = start
    while d <= end:
        letter = WEEKDAY_LETTERS[d.weekday()]
        if any(letter in day.weekdays for day in days):
            dates.add(d)
        d += timedelta(days=1)
    return dates
```

(`timedelta` needs importing at the top of `app/routers/workouts.py`: `from datetime import
date as date_type, timedelta`.)

Modify `app/routers/calendar.py`'s `calendar_page` — add right after the `ctx |= {"title": ...}`
block (which is where `first`/`last`, the visible date range, are already both in scope for
every view):

```python
    from app.routers.workouts import scheduled_workout_dates  # deferred: avoid a module-load cycle
    ctx["workout_dates"] = scheduled_workout_dates(session, uid, first, last)
```

Modify `app/templates/calendar/_month.html` — add a small marker next to the existing
`cal-daynum` link (line 13-14):

```html
    <a class="cal-daynum" href="{{ url('day', d) }}" style="grid-column: {{ loop.index + 1 }}"
       aria-label="{{ d.strftime('%A, %B') }} {{ d.day }}">{{ d.day }}</a>
    {% if d in workout_dates %}<span class="small" style="grid-column: {{ loop.index + 1 }}" title="Workout scheduled">🏋</span>{% endif %}
```

This is intentionally plain (no color, no lane, just a small icon) — Beautification owns turning
this into something more visually integrated later.

- [ ] **Step 6: Write the failing Calendar test**

Add to `tests/test_calendar_page.py` (open the file first to match its `client`/`db` fixture
style, same as the other tests in that file):

```python
def test_month_view_marks_a_day_with_a_scheduled_workout(client, db):
    from datetime import date
    from app.models import WorkoutPlan, WorkoutPlanDay, WorkoutSource, WEEKDAY_LETTERS
    today_letter = WEEKDAY_LETTERS[date.today().weekday()]
    plan = WorkoutPlan(owner_id=1, source=WorkoutSource.MANUAL, started_on=date.today())
    plan.days = [WorkoutPlanDay(position=0, label="Leg Day", weekdays=today_letter)]
    db.add(plan)
    db.commit()

    body = client.get("/calendar").text
    assert "Workout scheduled" in body
```

(Adjust `owner_id=1` the same way you would in Task 6's `test_dosing.py` test, if this test
file's convention for the logged-in test user's id differs.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `py -m pytest tests/test_dosing.py -k today_shows_a_due_workout tests/test_calendar_page.py -k marks_a_day_with_a_scheduled_workout -v`
Expected: PASS (both).

- [ ] **Step 8: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 9: Commit**

```bash
git add app/routers/workouts.py app/routers/dosing.py app/routers/calendar.py app/templates/dosing/today.html app/templates/calendar/_month.html tests/test_dosing.py tests/test_calendar_page.py
git commit -m "feat: surface due workouts on Today and Calendar"
```

---

### Task 7: Journal integration

**Files:**
- Modify: `app/routers/journal.py`
- Modify: `app/templates/measurements/index.html`
- Test: `tests/test_journal.py`

**Interfaces:**
- Consumes: `WorkoutLog`, `WorkoutExerciseLog` (Task 1).
- Produces: `workouts_for(session, owner_id, date) -> list[dict]`. Nothing consumed further.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_journal.py` (open the file first to match its fixture/helper conventions):

```python
def test_workouts_for_returns_completed_workouts_on_that_date(db):
    from datetime import date
    from app.models import WorkoutExerciseLog, WorkoutLog, WorkoutPlan, WorkoutPlanDay, WorkoutSource
    from app.routers.journal import workouts_for

    plan = WorkoutPlan(owner_id=1, source=WorkoutSource.MANUAL, started_on=date.today())
    plan.days = [WorkoutPlanDay(position=0, label="Leg Day")]
    db.add(plan)
    db.flush()
    log = WorkoutLog(owner_id=1, plan_day_id=plan.days[0].id, log_date=date(2026, 1, 8))
    db.add(log)
    db.commit()

    result = workouts_for(db, 1, date(2026, 1, 8))
    assert len(result) == 1
    assert result[0]["label"] == "Leg Day"

    assert workouts_for(db, 1, date(2026, 1, 9)) == []  # wrong date
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_journal.py -k workouts_for -v`
Expected: FAIL — `ImportError: cannot import name 'workouts_for'`

- [ ] **Step 3: Implement**

Add to `app/routers/journal.py`, right after the existing `doses_for` function:

```python
def workouts_for(session: Session, owner_id: int, entry_date: date) -> list[dict]:
    """That owner's completed workouts on that date. Query-time only, same ownership-check
    reasoning as doses_for: for a shared entry, owner_id must be the entry's own owner, not the
    viewer -- the viewer's access is already gated by the sharing query."""
    from app.models import WorkoutLog  # deferred: avoid a module-load cycle with app.routers.workouts
    rows = session.scalars(
        select(WorkoutLog).where(WorkoutLog.owner_id == owner_id, WorkoutLog.log_date == entry_date)
    ).all()
    return [
        {"label": r.plan_day.label,
         "completed_count": sum(1 for el in r.exercise_logs if el.completed),
         "total_count": len(r.exercise_logs)}
        for r in rows
    ]
```

Modify `_entry_view` (same file) to accept and include workouts:

```python
def _entry_view(entry: JournalEntry, doses: list[dict], workouts: list[dict], owner_name: str | None = None,
                viewer_tz: str | None = None) -> dict:
    return {
        "date": entry.entry_date,
        "mood": entry.mood,
        "energy": entry.energy,
        "sleep_quality": entry.sleep_quality,
        "side_effects": [se.side_effect.value for se in entry.side_effects],
        "side_effects_other": entry.side_effects_other,
        "notes": entry.notes,
        "owner_name": owner_name,
        "quick_notes": [
            {"noted_at": qn.noted_at, "time_display": _local_time_str(qn.noted_at, viewer_tz), "text": qn.text}
            for qn in entry.quick_notes
        ],
        "doses": doses,
        "workouts": workouts,
    }
```

Update this file's three existing `_entry_view(...)` call sites to also pass `workouts_for`,
exactly mirroring how each one already passes `doses_for`:

```python
# Before:
views = [_entry_view(e, doses_for(session, e.owner_id, e.entry_date), viewer_tz=viewer_tz)
# After:
views = [_entry_view(e, doses_for(session, e.owner_id, e.entry_date),
                     workouts_for(session, e.owner_id, e.entry_date), viewer_tz=viewer_tz)
```

```python
# Before:
    _entry_view(e, doses_for(session, e.owner_id, e.entry_date), owner_name=owner_names.get(e.owner_id),
# After:
    _entry_view(e, doses_for(session, e.owner_id, e.entry_date),
               workouts_for(session, e.owner_id, e.entry_date), owner_name=owner_names.get(e.owner_id),
```

```python
# Before:
    _entry_view(today_row, doses_for(session, viewer_uid, date.today()), viewer_tz=viewer_tz)
# After:
    _entry_view(today_row, doses_for(session, viewer_uid, date.today()),
               workouts_for(session, viewer_uid, date.today()), viewer_tz=viewer_tz)
```

(Each of these three lines continues onto the next line unchanged in the original file — only
the shown line itself changes; find each by searching for `_entry_view(` in `app/routers/journal.py`.)

- [ ] **Step 4: Render the new section**

Modify `app/templates/measurements/index.html` — find the `{% for d in e.doses %}` block (the
Journal history row's dose list) and add, immediately after its closing `{%- endif -%}`/`</ul>`:

```html
{% if e.workouts %}
<ul class="dose-list">
  {% for w in e.workouts %}
  <li>{{ w.label }} — {{ w.completed_count }}/{{ w.total_count }} exercises completed</li>
  {% endfor %}
</ul>
{% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_journal.py -v`
Expected: All PASS.

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/journal.py app/templates/measurements/index.html tests/test_journal.py
git commit -m "feat: surface completed workouts in the Journal tab"
```

---

### Task 8: Fitness Test

**Files:**
- Create: `app/routers/fitness_test.py`
- Create: `app/templates/fitness_test/index.html`
- Modify: `app/main.py` (register the router)
- Test: `tests/test_fitness_test.py`

**Interfaces:**
- Consumes: `FitnessTestResult`, `FitnessTestExerciseName` (Task 1);
  `app.routers.measurements._chart` (existing, via deferred import).
- Produces: `GET /fitness-test`, `POST /fitness-test`. Nothing consumed further.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fitness_test.py`:

```python
from datetime import date, timedelta

from sqlalchemy import select

from app.models import FitnessTestExerciseName, FitnessTestResult


def test_log_a_fitness_test_result(client, db):
    r = client.post("/fitness-test", data={
        "tested_at": "2026-01-08",
        "max_pushups": "20",
        "max_situps": "15",
    }, follow_redirects=False)
    assert r.status_code == 303
    results = db.scalars(select(FitnessTestResult)).all()
    by_exercise = {r.exercise: r.value for r in results}
    assert by_exercise[FitnessTestExerciseName.MAX_PUSHUPS] == 20.0
    assert by_exercise[FitnessTestExerciseName.MAX_SITUPS] == 15.0
    assert FitnessTestExerciseName.PLANK_HOLD_SECONDS not in by_exercise  # left blank, not required


def test_retest_suggestion_appears_per_exercise_independently(client, db):
    db.add(FitnessTestResult(owner_id=1, exercise=FitnessTestExerciseName.MAX_PUSHUPS,
                             value=20, tested_at=date.today() - timedelta(days=30)))
    db.add(FitnessTestResult(owner_id=1, exercise=FitnessTestExerciseName.MAX_SITUPS,
                             value=15, tested_at=date.today() - timedelta(days=5)))
    db.commit()
    body = client.get("/fitness-test").text
    assert "retest" in body.lower()
    # crude but effective: the suggestion text must be near Push-ups, not Sit-ups
    pushup_idx = body.lower().index("push")
    situp_idx = body.lower().index("sit")
    retest_idx = body.lower().index("retest")
    assert abs(retest_idx - pushup_idx) < abs(retest_idx - situp_idx)


def test_chart_renders_with_multiple_points(client, db):
    for i, value in enumerate([10, 15, 20]):
        db.add(FitnessTestResult(owner_id=1, exercise=FitnessTestExerciseName.MAX_PUSHUPS,
                                 value=value, tested_at=date.today() - timedelta(days=(2 - i) * 7)))
    db.commit()
    body = client.get("/fitness-test").text
    assert "<polyline" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_fitness_test.py -v`
Expected: FAIL — `/fitness-test` doesn't exist (404).

- [ ] **Step 3: Implement the router**

Create `app/routers/fitness_test.py`:

```python
"""The standalone Fitness Test: a fixed, no-equipment bodyweight exercise set, retaken anytime,
with a per-exercise trend chart and a gentle (never blocking) 28-day retest suggestion."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.models import FitnessTestExerciseName, FitnessTestResult
from app.templating import templates

router = APIRouter()

_LABELS = {
    FitnessTestExerciseName.MAX_PUSHUPS: "Max Push-ups",
    FitnessTestExerciseName.MAX_SITUPS: "Max Sit-ups",
    FitnessTestExerciseName.MAX_BODYWEIGHT_SQUATS: "Max Bodyweight Squats",
    FitnessTestExerciseName.PLANK_HOLD_SECONDS: "Plank Hold (seconds)",
}

_RETEST_AFTER_DAYS = 28


def _charts_and_suggestions(session: Session, uid: int) -> list[dict]:
    from app.routers.measurements import _chart  # deferred: avoid a module-load cycle

    out = []
    for exercise in FitnessTestExerciseName:
        rows = session.scalars(
            select(FitnessTestResult)
            .where(FitnessTestResult.owner_id == uid, FitnessTestResult.exercise == exercise)
            .order_by(FitnessTestResult.tested_at)
        ).all()
        points = [(r.tested_at, r.value) for r in rows]
        last_tested = rows[-1].tested_at if rows else None
        suggest_retest = last_tested is not None and (date.today() - last_tested).days >= _RETEST_AFTER_DAYS
        out.append({
            "key": exercise.value, "label": _LABELS[exercise],
            "chart": _chart(points) if len(points) >= 2 else None,
            "last_tested": last_tested, "suggest_retest": suggest_retest,
        })
    return out


@router.get("/fitness-test")
def fitness_test_page(request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    return templates.TemplateResponse(request, "fitness_test/index.html", {
        "exercises": _charts_and_suggestions(session, uid),
        "today": date.today(),
    })


@router.post("/fitness-test")
async def fitness_test_log(request: Request, session: Session = Depends(get_session),
                           uid: int = Depends(current_user_id)):
    raw = await request.form()
    tested_at = date.fromisoformat(raw["tested_at"])
    for exercise in FitnessTestExerciseName:
        value = raw.get(exercise.value)
        if value:
            session.add(FitnessTestResult(owner_id=uid, exercise=exercise, value=float(value), tested_at=tested_at))
    session.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/fitness-test", status_code=303)
```

Modify `app/main.py`: register alongside the other routers —

```python
from app.routers import fitness_test
app.include_router(fitness_test.router)
```

- [ ] **Step 4: Create the template**

Create `app/templates/fitness_test/index.html`:

```html
{% extends "base.html" %}
{% set active_nav = "fitness_test" %}
{% block title %}Fitness Test{% endblock %}

{% block content %}
<div class="page-head"><div><h1>Fitness Test</h1></div></div>

<form method="post" action="/fitness-test">
  <label>Date <input type="date" name="tested_at" value="{{ today }}" required></label>
  {% for e in exercises %}
  <label>{{ e.label }} <input type="number" step="0.1" name="{{ e.key }}"></label>
  {% endfor %}
  <button type="submit" class="btn btn-primary">Log a new test</button>
</form>

{% for e in exercises %}
<section class="lib-section">
  <h2 class="section-title">{{ e.label }}</h2>
  {% if e.suggest_retest %}
  <p class="note">It's been a while — consider a retest for {{ e.label }}.</p>
  {% endif %}
  {% if e.chart %}
  <svg viewBox="0 0 {{ e.chart.width }} {{ e.chart.height }}" class="measurement-chart" role="img" aria-label="{{ e.label }} over time">
    <polyline points="{{ e.chart.poly }}" fill="none" stroke="currentColor" stroke-width="2"></polyline>
  </svg>
  {% else %}
  <p class="muted small">Log at least two results to see a trend.</p>
  {% endif %}
</section>
{% endfor %}
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_fitness_test.py -v`
Expected: All PASS (3/3).

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/fitness_test.py app/templates/fitness_test/ app/main.py tests/test_fitness_test.py
git commit -m "feat: add standalone Fitness Test with per-exercise trend charts"
```

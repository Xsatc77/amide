# Food Tracking (Part A: core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Food tab (replacing Macros) with food logging, a goal-adjusted calorie limit and potential deficit, a diet-type picker, a macro pie chart and macro fulfilment bars, backed by My foods and a bundled starter list.

**Architecture:** Two tables, `foods` (starter rows have `owner_id` NULL, a person's own foods are scoped to them) and `food_logs` (each entry stores a snapshot of its numbers). Pure functions in `app/food/targets.py` compute targets, totals, deficit and fulfilment; `app/food/summary.py` assembles a day from the database using the Energy tab's `tdee.report` and the same daily workout burn the Energy tab charts. Routes live in `app/routers/food.py`; the page is a partial included by the Weight & Measurements template. Backup gets one person-level section; food is never shareable.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic (SQLite), Jinja2, server-rendered SVG (`app/workouts/charts.ring`), vanilla JS.

**Spec:** `docs/superpowers/specs/2026-10-06-food-tracking-design.md`

## Global Constraints

- Test command: `.venv/Scripts/python.exe -m pytest -q -p no:warnings`. The suite must stay green after every task (baseline 1570).
- Test data is invented. The shipped starter list contains only public-domain USDA nutrition numbers. The vendor-name scan `/c/tmp/amide-scrub/denylist_scan.py` must stay clean.
- Every food and log query is scoped to the signed-in owner; another person's food or log is **404**. A starter food (`owner_id` NULL) is readable by everyone and **never editable or deletable** through the app.
- Limits: calories per serving <= 5000, each of protein/carb/fat/fiber per serving <= 500, all >= 0 and finite; servings > 0 and <= 50; name 1-120 characters; serving text 1-60.
- One calorie number: TDEE comes from `app.measurements.tdee.report` (the Energy tab); the goal offset and safe floor from `calculations.target_calories`.
- A day is a date in the person's local calendar (`date.today()` as everywhere else in the app); dates in the UI use `shortdate`.
- Match surrounding style (sparse comments, no emojis). Write Python files with the Write tool, not shell heredocs with backslashes.

## Review Focus

1. A person with no profile or no weigh-in can still log food; the tab says what is missing instead of failing. (Task 5)
2. A food edited or deleted after it was logged: old logs and day totals do not move. (Task 4)
3. Fulfilment percent never divides by zero; floating-point sums are rounded only for display. (Task 2)
4. Boundary numbers: servings 0.1 and 50, calories exactly 5000, a 120-character name. (Task 3)
5. The old `?tab=macros` address and the dashboard link keep working. (Task 5)
6. Account deletion removes a person's foods and logs but never a starter food. (Task 6)

## File Structure

- Create `app/food/__init__.py`, `app/food/targets.py` (pure maths), `app/food/summary.py` (database day summary), `app/food/foods.py` (validation, search, starter loader), `app/food/logs.py` (add, edit, delete entries).
- Create `app/food/starter_foods.json` (shipped data) and `tools/build_starter_foods.py` + `tools/starter_food_list.txt` (how it is built).
- Create `migrations/versions/0038_food.py`; edit `app/models.py`, `app/main.py`.
- Create `app/routers/food.py`; edit `app/routers/measurements.py`, `app/routers/settings.py` (account deletion), `app/backup/sections.py`, `app/backup/load.py`.
- Create `app/templates/measurements/_food.html`, `app/static/js/food.js`; edit `app/templates/measurements/index.html`, `app/templates/dashboard/index.html`, `app/static/css/app.css`.
- Tests: `tests/test_food_targets.py`, `tests/test_food_foods.py`, `tests/test_food_logs.py`, `tests/test_food_page.py`, `tests/test_food_backup.py`, `tests/test_food_starter_data.py`; update the existing Macros-tab tests in `tests/test_measurements_page.py`.

---

### Task 1: Schema

**Files:**
- Create: `migrations/versions/0038_food.py`, `tests/test_food_foods.py` (model part)
- Modify: `app/models.py`, `tests/conftest.py`

**Interfaces:**
- Produces: `app.models.Food` (`id, owner_id, source, name, serving, serving_g, calories, protein_g, carb_g, fat_g, fiber_g, created_at`), `app.models.FoodLog` (`id, owner_id, eaten_on, meal, food_id, name, serving, servings, calories, protein_g, carb_g, fat_g, fiber_g, created_at`), `FOOD_MEALS = ("breakfast", "lunch", "dinner", "snack")`, `FOOD_MEAL_LABELS`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_food_foods.py`:
```python
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Food, FoodLog


def mine(me, **kw):
    base = dict(owner_id=me, source="mine", name="Test oats", serving="1 cup", calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4)
    return Food(**{**base, **kw})


def test_a_food_and_a_log_round_trip(db, me):
    food = mine(me)
    db.add(food)
    db.commit()
    db.add(FoodLog(owner_id=me, eaten_on=date(2026, 10, 6), meal="breakfast", food_id=food.id, name=food.name,
                   serving=food.serving, servings=2, calories=300, protein_g=10, carb_g=54, fat_g=6, fiber_g=8))
    db.commit()
    log = db.query(FoodLog).one()
    assert (log.name, log.servings, log.calories, log.created_at is not None) == ("Test oats", 2, 300, True)


def test_the_database_refuses_bad_rows(db, me):
    for bad in (mine(me, calories=-1), mine(me, source="other"), mine(me, owner_id=None), Food(source="starter", owner_id=me, name="x", serving="y", calories=1, protein_g=0, carb_g=0, fat_g=0, fiber_g=0)):
        db.add(bad)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_a_person_cannot_have_the_same_food_and_serving_twice_and_starter_rows_are_unique_too(db, me):
    db.add(mine(me))
    db.commit()
    db.add(mine(me))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    starter = dict(source="starter", owner_id=None, name="Test egg", serving="1 large", calories=70, protein_g=6, carb_g=0.4, fat_g=5, fiber_g=0)
    db.add(Food(**starter))
    db.commit()
    db.add(Food(**starter))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_log_with_a_bad_meal_or_servings_is_refused(db, me):
    base = dict(owner_id=me, eaten_on=date(2026, 10, 6), name="x", serving="y", calories=1, protein_g=0, carb_g=0, fat_g=0, fiber_g=0)
    for extra in ({"meal": "brunch", "servings": 1}, {"meal": "lunch", "servings": 0}, {"meal": "lunch", "servings": 51}):
        db.add(FoodLog(**base, **extra))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
```

- [ ] **Step 2: Run to verify it fails** — `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_food_foods.py` → FAIL at import (`Food` missing).

- [ ] **Step 3: Implement**

`app/models.py`: near `BODY_PHOTO_ANGLES` add
```python
FOOD_MEALS = ("breakfast", "lunch", "dinner", "snack")
FOOD_MEAL_LABELS = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner", "snack": "Snacks"}
```
add `Index, text` to the sqlalchemy imports, and before `class JournalEntry(Base):` add
```python
class Food(Base):
    """A food with its numbers for one serving. Starter foods (owner_id NULL) ship with the app and are read-only."""
    __tablename__ = "foods"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", "serving", name="uq_food_owner_name_serving"),
        Index("uq_food_starter_name_serving", "name", "serving", unique=True, sqlite_where=text("owner_id IS NULL")),
        CheckConstraint("source IN ('starter', 'mine')", name="ck_food_source"),
        CheckConstraint("(source = 'starter' AND owner_id IS NULL) OR (source = 'mine' AND owner_id IS NOT NULL)",
                        name="ck_food_owner_source"),
        CheckConstraint("calories >= 0 AND protein_g >= 0 AND carb_g >= 0 AND fat_g >= 0 AND fiber_g >= 0",
                        name="ck_food_nonnegative"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(120))
    serving: Mapped[str] = mapped_column(String(60))
    serving_g: Mapped[float | None] = mapped_column(Float)
    calories: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float)
    carb_g: Mapped[float] = mapped_column(Float)
    fat_g: Mapped[float] = mapped_column(Float)
    fiber_g: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FoodLog(Base):
    """One thing eaten. The name, serving and numbers are a snapshot taken when it was logged, so changing or deleting
    the food later never rewrites history."""
    __tablename__ = "food_logs"
    __table_args__ = (
        CheckConstraint("meal IN ('breakfast', 'lunch', 'dinner', 'snack')", name="ck_food_log_meal"),
        CheckConstraint("servings > 0 AND servings <= 50", name="ck_food_log_servings"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    eaten_on: Mapped[date] = mapped_column(Date, index=True)
    meal: Mapped[str] = mapped_column(String(10))
    food_id: Mapped[int | None] = mapped_column(ForeignKey("foods.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    serving: Mapped[str] = mapped_column(String(60))
    servings: Mapped[float] = mapped_column(Float)
    calories: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float)
    carb_g: Mapped[float] = mapped_column(Float)
    fat_g: Mapped[float] = mapped_column(Float)
    fiber_g: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

`migrations/versions/0038_food.py`:
```python
"""foods and food_logs

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0038'
down_revision: Union[str, None] = '0037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'foods',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE')),
        sa.Column('source', sa.String(10), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('serving', sa.String(60), nullable=False),
        sa.Column('serving_g', sa.Float()),
        sa.Column('calories', sa.Float(), nullable=False),
        sa.Column('protein_g', sa.Float(), nullable=False),
        sa.Column('carb_g', sa.Float(), nullable=False),
        sa.Column('fat_g', sa.Float(), nullable=False),
        sa.Column('fiber_g', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_id', 'name', 'serving', name='uq_food_owner_name_serving'),
        sa.CheckConstraint("source IN ('starter', 'mine')", name='ck_food_source'),
        sa.CheckConstraint("(source = 'starter' AND owner_id IS NULL) OR (source = 'mine' AND owner_id IS NOT NULL)",
                           name='ck_food_owner_source'),
        sa.CheckConstraint('calories >= 0 AND protein_g >= 0 AND carb_g >= 0 AND fat_g >= 0 AND fiber_g >= 0',
                           name='ck_food_nonnegative'),
    )
    op.create_index('ix_foods_owner_id', 'foods', ['owner_id'])
    op.create_index('uq_food_starter_name_serving', 'foods', ['name', 'serving'], unique=True,
                    sqlite_where=sa.text('owner_id IS NULL'))
    op.create_table(
        'food_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('eaten_on', sa.Date(), nullable=False),
        sa.Column('meal', sa.String(10), nullable=False),
        sa.Column('food_id', sa.Integer(), sa.ForeignKey('foods.id', ondelete='SET NULL')),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('serving', sa.String(60), nullable=False),
        sa.Column('servings', sa.Float(), nullable=False),
        sa.Column('calories', sa.Float(), nullable=False),
        sa.Column('protein_g', sa.Float(), nullable=False),
        sa.Column('carb_g', sa.Float(), nullable=False),
        sa.Column('fat_g', sa.Float(), nullable=False),
        sa.Column('fiber_g', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("meal IN ('breakfast', 'lunch', 'dinner', 'snack')", name='ck_food_log_meal'),
        sa.CheckConstraint('servings > 0 AND servings <= 50', name='ck_food_log_servings'),
    )
    op.create_index('ix_food_logs_owner_id', 'food_logs', ['owner_id'])
    op.create_index('ix_food_logs_eaten_on', 'food_logs', ['eaten_on'])


def downgrade() -> None:
    op.drop_index('ix_food_logs_eaten_on', table_name='food_logs')
    op.drop_index('ix_food_logs_owner_id', table_name='food_logs')
    op.drop_table('food_logs')
    op.drop_index('uq_food_starter_name_serving', table_name='foods')
    op.drop_index('ix_foods_owner_id', table_name='foods')
    op.drop_table('foods')
```

`tests/conftest.py`: import `Food, FoodLog` in the models import; in `clean()` add `s.query(FoodLog).delete()` then `s.query(Food).delete()` (before `s.commit()`).

- [ ] **Step 4: Run to verify it passes** — the new file and `tests/test_migrations.py`, then the full suite. Expected: PASS, except the backup registry guard test (`test_the_registry_covers_every_table_in_the_schema`), which stays red until Task 6 (note it and continue).

- [ ] **Step 5: Commit**
```bash
git add migrations/versions/0038_food.py app/models.py tests/conftest.py tests/test_food_foods.py
git commit -m "feat: food schema (foods with a read-only starter set, food logs with snapshots)"
```

---

### Task 2: Targets, totals, deficit and fulfilment (pure functions)

**Files:**
- Create: `app/food/__init__.py` (empty), `app/food/targets.py`, `tests/test_food_targets.py`

**Interfaces:**
- Consumes: `app.models.DietPreset`, `MacroGoal`, `BiologicalSex`; `app.measurements.calculations.target_calories`, `macros_for_preset`.
- Produces in `app/food/targets.py`:
  - `FIBER_G_PER_1000_KCAL = 14`, `KCAL_PER_LB = 3500`
  - `Targets(calories: int, protein_g: int, carb_g: int, fat_g: int, fiber_g: int, floored: bool, kcal_split: tuple[float, float, float])` (frozen dataclass; `kcal_split` is the protein, carb, fat calories)
  - `day_targets(tdee: float, goal: MacroGoal, preset: DietPreset, custom: tuple[int, int, int] | None, sex: BiologicalSex) -> Targets`
  - `Totals(calories, protein_g, carb_g, fat_g, fiber_g)` (frozen dataclass of floats) and `sum_totals(entries) -> Totals` (entries have the five attributes)
  - `deficit(tdee: float, workout_burn: float, eaten: float) -> dict` with `kcal` (int, positive = deficit), `surplus` (bool), `lb_per_week` (float, absolute, 1 decimal)
  - `fulfilment(eaten: float, target: float) -> dict` with `pct` (int, 0 when target is 0), `state` (`"under"` below 90, `"good"` 90-110 inclusive, `"over"` above 110), `remaining` (float, target - eaten)

- [ ] **Step 1: Write the failing tests** (`tests/test_food_targets.py`)

```python
from types import SimpleNamespace as N

import pytest

from app.food.targets import Totals, day_targets, deficit, fulfilment, sum_totals
from app.models import BiologicalSex, DietPreset, MacroGoal

MALE, FEMALE = BiologicalSex.MALE, BiologicalSex.FEMALE


def test_a_balanced_maintenance_day_splits_calories_by_the_preset():
    t = day_targets(2000, MacroGoal.MAINTAIN, DietPreset.BALANCED, None, MALE)
    assert (t.calories, t.protein_g, t.carb_g, t.fat_g) == (2000, 150, 200, 67)         # 30/40/30 -> 150g, 200g, 66.7g
    assert t.fiber_g == 28 and t.floored is False
    assert t.kcal_split == (600.0, 800.0, 600.0)


def test_the_goal_offset_is_applied_to_tdee():
    assert day_targets(2200, MacroGoal.MODERATE_LOSS, DietPreset.HIGH_PROTEIN, None, MALE).calories == 1700
    assert day_targets(2200, MacroGoal.SLOW_GAIN, DietPreset.HIGH_PROTEIN, None, MALE).calories == 2450


def test_each_diet_type_gives_its_own_split():
    keto = day_targets(2000, MacroGoal.MAINTAIN, DietPreset.KETO, None, MALE)
    assert (keto.protein_g, keto.carb_g, keto.fat_g) == (100, 25, 167)
    low = day_targets(2000, MacroGoal.MAINTAIN, DietPreset.LOW_CARB, None, MALE)
    assert (low.protein_g, low.carb_g, low.fat_g) == (200, 75, 100)


def test_a_custom_split_uses_the_given_percentages():
    t = day_targets(2000, MacroGoal.MAINTAIN, DietPreset.CUSTOM, (25, 50, 25), FEMALE)
    assert (t.protein_g, t.carb_g, t.fat_g) == (125, 250, 56)


def test_a_custom_split_that_does_not_total_100_is_an_error():
    with pytest.raises(ValueError):
        day_targets(2000, MacroGoal.MAINTAIN, DietPreset.CUSTOM, (30, 30, 30), MALE)
    with pytest.raises(ValueError):
        day_targets(2000, MacroGoal.MAINTAIN, DietPreset.CUSTOM, None, MALE)


def test_a_target_below_the_safe_floor_is_raised_and_flagged():
    t = day_targets(1400, MacroGoal.AGGRESSIVE_LOSS, DietPreset.BALANCED, None, FEMALE)
    assert t.calories == 1200 and t.floored is True
    assert day_targets(1900, MacroGoal.AGGRESSIVE_LOSS, DietPreset.BALANCED, None, MALE).calories == 1500


def test_totals_sum_entries_and_an_empty_day_is_all_zero():
    rows = [N(calories=300.5, protein_g=10, carb_g=40, fat_g=5, fiber_g=3), N(calories=199.5, protein_g=20.25, carb_g=0, fat_g=9, fiber_g=1.5)]
    assert sum_totals(rows) == Totals(500.0, 30.25, 40.0, 14.0, 4.5)
    assert sum_totals([]) == Totals(0.0, 0.0, 0.0, 0.0, 0.0)


def test_deficit_counts_tdee_plus_workout_minus_eaten():
    d = deficit(2400, 300, 1800)
    assert (d["kcal"], d["surplus"], d["lb_per_week"]) == (900, False, 1.8)         # 900 * 7 / 3500
    assert deficit(2400, 0, 2400)["kcal"] == 0


def test_eating_more_than_burned_is_a_surplus():
    d = deficit(2000, 100, 2600)
    assert (d["kcal"], d["surplus"], d["lb_per_week"]) == (-500, True, 1.0)


@pytest.mark.parametrize("eaten, state, pct", [(0, "under", 0), (89, "under", 89), (90, "good", 90), (110, "good", 110),
                                                (111, "over", 111), (250, "over", 250)])
def test_fulfilment_states_at_the_boundaries(eaten, state, pct):
    f = fulfilment(eaten, 100)
    assert (f["state"], f["pct"]) == (state, pct) and f["remaining"] == 100 - eaten


def test_fulfilment_never_divides_by_zero():
    assert fulfilment(50, 0) == {"pct": 0, "state": "under", "remaining": -50.0}
```

- [ ] **Step 2: Run to verify it fails** (module missing).

- [ ] **Step 3: Implement `app/food/targets.py`**
```python
"""Daily calorie and macro targets, totals, deficit and fulfilment. Pure functions: no database, no web."""

from dataclasses import dataclass

from app.measurements.calculations import macros_for_preset, target_calories
from app.models import BiologicalSex, DietPreset, MacroGoal

FIBER_G_PER_1000_KCAL = 14   # a common guideline; shown as such
KCAL_PER_LB = 3500
_KCAL_PER_G = (4, 4, 9)      # protein, carbs, fat


@dataclass(frozen=True)
class Targets:
    calories: int
    protein_g: int
    carb_g: int
    fat_g: int
    fiber_g: int
    floored: bool
    kcal_split: tuple[float, float, float]   # calories from protein, carbs, fat


@dataclass(frozen=True)
class Totals:
    calories: float
    protein_g: float
    carb_g: float
    fat_g: float
    fiber_g: float


def day_targets(tdee: float, goal: MacroGoal, preset: DietPreset, custom: tuple[int, int, int] | None,
                sex: BiologicalSex) -> Targets:
    """The day's limit (TDEE plus the goal offset, never below the safe floor) split by the diet type. Raises
    ValueError for a custom split that is missing or does not total 100."""
    calories, floored = target_calories(tdee, goal, sex)
    protein, carb, fat = macros_for_preset(calories, preset, custom)
    split = (protein * _KCAL_PER_G[0], carb * _KCAL_PER_G[1], fat * _KCAL_PER_G[2])
    return Targets(round(calories), round(protein), round(carb), round(fat),
                   round(calories / 1000 * FIBER_G_PER_1000_KCAL), floored, split)


def sum_totals(entries) -> Totals:
    entries = list(entries)
    return Totals(*(sum(getattr(e, field) for e in entries)
                    for field in ("calories", "protein_g", "carb_g", "fat_g", "fiber_g")))


def deficit(tdee: float, workout_burn: float, eaten: float) -> dict:
    """Potential deficit = what could be burned (TDEE plus logged workout calories) minus what was eaten. A negative
    number is a surplus. `lb_per_week` is the estimate if every day were like this one."""
    kcal = round(tdee + workout_burn - eaten)
    return {"kcal": kcal, "surplus": kcal < 0, "lb_per_week": round(abs(kcal) * 7 / KCAL_PER_LB, 1)}


def fulfilment(eaten: float, target: float) -> dict:
    pct = round(eaten / target * 100) if target else 0
    state = "under" if pct < 90 else "good" if pct <= 110 else "over"
    return {"pct": pct, "state": state, "remaining": float(target - eaten)}
```

- [ ] **Step 4: Run to verify it passes.** Adjust nothing in the tests to fit: if a rounding expectation disagrees (for example 67 vs 66.7 grams), fix the *implementation's* rounding choice only if the spec's meaning is violated; otherwise correct the arithmetic in the test and note it in the ledger.

- [ ] **Step 5: Commit**
```bash
git add app/food/__init__.py app/food/targets.py tests/test_food_targets.py
git commit -m "feat: food targets, totals, deficit and fulfilment (pure functions)"
```

---

### Task 3: Foods: validation, search, starter loader

**Files:**
- Create: `app/food/foods.py`, `app/food/starter_foods.json` (a small seed of 6 foods for now; Task 7 replaces it with the full list)
- Modify: `app/main.py` (load the starter foods at startup), `tests/test_food_foods.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces in `app/food/foods.py`:
  - `LIMITS = {"calories": 5000, "protein_g": 500, "carb_g": 500, "fat_g": 500, "fiber_g": 500}`
  - `parse_food(raw: dict) -> tuple[dict, dict]` (values: `name, serving, serving_g, calories, protein_g, carb_g, fat_g, fiber_g`; errors keyed by field)
  - `search(session, uid: int, query: str, limit: int = 25) -> list[Food]` (own foods first, then starter; every word of the query must appear in the name, case-insensitive; empty query returns the person's own foods, most recent first)
  - `load_starter(session, path: Path | None = None) -> dict` (`{"added": n, "updated": n}`; idempotent; never deletes)
  - `own_food(session, food_id: int, uid: int) -> Food` (raises `LookupError` for another person's food, a starter food or a missing id)
  - `copy_to_mine(session, uid: int, food: Food) -> Food` (a person's copy of a starter food, or the existing copy)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_food_foods.py`)

```python
import json

from app.food import foods as foods_mod
from app.food.foods import LIMITS, copy_to_mine, load_starter, own_food, parse_food, search
from photo_helpers import other_client
from app.models import User


def good(**kw):
    return {"name": "Greek yogurt", "serving": "3/4 cup", "calories": "100", "protein_g": "17", "carb_g": "6", "fat_g": "0.7", "fiber_g": "0", **kw}


def test_a_valid_food_parses_to_numbers():
    values, errors = parse_food(good(serving_g="170"))
    assert errors == {} and values["calories"] == 100.0 and values["serving_g"] == 170.0 and values["name"] == "Greek yogurt"


@pytest.mark.parametrize("field, bad", [("name", ""), ("name", "x" * 121), ("serving", ""), ("serving", "y" * 61), ("calories", "abc"),
                                        ("calories", "-1"), ("calories", "5000.1"), ("protein_g", "500.1"), ("fiber_g", "nan"),
                                        ("fat_g", "inf"), ("carb_g", "")])
def test_bad_input_is_rejected_per_field(field, bad):
    values, errors = parse_food(good(**{field: bad}))
    assert field in errors


def test_the_limits_themselves_are_allowed():
    _, errors = parse_food(good(name="x" * 120, serving="y" * 60, calories=str(LIMITS["calories"]), protein_g="500"))
    assert errors == {}


def seed(db, me):
    db.add_all([
        Food(source="starter", owner_id=None, name="Egg, whole, cooked", serving="1 large", calories=72, protein_g=6.3, carb_g=0.4, fat_g=4.8, fiber_g=0),
        Food(source="starter", owner_id=None, name="Chicken breast, cooked", serving="3 oz", calories=140, protein_g=26, carb_g=0, fat_g=3, fiber_g=0),
        Food(owner_id=me, source="mine", name="Egg white wrap", serving="1 wrap", calories=120, protein_g=12, carb_g=14, fat_g=2, fiber_g=3),
    ])
    db.commit()


def test_search_finds_own_and_starter_foods_by_every_word_own_first(client, db, me):
    seed(db, me)
    assert [f.name for f in search(db, me, "egg")] == ["Egg white wrap", "Egg, whole, cooked"]
    assert [f.name for f in search(db, me, "CHICKEN cooked")] == ["Chicken breast, cooked"]
    assert search(db, me, "egg quinoa") == []


def test_search_never_returns_another_persons_foods(client, db, me):
    seed(db, me)
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        assert [f.name for f in search(db, other_id, "egg")] == ["Egg, whole, cooked"]


def test_an_empty_search_lists_the_persons_own_foods(client, db, me):
    seed(db, me)
    assert [f.name for f in search(db, me, "")] == ["Egg white wrap"]


def test_search_treats_like_wildcards_as_plain_text(client, db, me):
    seed(db, me)
    assert search(db, me, "%") == [] and search(db, me, "_gg") == []


def test_own_food_refuses_other_peoples_and_starter_foods(client, db, me):
    seed(db, me)
    starter = db.query(Food).filter_by(source="starter").first()
    mine_food = db.query(Food).filter_by(source="mine").one()
    assert own_food(db, mine_food.id, me).id == mine_food.id
    with pytest.raises(LookupError):
        own_food(db, starter.id, me)
    with pytest.raises(LookupError):
        own_food(db, mine_food.id, me + 999)
    with pytest.raises(LookupError):
        own_food(db, 999999, me)


def test_copying_a_starter_food_makes_a_personal_copy_once(client, db, me):
    seed(db, me)
    starter = db.query(Food).filter_by(source="starter", name="Egg, whole, cooked").one()
    first = copy_to_mine(db, me, starter)
    assert (first.source, first.owner_id, first.calories) == ("mine", me, 72)
    assert copy_to_mine(db, me, starter).id == first.id


def test_the_starter_loader_adds_updates_and_is_idempotent_and_never_deletes(db, tmp_path):
    path = tmp_path / "starter.json"
    row = {"name": "Test rice", "serving": "1 cup cooked", "serving_g": 158, "calories": 205, "protein_g": 4.3, "carb_g": 45, "fat_g": 0.4, "fiber_g": 0.6}
    path.write_text(json.dumps([row]), encoding="utf-8")
    assert load_starter(db, path) == {"added": 1, "updated": 0}
    assert load_starter(db, path) == {"added": 0, "updated": 0}
    path.write_text(json.dumps([{**row, "calories": 210}]), encoding="utf-8")
    assert load_starter(db, path) == {"added": 0, "updated": 1}
    path.write_text(json.dumps([]), encoding="utf-8")
    load_starter(db, path)
    assert db.query(Food).filter_by(name="Test rice").one().calories == 210
    db.query(Food).filter_by(name="Test rice").delete()
    db.commit()


def test_the_starter_loader_ignores_a_malformed_row_and_keeps_the_rest(db, tmp_path):
    path = tmp_path / "starter.json"
    ok = {"name": "Test beans", "serving": "1/2 cup", "calories": 110, "protein_g": 7, "carb_g": 20, "fat_g": 0.5, "fiber_g": 7}
    path.write_text(json.dumps([{"name": "Broken"}, ok]), encoding="utf-8")
    assert load_starter(db, path)["added"] == 1
    db.query(Food).filter_by(name="Test beans").delete()
    db.commit()
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `app/food/foods.py`**
```python
"""Foods: validating a food, searching the starter list and a person's own foods, and loading the starter list."""

import json
import math
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Food

LIMITS = {"calories": 5000, "protein_g": 500, "carb_g": 500, "fat_g": 500, "fiber_g": 500}
LABELS = {"calories": "Calories", "protein_g": "Protein", "carb_g": "Carbs", "fat_g": "Fat", "fiber_g": "Fiber"}
STARTER_PATH = Path(__file__).with_name("starter_foods.json")


def _number(raw, field: str, errors: dict, upper: float) -> float | None:
    text = str(raw if raw is not None else "").strip()
    try:
        value = float(text)
    except ValueError:
        errors[field] = f"{LABELS.get(field, 'This')} must be a number."
        return None
    if not math.isfinite(value) or value < 0 or value > upper:
        errors[field] = f"{LABELS.get(field, 'This')} must be between 0 and {upper:g}."
        return None
    return value


def parse_food(raw: dict) -> tuple[dict, dict]:
    errors: dict[str, str] = {}
    values: dict = {}
    name = str(raw.get("name", "")).strip()
    serving = str(raw.get("serving", "")).strip()
    if not 1 <= len(name) <= 120:
        errors["name"] = "Name is required (120 characters at most)."
    if not 1 <= len(serving) <= 60:
        errors["serving"] = "Serving is required, for example 1 cup cooked (60 characters at most)."
    values["name"], values["serving"] = name, serving
    for field, upper in LIMITS.items():
        values[field] = _number(raw.get(field), field, errors, upper)
    grams = str(raw.get("serving_g", "") or "").strip()
    values["serving_g"] = _number(grams, "serving_g", errors, 5000) if grams else None
    return values, errors


def _words(query: str) -> list[str]:
    return [w for w in query.replace("%", " ").replace("_", " ").split() if w]


def search(session: Session, uid: int, query: str, limit: int = 25) -> list[Food]:
    """Foods visible to this person (their own and the starter list) whose name has every word of the query, their
    own first. An empty query lists their own foods, newest first."""
    if not query.strip():
        stmt = select(Food).where(Food.owner_id == uid).order_by(Food.created_at.desc(), Food.id.desc())
        return list(session.scalars(stmt.limit(limit)))
    words = _words(query)
    if not words:                          # only wildcard characters: nothing to match
        return []
    stmt = select(Food).where(or_(Food.owner_id == uid, Food.owner_id.is_(None)))
    for word in words:
        stmt = stmt.where(Food.name.ilike(f"%{word}%"))
    return list(session.scalars(stmt.order_by(Food.owner_id.is_(None), Food.name).limit(limit)))   # own foods first


def own_food(session: Session, food_id: int, uid: int) -> Food:
    food = session.get(Food, food_id)
    if food is None or food.owner_id != uid:
        raise LookupError("food not found")
    return food


def copy_to_mine(session: Session, uid: int, food: Food) -> Food:
    existing = session.scalar(select(Food).where(Food.owner_id == uid, Food.name == food.name, Food.serving == food.serving))
    if existing is not None:
        return existing
    mine = Food(owner_id=uid, source="mine", name=food.name, serving=food.serving, serving_g=food.serving_g,
                calories=food.calories, protein_g=food.protein_g, carb_g=food.carb_g, fat_g=food.fat_g, fiber_g=food.fiber_g)
    session.add(mine)
    session.commit()
    return mine


def load_starter(session: Session, path: Path | None = None) -> dict:
    """Insert missing starter foods and refresh changed ones from the shipped JSON. Never deletes (a logged starter food
    must stay). A malformed row is skipped."""
    rows = json.loads((path or STARTER_PATH).read_text(encoding="utf-8"))
    added = updated = 0
    for row in rows:
        values, errors = parse_food(row)
        if errors:
            continue
        food = session.scalar(select(Food).where(Food.owner_id.is_(None), Food.name == values["name"], Food.serving == values["serving"]))
        if food is None:
            session.add(Food(owner_id=None, source="starter", **values))
            added += 1
        elif any(getattr(food, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(food, k, v)
            updated += 1
    session.commit()
    return {"added": added, "updated": updated}
```

`app/food/starter_foods.json` (seed, replaced in Task 7): a JSON array of six rows with the keys `name, serving, serving_g, calories, protein_g, carb_g, fat_g, fiber_g` (for example eggs, chicken breast, white rice, oatmeal, banana, peanut butter), numbers from USDA values.

`app/main.py`: in `lifespan`, after `upgrade_db()` add
```python
    with SessionLocal() as session:
        load_starter(session)
```
with imports `from app.db import SessionLocal` and `from app.food.foods import load_starter` (check `SessionLocal` is not already imported there).

- [ ] **Step 4: Run to verify it passes**, then the full suite. If a test that counts foods is affected by the seeded starter rows, scope that test (never delete starter rows in `conftest.clean`: the loader repopulates them, but tests must not depend on their absence).

- [ ] **Step 5: Commit**
```bash
git add app/food/foods.py app/food/starter_foods.json app/main.py tests/test_food_foods.py
git commit -m "feat: foods (validation, search, copy) and the starter-food loader"
```

---

### Task 4: The Food tab (replaces Macros): summary, settings, pie chart, fulfilment

**Files:**
- Create: `app/food/summary.py`, `app/templates/measurements/_food.html`, `tests/test_food_page.py`
- Modify: `app/routers/measurements.py`, `app/templates/measurements/index.html`, `app/templates/dashboard/index.html`, `app/static/css/app.css`, `tests/test_measurements_page.py`; create `app/routers/food.py` (settings route only for now) and register it in `app/main.py`.

**Interfaces:**
- Consumes: Tasks 1-3; `app.measurements.tdee.report`; `app.workouts.progress` (`daily_net_kcal`, `in_range`); `app.routers.workout_insights.load_logged`; `app.workouts.logging.latest_body_weight`; `app.workouts.charts.ring`; `MACRO_COLORS` from `app.routers.workout_insights`.
- Produces in `app/food/summary.py`:
  - `parse_day(raw: str | None) -> date | None` (ISO date from 2000-01-01 to today + 7 days, else None)
  - `workout_burn(session, uid, day) -> float` (that day's net estimated workout kcal; 0 when none) - a separate function so tests can replace it
  - `day_summary(session, user, day) -> dict` with: always `status` (`ok`, `missing_profile`, `missing_weight`, `missing_custom_macros`, `error`), `day`, `meals` (list of `{key, label, entries, totals}` in breakfast, lunch, dinner, snack order), `eaten` (`Totals`); and for `ok`: `targets`, `tdee`, `workout_burn` (rounded int), `deficit`, `fulfil` (dict keyed `calories, protein, carb, fat, fiber`, each with `eaten, target, pct, state, remaining, kind`), `ring` (the `charts.ring` result for protein, carbs, fat calories) and `legend` (list of `{name, grams, pct, color}`); for the missing statuses `missing_fields` (a list of labels).
- Produces routes: `POST /food/settings` (fields `diet_preset`, `macro_goal`, `custom_protein_pct`, `custom_carb_pct`, `custom_fat_pct`, `date`); `/measurements?tab=food&date=...`; `?tab=macros` redirects (303) to `?tab=food`.

- [ ] **Step 1: Write the failing tests** (`tests/test_food_page.py`)

```python
import html
import re
from datetime import date, datetime, timedelta

import pytest

from app.food import summary as food_summary
from app.food.targets import day_targets
from app.measurements import tdee
from app.models import (ActivityLevel, BiologicalSex, BodyMeasurement, DietPreset, FoodLog, MacroGoal, User)
from photo_helpers import other_client


def profile(db, me, **kw):
    user = db.get(User, me)
    fields = dict(sex=BiologicalSex.MALE, birth_date=date(1990, 1, 1), height_in=70, activity_level=ActivityLevel.MODERATE,
                  macro_goal=MacroGoal.MODERATE_LOSS, diet_preset=DietPreset.BALANCED)
    for key, value in {**fields, **kw}.items():
        setattr(user, key, value)
    db.add(BodyMeasurement(owner_id=me, measured_at=datetime(2026, 10, 1, 8, 0), weight_lbs=190))
    db.commit()
    return user


def expected_tdee(user):
    today = date.today()
    age = today.year - user.birth_date.year - ((today.month, today.day) < (user.birth_date.month, user.birth_date.day))
    return tdee.report(male=True, age=age, height_in=user.height_in, weight_lb=190, activity_factor=float(user.activity_level.value),
                       life_stage=user.life_stage).tdee


def entry(db, me, day, meal="lunch", name="Test rice", **kw):
    row = dict(owner_id=me, eaten_on=day, meal=meal, name=name, serving="1 cup", servings=1, calories=200, protein_g=4, carb_g=44, fat_g=0.5, fiber_g=1)
    db.add(FoodLog(**{**row, **kw}))
    db.commit()


def page(client, query=""):
    return html.unescape(client.get(f"/measurements?tab=food{query}").text)


def reset_profile(db, me):
    user = db.get(User, me)
    for field in ("sex", "birth_date", "height_in", "activity_level", "macro_goal", "diet_preset", "custom_protein_pct",
                  "custom_carb_pct", "custom_fat_pct"):
        setattr(user, field, None)
    db.commit()


@pytest.fixture(autouse=True)
def clean_profile(client, db, me):
    yield
    reset_profile(db, me)


def test_the_food_tab_replaces_macros_and_the_old_address_still_works(client, db, me):
    r = client.get("/measurements?tab=macros", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/measurements?tab=food"
    text = client.get("/measurements?tab=measurements").text
    assert "tab=food" in text and ">Food<" in text and "tab=macros" not in text


def test_without_a_profile_the_tab_says_what_is_missing_but_the_log_still_shows(client, db, me):
    entry(db, me, date.today(), name="Plain toast")
    text = page(client)
    assert "body profile" in text.lower() and "Plain toast" in text


def test_without_a_weigh_in_the_tab_asks_for_one(client, db, me):
    profile(db, me)
    db.query(BodyMeasurement).delete()
    db.commit()
    assert "weigh-in" in page(client).lower()


def test_a_custom_diet_with_no_percentages_asks_for_them(client, db, me):
    profile(db, me, diet_preset=DietPreset.CUSTOM)
    assert "custom" in page(client).lower() and "percent" in page(client).lower()


def test_the_calorie_limit_is_the_energy_tabs_tdee_plus_the_goal_offset(client, db, me):
    user = profile(db, me)
    limit = day_targets(expected_tdee(user), user.macro_goal, user.diet_preset, None, user.sex).calories
    assert f"{limit:,}" in page(client)


def test_eaten_remaining_and_the_deficit_with_workout_burn(client, db, me, monkeypatch):
    user = profile(db, me)
    monkeypatch.setattr(food_summary, "workout_burn", lambda session, uid, day: 300.0)
    entry(db, me, date.today(), calories=500)
    entry(db, me, date.today(), calories=400, meal="dinner", name="Second")
    t = expected_tdee(user)
    limit = day_targets(t, user.macro_goal, user.diet_preset, None, user.sex).calories
    text = page(client)
    assert "900" in text and f"{limit - 900:,}" in text                      # eaten and remaining
    assert f"{t + 300 - 900:,}" in text and "estimate" in text.lower()       # potential deficit
    summary = food_summary.day_summary(db, user, date.today())
    assert summary["deficit"]["kcal"] == t + 300 - 900 and summary["workout_burn"] == 300


def test_eating_more_than_burned_is_shown_as_a_surplus(client, db, me, monkeypatch):
    user = profile(db, me)
    monkeypatch.setattr(food_summary, "workout_burn", lambda session, uid, day: 0.0)
    entry(db, me, date.today(), calories=4000, protein_g=10, carb_g=10, fat_g=10)
    assert "surplus" in page(client).lower()


def test_the_pie_chart_and_legend_follow_the_diet_type(client, db, me):
    profile(db, me, diet_preset=DietPreset.KETO)
    text = page(client)
    assert "<svg" in text and re.search(r"Fat[^%]{0,80}75%", text) and re.search(r"Protein[^%]{0,80}20%", text)
    profile_user = db.get(User, me)
    profile_user.diet_preset = DietPreset.HIGH_PROTEIN
    db.commit()
    text = page(client)
    assert re.search(r"Protein[^%]{0,80}40%", text) and re.search(r"Carbs[^%]{0,80}30%", text)


def test_fulfilment_bars_show_eaten_against_target_with_a_state(client, db, me):
    profile(db, me)
    entry(db, me, date.today(), calories=100, protein_g=1, carb_g=1, fat_g=1, fiber_g=0)
    text = page(client)
    for label in ("Calories", "Protein", "Carbs", "Fat", "Fiber"):
        assert label in text
    assert 'data-state="under"' in text


def test_meals_are_listed_in_order_with_their_subtotals(client, db, me):
    profile(db, me)
    entry(db, me, date.today(), meal="dinner", name="Supper dish", calories=600)
    entry(db, me, date.today(), meal="breakfast", name="Morning oats", calories=250)
    text = page(client)
    assert text.index("Morning oats") < text.index("Supper dish")
    for heading in ("Breakfast", "Lunch", "Dinner", "Snacks"):
        assert heading in text


def test_another_day_shows_that_days_entries_and_a_bad_date_means_today(client, db, me):
    profile(db, me)
    yesterday = date.today() - timedelta(days=1)
    entry(db, me, yesterday, name="Yesterday stew")
    assert "Yesterday stew" in page(client, f"&date={yesterday.isoformat()}")
    assert "Yesterday stew" not in page(client)
    assert "Yesterday stew" not in page(client, "&date=garbage")
    assert "Yesterday stew" not in page(client, "&date=1999-01-01")


def test_other_peoples_entries_never_appear(client, db, me):
    profile(db, me)
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        entry(db, other_id, date.today(), name="Someone elses lunch")
        assert "Someone elses lunch" not in page(client)
        assert "Someone elses lunch" in html.unescape(other.get("/measurements?tab=food").text)


def test_changing_the_diet_type_and_goal_saves_to_the_profile(client, db, me):
    profile(db, me)
    r = client.post("/food/settings", data={"diet_preset": "keto", "macro_goal": "0"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/measurements?tab=food")
    db.expire_all()
    user = db.get(User, me)
    assert (user.diet_preset, user.macro_goal) == (DietPreset.KETO, MacroGoal.MAINTAIN)


def test_a_custom_split_must_total_100_and_is_saved_when_it_does(client, db, me):
    profile(db, me)
    bad = client.post("/food/settings", data={"diet_preset": "custom", "macro_goal": "0", "custom_protein_pct": "30",
                                              "custom_carb_pct": "30", "custom_fat_pct": "30"})
    assert bad.status_code == 422 and "100" in html.unescape(bad.text)
    assert db.get(User, me).diet_preset == DietPreset.BALANCED
    ok = client.post("/food/settings", data={"diet_preset": "custom", "macro_goal": "0", "custom_protein_pct": "30",
                                             "custom_carb_pct": "40", "custom_fat_pct": "30"}, follow_redirects=False)
    assert ok.status_code == 303
    db.expire_all()
    assert (db.get(User, me).custom_protein_pct, db.get(User, me).diet_preset) == (30, DietPreset.CUSTOM)


def test_unknown_diet_or_goal_values_are_rejected(client, db, me):
    profile(db, me)
    assert client.post("/food/settings", data={"diet_preset": "carnivore", "macro_goal": "0"}).status_code == 422
    assert client.post("/food/settings", data={"diet_preset": "keto", "macro_goal": "99"}).status_code == 422


def test_the_dashboard_water_link_points_at_the_food_tab(client, db, me):
    assert "/measurements?tab=food" in client.get("/dashboard").text
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`app/food/summary.py`:
```python
"""A day of eating for the Food tab: the entries by meal, the totals, and (when the profile allows) the targets."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.food.targets import day_targets, deficit, fulfilment, sum_totals
from app.measurements import tdee
from app.models import FOOD_MEAL_LABELS, FOOD_MEALS, DietPreset, FoodLog, MacroGoal, User
from app.workouts import charts, progress
from app.workouts import logging as workout_logging

MACRO_COLORS = {"Protein": "#2563eb", "Carbs": "#ca8a04", "Fat": "#db2777"}
_MAX_AHEAD = timedelta(days=7)


def parse_day(raw: str | None) -> date | None:
    try:
        day = date.fromisoformat((raw or "").strip())
    except ValueError:
        return None
    return day if date(2000, 1, 1) <= day <= date.today() + _MAX_AHEAD else None


def workout_burn(session: Session, uid: int, day: date) -> float:
    """That day's net estimated workout calories, the same number the Energy tab charts."""
    from app.routers.workout_insights import load_logged     # imported here: that module imports routers and templates
    return progress.daily_net_kcal(progress.in_range(load_logged(session, uid), day, day)).get(day, 0.0)


def _age(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def day_summary(session: Session, user: User, day: date) -> dict:
    rows = session.scalars(select(FoodLog).where(FoodLog.owner_id == user.id, FoodLog.eaten_on == day)
                           .order_by(FoodLog.id)).all()
    meals = [{"key": key, "label": FOOD_MEAL_LABELS[key], "entries": [r for r in rows if r.meal == key],
              "totals": sum_totals([r for r in rows if r.meal == key])} for key in FOOD_MEALS]
    summary = {"status": "ok", "day": day, "meals": meals, "eaten": sum_totals(rows), "entries": rows}

    labels = (("sex", "sex"), ("birth_date", "birth date"), ("height_in", "height"), ("activity_level", "activity level"))
    missing = [label for attr, label in labels if getattr(user, attr) is None]
    if missing:
        return summary | {"status": "missing_profile", "missing_fields": missing}
    weight = workout_logging.latest_body_weight(session, user.id)
    if weight is None:
        return summary | {"status": "missing_weight", "missing_fields": ["a weigh-in"]}
    preset = user.diet_preset or DietPreset.BALANCED
    custom = None
    if preset == DietPreset.CUSTOM:
        if None in (user.custom_protein_pct, user.custom_carb_pct, user.custom_fat_pct):
            return summary | {"status": "missing_custom_macros"}
        custom = (user.custom_protein_pct, user.custom_carb_pct, user.custom_fat_pct)

    report = tdee.report(male=user.sex.value == "male", age=_age(user.birth_date, date.today()), height_in=user.height_in,
                         weight_lb=weight, activity_factor=float(user.activity_level.value), life_stage=user.life_stage)
    try:
        targets = day_targets(report.tdee, user.macro_goal or MacroGoal.MAINTAIN, preset, custom, user.sex)
    except ValueError as exc:
        return summary | {"status": "error", "message": str(exc)}
    burn = workout_burn(session, user.id, day)
    eaten = summary["eaten"]
    fulfil = {
        "calories": fulfilment(eaten.calories, targets.calories) | {"eaten": eaten.calories, "target": targets.calories, "kind": "limit"},
        "protein": fulfilment(eaten.protein_g, targets.protein_g) | {"eaten": eaten.protein_g, "target": targets.protein_g, "kind": "minimum"},
        "carb": fulfilment(eaten.carb_g, targets.carb_g) | {"eaten": eaten.carb_g, "target": targets.carb_g, "kind": "target"},
        "fat": fulfilment(eaten.fat_g, targets.fat_g) | {"eaten": eaten.fat_g, "target": targets.fat_g, "kind": "limit"},
        "fiber": fulfilment(eaten.fiber_g, targets.fiber_g) | {"eaten": eaten.fiber_g, "target": targets.fiber_g, "kind": "minimum"},
    }
    names = ("Protein", "Carbs", "Fat")
    ring = charts.ring(list(zip(names, targets.kcal_split)))
    grams = (targets.protein_g, targets.carb_g, targets.fat_g)
    total = sum(targets.kcal_split) or 1
    legend = [{"name": n, "grams": g, "pct": round(k / total * 100), "color": MACRO_COLORS[n]}
              for n, g, k in zip(names, grams, targets.kcal_split)]
    return summary | {"targets": targets, "tdee": report.tdee, "workout_burn": round(burn),
                      "deficit": deficit(report.tdee, burn, eaten.calories), "fulfil": fulfil, "ring": ring,
                      "legend": legend, "colors": MACRO_COLORS}
```

`app/routers/measurements.py`: `TABS = ("measurements", "food", "journal", "labs")`; in `list_measurements` add a `date_param: str | None = Query(None, alias="date")` parameter and at its top `if tab == "macros": return RedirectResponse("/measurements?tab=food", status_code=303)`; pass the date through `extra`. In `_render`: delete the `"macros": _macros_context(...)` context entry and delete `_macros_context` (and any import left unused); after the photo block add
```python
    if tab == "food" and me_user:
        from app.food import summary as food_summary
        day = food_summary.parse_day((extra or {}).get("food_date_raw") or request.query_params.get("date")) or date.today()
        if isinstance((extra or {}).get("food_date"), date):
            day = extra["food_date"]
        context.update(food=food_summary.day_summary(session, me_user, day), food_day=day,
                       food_prev=(day - timedelta(days=1)).isoformat(), food_next=(day + timedelta(days=1)).isoformat(),
                       diet_presets=list(DietPreset), macro_goals=list(MacroGoal), me=me_user)
```
(`DietPreset`, `MacroGoal` are already imported there.) The existing water section stays inside the Food tab.

`app/routers/food.py` (settings route only for now):
```python
"""Food: diet type and goal, foods and the day's log."""

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.food import summary as food_summary
from app.models import DietPreset, MacroGoal, User

router = APIRouter()


def _back(day: date, meal: str | None = None) -> RedirectResponse:
    return RedirectResponse(f"/measurements?tab=food&date={day.isoformat()}" + (f"#meal-{meal}" if meal else ""), status_code=303)


def _fail(request: Request, session: Session, uid: int, day: date, errors: dict, form: dict | None = None):
    from app.routers import measurements
    return measurements._render(request, session, uid, tab="food", status_code=422,
                                extra={"food_error": errors, "food_form": form or {}, "food_date": day})


@router.post("/food/settings")
async def food_settings(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    values = {k: str(form.get(k, "")).strip() for k in ("diet_preset", "macro_goal", "custom_protein_pct", "custom_carb_pct", "custom_fat_pct", "date")}
    day = food_summary.parse_day(values["date"]) or date.today()
    errors: dict[str, str] = {}
    try:
        preset = DietPreset(values["diet_preset"])
    except ValueError:
        preset = None
        errors["diet_preset"] = "Choose a diet type from the list."
    try:
        goal = MacroGoal(values["macro_goal"])
    except ValueError:
        goal = None
        errors["macro_goal"] = "Choose a goal from the list."
    custom = None
    if preset == DietPreset.CUSTOM:
        try:
            custom = tuple(int(values[k]) for k in ("custom_protein_pct", "custom_carb_pct", "custom_fat_pct"))
        except ValueError:
            errors["custom"] = "Enter whole-number percentages for protein, carbs and fat."
        else:
            if any(not 0 <= v <= 100 for v in custom) or sum(custom) != 100:
                errors["custom"] = "The three percentages must total 100."
    if errors:
        return _fail(request, session, uid, day, errors, values)
    user = session.get(User, uid)
    user.diet_preset, user.macro_goal = preset, goal
    if custom:
        user.custom_protein_pct, user.custom_carb_pct, user.custom_fat_pct = custom
    session.commit()
    return _back(day)
```
Register in `app/main.py` (`food` in the router import list and `app.include_router(food.router)`).

`app/templates/measurements/index.html`: the tab link row: replace the Macros link with
`<a href="/measurements?tab=food" class="tab {{ 'active' if active_tab == 'food' }}" role="tab" aria-selected="{{ 'true' if active_tab == 'food' else 'false' }}">Food</a>`; replace the whole `{% elif active_tab == 'macros' %}` block's body (the Macros section) with `{% elif active_tab == 'food' %}` followed by `{% include "measurements/_food.html" %}` and then the existing `{% if water %}...{% endif %}` Water goal block unchanged. `app/templates/dashboard/index.html` line with `tab=macros` becomes `tab=food`.

`app/templates/measurements/_food.html` (the section; entries are read-only in this task, Task 5 adds the forms):
```html
<section aria-labelledby="food-heading" id="food">
  <div class="food-head">
    <h2 id="food-heading" class="section-title">Food</h2>
    <div class="food-date">
      <a class="btn btn-ghost" href="/measurements?tab=food&date={{ food_prev }}" aria-label="Previous day">&larr;</a>
      <strong>{{ food_day | shortdate }}</strong>
      <a class="btn btn-ghost" href="/measurements?tab=food&date={{ food_next }}" aria-label="Next day">&rarr;</a>
    </div>
  </div>

  <form method="post" action="/food/settings" class="food-settings">
    <input type="hidden" name="date" value="{{ food_day.isoformat() }}">
    <label class="field"><span>Diet type</span>
      <select name="diet_preset" data-food-diet>
        {% for p in diet_presets %}<option value="{{ p.value }}" {{ 'selected' if (me.diet_preset or diet_presets[0]) == p }}>{{ p.label }}</option>{% endfor %}
      </select></label>
    <label class="field"><span>Goal</span>
      <select name="macro_goal">
        {% for g in macro_goals %}<option value="{{ g.value }}" {{ 'selected' if (me.macro_goal or macro_goals[3]) == g }}>{{ g.label }}</option>{% endfor %}
      </select></label>
    <div class="food-custom" data-food-custom {{ '' if me.diet_preset and me.diet_preset.value == 'custom' else 'hidden' }}>
      <label class="field"><span>Protein %</span><input name="custom_protein_pct" type="number" min="0" max="100" value="{{ me.custom_protein_pct if me.custom_protein_pct is not none else '' }}"></label>
      <label class="field"><span>Carbs %</span><input name="custom_carb_pct" type="number" min="0" max="100" value="{{ me.custom_carb_pct if me.custom_carb_pct is not none else '' }}"></label>
      <label class="field"><span>Fat %</span><input name="custom_fat_pct" type="number" min="0" max="100" value="{{ me.custom_fat_pct if me.custom_fat_pct is not none else '' }}"></label>
    </div>
    <button type="submit" class="btn">Save</button>
  </form>
  {% for key in ('diet_preset', 'macro_goal', 'custom') %}{% if food_error and food_error[key] %}<p class="error small" role="alert">{{ food_error[key] }}</p>{% endif %}{% endfor %}

  {% if food.status == 'missing_profile' %}
  <div class="empty"><p><strong>Add your body profile to see your calorie limit.</strong></p>
    <p class="muted">We need your {{ food.missing_fields | join(', ') }}. You can still log food below.</p>
    <p><a href="/settings">Go to Settings</a></p></div>
  {% elif food.status == 'missing_weight' %}
  <div class="empty"><p><strong>Log a weigh-in to see your calorie limit.</strong></p>
    <p class="muted">Add a weight on the Measurements tab first. You can still log food below.</p></div>
  {% elif food.status == 'missing_custom_macros' %}
  <div class="empty"><p><strong>Add your custom macro percentages.</strong></p>
    <p class="muted">Your diet type is Custom: enter the protein, carb and fat percent above (they must total 100) and save.</p></div>
  {% elif food.status == 'error' %}
  <div class="empty"><p>{{ food.message }}</p></div>
  {% else %}
  {% set t = food.targets %}{% set e = food.eaten %}{% set d = food.deficit %}
  {% if t.floored %}<p class="alert" role="alert">Adjusted: your calculated limit was below a safe minimum, so it was raised to a safe floor.</p>{% endif %}
  <div class="card food-calories">
    <p class="stat-big">{{ '{:,}'.format(t.calories) }} kcal limit</p>
    <ul class="kv-list">
      <li>Eaten: <strong>{{ '{:,}'.format(e.calories | round | int) }}</strong> kcal</li>
      <li>{{ 'Remaining' if e.calories <= t.calories else 'Over by' }}: <strong>{{ '{:,}'.format((t.calories - e.calories) | abs | round | int) }}</strong> kcal</li>
      <li>TDEE: {{ '{:,}'.format(food.tdee) }} kcal &middot; logged workout burn: {{ '{:,}'.format(food.workout_burn) }} kcal</li>
      <li>Potential {{ 'surplus' if d.surplus else 'deficit' }}: <strong>{{ '{:,}'.format(d.kcal | abs) }}</strong> kcal
        <span class="muted small">(TDEE + workout burn - eaten; about {{ d.lb_per_week }} lb per week if every day were like this: an estimate)</span></li>
    </ul>
  </div>

  <div class="food-split">
    {% if food.ring %}
    <svg viewBox="0 0 {{ food.ring.size }} {{ food.ring.size }}" class="food-ring" role="img"
         aria-label="Calories from protein, carbs and fat for your diet type">
      {% for s in food.ring.slices %}<path d="{{ s.path }}" fill="{{ food.colors[s.name] }}"><title>{{ s.name }} {{ (s.share * 100) | round | int }}%</title></path>{% endfor %}
    </svg>
    {% endif %}
    <ul class="food-legend">
      {% for item in food.legend %}<li><span class="swatch" style="background: {{ item.color }}"></span> {{ item.name }}: <strong>{{ item.grams }} g</strong> <span class="muted">({{ item.pct }}% of calories)</span></li>{% endfor %}
    </ul>
  </div>

  <h3 class="small muted">Today against your targets</h3>
  <div class="fulfil">
    {% for key, label, unit in (('calories', 'Calories', 'kcal'), ('protein', 'Protein', 'g'), ('carb', 'Carbs', 'g'), ('fat', 'Fat', 'g'), ('fiber', 'Fiber', 'g')) %}
    {% set f = food.fulfil[key] %}
    <div class="fulfil-row" data-state="{{ f.state }}">
      <div class="fulfil-label"><strong>{{ label }}</strong>
        <span class="muted small">{{ {'limit': 'limit', 'minimum': 'aim for at least', 'target': 'target'}[f.kind] }}</span></div>
      <div class="fulfil-bar" role="progressbar" aria-valuenow="{{ f.pct }}" aria-valuemin="0" aria-valuemax="100" aria-label="{{ label }}">
        <div class="fulfil-fill" style="width: {{ [f.pct, 100] | min }}%"></div></div>
      <div class="fulfil-num">{{ f.eaten | round(1) }} / {{ f.target }} {{ unit }} ({{ f.pct }}%)</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  <h3 class="section-title">Log</h3>
  {% for meal in food.meals %}
  <div class="food-meal" id="meal-{{ meal.key }}">
    <div class="food-meal-head"><h4>{{ meal.label }}</h4>
      <span class="muted small">{{ meal.totals.calories | round | int }} kcal &middot; P {{ meal.totals.protein_g | round | int }} &middot; C {{ meal.totals.carb_g | round | int }} &middot; F {{ meal.totals.fat_g | round | int }}</span></div>
    {% if meal.entries %}
    <ul class="food-entries">
      {% for en in meal.entries %}
      <li><span><strong>{{ en.name }}</strong> <span class="muted small">{{ en.servings | round(2) }} &times; {{ en.serving }}</span></span>
        <span class="small">{{ en.calories | round | int }} kcal &middot; P {{ en.protein_g | round(1) }} &middot; C {{ en.carb_g | round(1) }} &middot; F {{ en.fat_g | round(1) }} &middot; fiber {{ en.fiber_g | round(1) }}</span></li>
      {% endfor %}
    </ul>
    {% else %}<p class="muted small">Nothing logged.</p>{% endif %}
  </div>
  {% endfor %}
</section>
```

`app/static/css/app.css` — append:
```css
/* ---------- food ---------- */
.food-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.food-date { display: flex; align-items: center; gap: 8px; }
.food-settings { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
.food-custom { display: flex; gap: 8px; }
.food-calories .kv-list { list-style: none; padding: 0; margin: 8px 0 0; }
.food-split { display: flex; align-items: center; gap: 24px; flex-wrap: wrap; margin: 16px 0; }
.food-ring { width: 180px; height: 180px; }
.food-legend { list-style: none; padding: 0; margin: 0; }
.swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -1px; }
.fulfil-row { display: grid; grid-template-columns: 150px 1fr 190px; align-items: center; gap: 12px; margin: 8px 0; }
.fulfil-bar { height: 12px; border-radius: 6px; background: var(--bg); border: 1px solid var(--border); overflow: hidden; }
.fulfil-fill { height: 100%; background: var(--muted); }
.fulfil-row[data-state="good"] .fulfil-fill { background: #059669; }
.fulfil-row[data-state="over"] .fulfil-fill { background: #dc2626; }
.fulfil-num { font-size: 0.85rem; text-align: right; }
.food-meal { border-top: 1px solid var(--border); padding: 10px 0; }
.food-meal-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.food-entries { list-style: none; padding: 0; margin: 6px 0; display: grid; gap: 6px; }
.food-entries li { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
```

Update `tests/test_measurements_page.py`: find the tests that exercise the Macros tab (search for `tab=macros` and the strings `kcal/day`, `Macros`) and rewrite each to the Food tab's equivalent (same intent: missing-profile prompt, missing-weight prompt, custom-macros prompt, safe-floor notice, water goal still shown on this tab). A test that asserted the old calorie arithmetic is replaced by `test_the_calorie_limit_is_the_energy_tabs_tdee_plus_the_goal_offset` above and removed.

- [ ] **Step 4: Run to verify it passes**, then the full suite. Fix each remaining failure by reading it, never by loosening an assertion that guards behavior this plan keeps.

- [ ] **Step 5: Browser check, then commit.** Render the tab against a throwaway database and look at it in the in-app browser (pie chart, bars, controls; change the diet type to Keto and confirm the legend and ring change). Then:
```bash
git add app/food/summary.py app/routers/food.py app/routers/measurements.py app/main.py app/templates/measurements/ app/templates/dashboard/index.html app/static/css/app.css tests/test_food_page.py tests/test_measurements_page.py
git commit -m "feat: Food tab replaces Macros: calorie limit, deficit, diet type, pie chart and fulfilment bars"
```

---

### Task 5: Logging: add, edit and delete entries, My foods, search, and the Add food dialog

**Files:**
- Create: `app/food/logs.py`, `app/static/js/food.js`, `tests/test_food_logs.py`
- Modify: `app/routers/food.py`, `app/templates/measurements/_food.html`, `app/static/css/app.css`

**Interfaces:**
- Consumes: Tasks 1-4 (`foods.parse_food`, `foods.search`, `foods.own_food`, `foods.copy_to_mine`, `summary.parse_day`, `food._back`, `food._fail`).
- Produces in `app/food/logs.py`:
  - `parse_servings(raw) -> tuple[float | None, str | None]` (a number greater than 0 and at most 50, else a message)
  - `snapshot(food_like, servings: float) -> dict` (the five numbers times servings, rounded to 2 decimals, plus `name` and `serving`)
  - `add_entry(session, uid, day, meal, values: dict, servings: float, food_id: int | None) -> FoodLog`
  - `edit_entry(session, uid, log_id, *, servings: float | None, meal: str | None) -> FoodLog` (rescales from the entry's own stored numbers, never from the food)
  - `delete_entry(session, uid, log_id) -> None` (both raise `LookupError` for another person's entry)
- Produces routes: `POST /food/log`, `POST /food/log/{id}/edit`, `POST /food/log/{id}/delete`, `GET /food/search?q=`, `POST /food/foods`, `POST /food/foods/{id}/edit`, `POST /food/foods/{id}/delete`, `POST /food/foods/{id}/copy`.

- [ ] **Step 1: Write the failing tests** (`tests/test_food_logs.py`)

```python
import html
from datetime import date, timedelta

import pytest

from app.food.logs import parse_servings
from app.models import Food, FoodLog, User
from photo_helpers import other_client

TODAY = date.today().isoformat()


def mine(db, me, **kw):
    food = Food(**{**dict(owner_id=me, source="mine", name="Test oats", serving="1 cup", calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4), **kw})
    db.add(food)
    db.commit()
    return food


def starter(db, **kw):
    food = Food(**{**dict(owner_id=None, source="starter", name="Test starter egg", serving="1 large", calories=72, protein_g=6.3, carb_g=0.4, fat_g=4.8, fiber_g=0), **kw})
    db.add(food)
    db.commit()
    return food


def post_log(client, **fields):
    data = {"date": TODAY, "meal": "breakfast", "mode": "existing", "servings": "1", **fields}
    return client.post("/food/log", data=data, follow_redirects=False)


def logs(db):
    db.expire_all()
    return db.query(FoodLog).order_by(FoodLog.id).all()


@pytest.mark.parametrize("raw, ok", [("1", True), ("0.1", True), ("50", True), (" 2.5 ", True), ("0", False), ("-1", False), ("50.1", False),
                                     ("abc", False), ("", False), ("nan", False), ("inf", False)])
def test_servings_limits(raw, ok):
    value, error = parse_servings(raw)
    assert (error is None) == ok and (value is not None) == ok


def test_logging_an_existing_food_stores_a_scaled_snapshot(client, db, me):
    food = mine(db, me)
    r = post_log(client, food_id=food.id, servings="2")
    assert r.status_code == 303 and r.headers["location"] == f"/measurements?tab=food&date={TODAY}#meal-breakfast"
    (log,) = logs(db)
    assert (log.name, log.serving, log.servings, log.calories, log.protein_g, log.carb_g, log.fat_g, log.fiber_g, log.food_id) == (
        "Test oats", "1 cup", 2, 300, 10, 54, 6, 8, food.id)


def test_a_starter_food_can_be_logged(client, db, me):
    egg = starter(db)
    assert post_log(client, food_id=egg.id).status_code == 303 and logs(db)[0].name == "Test starter egg"


def test_another_persons_food_cannot_be_logged(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        theirs = mine(db, other_id, name="Their private food")
        r = post_log(client, food_id=theirs.id)
        assert r.status_code == 404 and logs(db) == []


def test_bad_servings_date_or_meal_are_refused_and_nothing_is_saved(client, db, me):
    food = mine(db, me)
    for fields in ({"servings": "0"}, {"servings": "51"}, {"meal": "brunch"}, {"date": "garbage"}, {"date": "1999-01-01"}):
        r = post_log(client, food_id=food.id, **fields)
        assert r.status_code == 422, fields
    assert logs(db) == []


def test_an_error_page_shows_the_message(client, db, me):
    food = mine(db, me)
    r = post_log(client, food_id=food.id, servings="0")
    assert "servings" in html.unescape(r.text).lower()


def test_create_mode_saves_a_my_food_once_and_logs_it(client, db, me):
    fields = dict(mode="create", name="Cottage cheese", serving="1/2 cup", calories="90", protein_g="12", carb_g="5", fat_g="2.5", fiber_g="0")
    assert post_log(client, **fields).status_code == 303
    assert post_log(client, servings="2", **fields).status_code == 303
    assert db.query(Food).filter_by(owner_id=me, name="Cottage cheese").count() == 1
    assert [l.calories for l in logs(db)] == [90, 180]


def test_create_mode_validates_the_food(client, db, me):
    r = post_log(client, mode="create", name="", serving="1 cup", calories="-5", protein_g="1", carb_g="1", fat_g="1", fiber_g="1")
    assert r.status_code == 422 and db.query(Food).filter_by(owner_id=me).count() == 0 and logs(db) == []


def test_quick_add_logs_a_one_off_without_saving_a_food(client, db, me):
    r = post_log(client, mode="quick", name="Restaurant burger", serving="1 burger", calories="650", protein_g="30", carb_g="45", fat_g="35", fiber_g="2")
    assert r.status_code == 303
    (log,) = logs(db)
    assert (log.name, log.calories, log.food_id) == ("Restaurant burger", 650, None)
    assert db.query(Food).filter_by(owner_id=me).count() == 0


def test_editing_servings_rescales_from_the_entry_not_the_food(client, db, me):
    food = mine(db, me)
    post_log(client, food_id=food.id, servings="1")
    food.calories = 999                                                    # the food changes after it was logged
    db.commit()
    (log,) = logs(db)
    r = client.post(f"/food/log/{log.id}/edit", data={"servings": "3", "date": TODAY}, follow_redirects=False)
    assert r.status_code == 303
    (log,) = logs(db)
    assert (log.servings, log.calories, log.protein_g) == (3, 450, 15)


def test_editing_can_move_an_entry_to_another_meal(client, db, me):
    food = mine(db, me)
    post_log(client, food_id=food.id)
    (log,) = logs(db)
    client.post(f"/food/log/{log.id}/edit", data={"meal": "dinner", "servings": "1", "date": TODAY})
    assert logs(db)[0].meal == "dinner"


def test_deleting_an_entry(client, db, me):
    food = mine(db, me)
    post_log(client, food_id=food.id)
    (log,) = logs(db)
    assert client.post(f"/food/log/{log.id}/delete", data={"date": TODAY}, follow_redirects=False).status_code == 303
    assert logs(db) == []


def test_other_peoples_entries_are_404_for_edit_and_delete(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        db.add(FoodLog(owner_id=other_id, eaten_on=date.today(), meal="lunch", name="x", serving="y", servings=1, calories=1, protein_g=0, carb_g=0, fat_g=0, fiber_g=0))
        db.commit()
        log_id = logs(db)[0].id
        assert client.post(f"/food/log/{log_id}/edit", data={"servings": "2"}).status_code == 404
        assert client.post(f"/food/log/{log_id}/delete").status_code == 404
        assert logs(db)[0].servings == 1


def test_changing_or_deleting_a_food_never_changes_past_logs(client, db, me):
    food = mine(db, me)
    post_log(client, food_id=food.id, servings="2")
    client.post(f"/food/foods/{food.id}/edit", data=dict(name="Renamed", serving="1 cup", calories="1", protein_g="0", carb_g="0", fat_g="0", fiber_g="0"))
    assert (logs(db)[0].name, logs(db)[0].calories) == ("Test oats", 300)
    client.post(f"/food/foods/{food.id}/delete")
    (log,) = logs(db)
    assert (log.name, log.calories, log.food_id) == ("Test oats", 300, None)


def test_search_returns_json_for_own_and_starter_foods_only(client, db, me):
    mine(db, me, name="Egg white wrap")
    starter(db, name="Egg, whole, cooked")
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        mine(db, other_id, name="Egg private to them")
    rows = client.get("/food/search?q=egg").json()
    assert [r["name"] for r in rows] == ["Egg white wrap", "Egg, whole, cooked"]
    assert rows[0]["starter"] is False and rows[1]["starter"] is True and {"id", "serving", "calories", "protein_g", "carb_g", "fat_g", "fiber_g"} <= set(rows[0])


def test_my_foods_can_be_edited_and_deleted_but_starter_foods_cannot(client, db, me):
    food, egg = mine(db, me), starter(db)
    assert client.post(f"/food/foods/{food.id}/edit", data=dict(name="Edited", serving="1 cup", calories="10", protein_g="1", carb_g="1", fat_g="1", fiber_g="1")).status_code == 303
    db.expire_all()
    assert db.get(Food, food.id).name == "Edited"
    assert client.post(f"/food/foods/{egg.id}/edit", data=dict(name="Hacked", serving="1", calories="1", protein_g="0", carb_g="0", fat_g="0", fiber_g="0")).status_code == 404
    assert client.post(f"/food/foods/{egg.id}/delete").status_code == 404
    db.expire_all()
    assert db.get(Food, egg.id).name == "Test starter egg"
    assert client.post(f"/food/foods/{food.id}/delete").status_code == 303 and db.get(Food, food.id) is None


def test_another_persons_food_cannot_be_edited_or_deleted(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        theirs = mine(db, other_id, name="Theirs")
        assert client.post(f"/food/foods/{theirs.id}/delete").status_code == 404
        assert client.post(f"/food/foods/{theirs.id}/edit", data=dict(name="x", serving="y", calories="1", protein_g="0", carb_g="0", fat_g="0", fiber_g="0")).status_code == 404


def test_copying_a_starter_food_to_my_foods(client, db, me):
    egg = starter(db)
    r = client.post(f"/food/foods/{egg.id}/copy")
    assert r.status_code == 200 and r.json()["name"] == "Test starter egg"
    assert db.query(Food).filter_by(owner_id=me, name="Test starter egg").count() == 1
    client.post(f"/food/foods/{egg.id}/copy")
    assert db.query(Food).filter_by(owner_id=me, name="Test starter egg").count() == 1


def test_the_page_shows_edit_delete_add_and_my_foods(client, db, me):
    food = mine(db, me, name="Pantry oats")
    post_log(client, food_id=food.id)
    text = html.unescape(client.get("/measurements?tab=food").text)
    assert "data-food-add" in text and 'action="/food/log/' in text and "Pantry oats" in text and "My foods" in text
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`app/food/logs.py`:
```python
"""Logging food: scaled snapshots, editing servings from the entry's own numbers, and deleting."""

import math
from datetime import date

from sqlalchemy.orm import Session

from app.models import FoodLog

_FIELDS = ("calories", "protein_g", "carb_g", "fat_g", "fiber_g")
MAX_SERVINGS = 50


def parse_servings(raw) -> tuple[float | None, str | None]:
    try:
        value = float(str(raw if raw is not None else "").strip())
    except ValueError:
        return None, "Servings must be a number."
    if not math.isfinite(value) or value <= 0 or value > MAX_SERVINGS:
        return None, f"Servings must be more than 0 and at most {MAX_SERVINGS}."
    return value, None


def snapshot(values: dict, servings: float) -> dict:
    """The entry's stored numbers: per-serving values times servings, rounded to 2 decimals."""
    out = {f: round(float(values[f]) * servings, 2) for f in _FIELDS}
    out["name"], out["serving"] = values["name"], values["serving"]
    return out


def add_entry(session: Session, uid: int, day: date, meal: str, values: dict, servings: float, food_id: int | None) -> FoodLog:
    log = FoodLog(owner_id=uid, eaten_on=day, meal=meal, food_id=food_id, servings=servings, **snapshot(values, servings))
    session.add(log)
    session.commit()
    return log


def _own_entry(session: Session, uid: int, log_id: int) -> FoodLog:
    log = session.get(FoodLog, log_id)
    if log is None or log.owner_id != uid:
        raise LookupError("entry not found")
    return log


def edit_entry(session: Session, uid: int, log_id: int, *, servings: float | None, meal: str | None) -> FoodLog:
    """Change servings (the numbers are rescaled from this entry's own, so a food changed since never leaks in) and/or meal."""
    log = _own_entry(session, uid, log_id)
    if servings is not None and servings != log.servings:
        factor = servings / log.servings
        for field in _FIELDS:
            setattr(log, field, round(getattr(log, field) * factor, 2))
        log.servings = servings
    if meal is not None:
        log.meal = meal
    session.commit()
    return log


def delete_entry(session: Session, uid: int, log_id: int) -> None:
    session.delete(_own_entry(session, uid, log_id))
    session.commit()
```

`app/routers/food.py` — append (imports `HTTPException, Query`, `JSONResponse`, `FOOD_MEALS`, `Food`, `FoodLog`, and `from app.food import foods as foods_mod, logs as logs_mod`):
```python
def _meal(raw: str) -> str | None:
    return raw if raw in FOOD_MEALS else None


def _food_values(form) -> dict:
    return {k: str(form.get(k, "")).strip() for k in ("name", "serving", "serving_g", "calories", "protein_g", "carb_g", "fat_g", "fiber_g")}


@router.post("/food/log")
async def log_food(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    raw_day, raw_meal, mode = str(form.get("date", "")), str(form.get("meal", "")), str(form.get("mode", "existing"))
    day = food_summary.parse_day(raw_day)
    errors: dict[str, str] = {}
    if day is None:
        errors["date"] = "Choose a date between 2000 and a week from today."
    meal = _meal(raw_meal)
    if meal is None:
        errors["meal"] = "Choose Breakfast, Lunch, Dinner or Snacks."
    servings, problem = logs_mod.parse_servings(form.get("servings", "1"))
    if problem:
        errors["servings"] = problem
    values, food_id = None, None
    if mode == "existing":
        try:
            food = session.get(Food, int(form.get("food_id", "")))
        except ValueError:
            food = None
        if food is None or (food.owner_id is not None and food.owner_id != uid):
            raise HTTPException(404, "Food not found")
        values, food_id = {f: getattr(food, f) for f in ("name", "serving", *foods_mod.LIMITS)}, food.id
    elif mode in ("create", "quick"):
        values, food_errors = foods_mod.parse_food(_food_values(form))
        errors.update(food_errors)
    else:
        errors["mode"] = "Choose a food."
    shown_day = day or date.today()
    if errors:
        return _fail(request, session, uid, shown_day, errors, {k: str(v) for k, v in form.items() if not hasattr(v, "filename")})
    if mode == "create":
        mine = foods_mod.own_or_create(session, uid, values)
        food_id = mine.id
    logs_mod.add_entry(session, uid, day, meal, values, servings, food_id)
    return _back(day, meal)


@router.post("/food/log/{log_id}/edit")
async def edit_log(log_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    servings, problem = logs_mod.parse_servings(form.get("servings", "")) if form.get("servings") else (None, None)
    meal = _meal(str(form.get("meal", ""))) if form.get("meal") else None
    if problem or (form.get("meal") and meal is None):
        try:
            logs_mod._own_entry(session, uid, log_id)
        except LookupError:
            raise HTTPException(404, "Entry not found") from None
        return _fail(request, session, uid, day, {"servings": problem or "Choose a meal from the list."})
    try:
        entry = logs_mod.edit_entry(session, uid, log_id, servings=servings, meal=meal)
    except LookupError:
        raise HTTPException(404, "Entry not found") from None
    return _back(entry.eaten_on, entry.meal)


@router.post("/food/log/{log_id}/delete")
async def delete_log(log_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    try:
        logs_mod.delete_entry(session, uid, log_id)
    except LookupError:
        raise HTTPException(404, "Entry not found") from None
    return _back(day)


@router.get("/food/search")
def search_foods(q: str = Query("", max_length=80), session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return JSONResponse([{"id": f.id, "name": f.name, "serving": f.serving, "starter": f.owner_id is None,
                          **{k: getattr(f, k) for k in foods_mod.LIMITS}} for f in foods_mod.search(session, uid, q)],
                        headers={"Cache-Control": "no-store"})


def _own_or_404(session: Session, food_id: int, uid: int) -> Food:
    try:
        return foods_mod.own_food(session, food_id, uid)
    except LookupError:
        raise HTTPException(404, "Food not found") from None


@router.post("/food/foods")
async def create_food(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    values, errors = foods_mod.parse_food(_food_values(form))
    if errors:
        return _fail(request, session, uid, day, errors, _food_values(form))
    foods_mod.own_or_create(session, uid, values)
    return _back(day)


@router.post("/food/foods/{food_id}/edit")
async def edit_food(food_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    food = _own_or_404(session, food_id, uid)
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    values, errors = foods_mod.parse_food(_food_values(form))
    if errors:
        return _fail(request, session, uid, day, errors, _food_values(form))
    clash = foods_mod.find_own(session, uid, values["name"], values["serving"])
    if clash is not None and clash.id != food.id:
        return _fail(request, session, uid, day, {"name": "You already have a food with that name and serving."}, _food_values(form))
    for key, value in values.items():
        setattr(food, key, value)
    session.commit()
    return _back(day)


@router.post("/food/foods/{food_id}/delete")
async def delete_food(food_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    food = _own_or_404(session, food_id, uid)
    form = await request.form()
    day = food_summary.parse_day(str(form.get("date", ""))) or date.today()
    session.delete(food)
    session.commit()
    return _back(day)


@router.post("/food/foods/{food_id}/copy")
def copy_food(food_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    food = session.get(Food, food_id)
    if food is None or food.owner_id is not None:
        raise HTTPException(404, "Food not found")
    mine = foods_mod.copy_to_mine(session, uid, food)
    return JSONResponse({"id": mine.id, "name": mine.name}, headers={"Cache-Control": "no-store"})
```
In `app/food/foods.py` add the two helpers the routes use:
```python
def find_own(session: Session, uid: int, name: str, serving: str) -> Food | None:
    return session.scalar(select(Food).where(Food.owner_id == uid, Food.name == name, Food.serving == serving))


def own_or_create(session: Session, uid: int, values: dict) -> Food:
    """The person's food with this name and serving, creating it (or updating its numbers) from `values`."""
    food = find_own(session, uid, values["name"], values["serving"])
    if food is None:
        food = Food(owner_id=uid, source="mine", **values)
        session.add(food)
    else:
        for key, value in values.items():
            setattr(food, key, value)
    session.commit()
    return food
```

`app/templates/measurements/_food.html` — inside each meal's `.food-meal` add, after the entries list, the Add button, and extend each entry `<li>` with its edit and delete forms; after the meal loop add the My foods section and the dialog:
```html
      <!-- inside each entry <li>, after the numbers span -->
        <form method="post" action="/food/log/{{ en.id }}/edit" class="food-inline">
          <input type="hidden" name="date" value="{{ food_day.isoformat() }}">
          <input type="number" name="servings" value="{{ en.servings | round(2) }}" min="0.1" max="50" step="0.1" aria-label="Servings">
          <button type="submit" class="btn btn-ghost small">Update</button></form>
        <form method="post" action="/food/log/{{ en.id }}/delete" class="food-inline" data-food-delete>
          <input type="hidden" name="date" value="{{ food_day.isoformat() }}">
          <button type="submit" class="btn btn-ghost small">Delete</button></form>

    <!-- after the entries list or the "Nothing logged." line, inside .food-meal -->
    <button type="button" class="btn small" data-food-add data-meal="{{ meal.key }}">Add food</button>
```
and after the meal loop (still inside the `<section>`):
```html
  <details class="food-myfoods">
    <summary>My foods</summary>
    {% if my_foods %}
    <ul class="food-entries">
      {% for f in my_foods %}
      <li>
        <form method="post" action="/food/foods/{{ f.id }}/edit" class="food-foodform">
          <input type="hidden" name="date" value="{{ food_day.isoformat() }}">
          <input name="name" value="{{ f.name }}" maxlength="120" aria-label="Name">
          <input name="serving" value="{{ f.serving }}" maxlength="60" aria-label="Serving">
          <input name="calories" type="number" step="0.1" min="0" value="{{ f.calories }}" aria-label="Calories">
          <input name="protein_g" type="number" step="0.1" min="0" value="{{ f.protein_g }}" aria-label="Protein g">
          <input name="carb_g" type="number" step="0.1" min="0" value="{{ f.carb_g }}" aria-label="Carbs g">
          <input name="fat_g" type="number" step="0.1" min="0" value="{{ f.fat_g }}" aria-label="Fat g">
          <input name="fiber_g" type="number" step="0.1" min="0" value="{{ f.fiber_g }}" aria-label="Fiber g">
          <button type="submit" class="btn btn-ghost small">Save</button></form>
        <form method="post" action="/food/foods/{{ f.id }}/delete" class="food-inline" data-food-delete>
          <input type="hidden" name="date" value="{{ food_day.isoformat() }}">
          <button type="submit" class="btn btn-ghost small">Delete</button></form>
      </li>
      {% endfor %}
    </ul>
    {% else %}<p class="muted small">Foods you create appear here.</p>{% endif %}
  </details>
  {% for key, message in (food_error or {}).items() %}{% if key not in ('diet_preset', 'macro_goal', 'custom') %}<p class="error small" role="alert">{{ message }}</p>{% endif %}{% endfor %}
</section>

<dialog id="food-dialog" class="dialog"{{ ' data-open-on-load' if food_error and not (food_error.keys() | select('in', ('diet_preset', 'macro_goal', 'custom')) | list) }}>
  <div class="dialog-head"><h3>Add food</h3><button type="button" class="btn btn-ghost" data-food-close aria-label="Close">&times;</button></div>
  <div class="form-section">
    <div class="food-modes" role="tablist">
      <label><input type="radio" name="food-mode" value="existing" checked> Find a food</label>
      <label><input type="radio" name="food-mode" value="create"> Create a food</label>
      <label><input type="radio" name="food-mode" value="quick"> Quick add</label>
    </div>
    <form method="post" action="/food/log" data-food-form>
      <input type="hidden" name="date" value="{{ food_day.isoformat() }}">
      <input type="hidden" name="meal" value="breakfast" data-food-meal>
      <input type="hidden" name="mode" value="existing" data-food-mode>
      <input type="hidden" name="food_id" value="" data-food-id>
      <div data-food-pane="existing">
        <label class="field"><span>Search foods</span><input type="search" data-food-search placeholder="egg, rice, chicken..." autocomplete="off"></label>
        <ul class="food-results" data-food-results></ul>
        <p class="muted small" data-food-picked></p>
      </div>
      <div data-food-pane="create" hidden>
        <label class="field"><span>Name</span><input name="name" maxlength="120" disabled></label>
        <label class="field"><span>Serving (for example 1 cup cooked)</span><input name="serving" maxlength="60" disabled></label>
        <div class="grid">
          <label class="field"><span>Calories</span><input name="calories" type="number" min="0" max="5000" step="0.1" disabled></label>
          <label class="field"><span>Protein g</span><input name="protein_g" type="number" min="0" max="500" step="0.1" disabled></label>
          <label class="field"><span>Carbs g</span><input name="carb_g" type="number" min="0" max="500" step="0.1" disabled></label>
          <label class="field"><span>Fat g</span><input name="fat_g" type="number" min="0" max="500" step="0.1" disabled></label>
          <label class="field"><span>Fiber g</span><input name="fiber_g" type="number" min="0" max="500" step="0.1" disabled></label>
        </div>
        <p class="muted small"><span data-food-quick-note hidden>A quick add logs these numbers once and is not saved as a food.</span><span data-food-create-note>Create saves this food to My foods and logs it.</span></p>
      </div>
      <label class="field"><span>Servings</span><input name="servings" type="number" min="0.1" max="50" step="0.1" value="1" required></label>
      <div class="dialog-foot"><button type="submit" class="btn btn-primary">Add to log</button></div>
    </form>
  </div>
</dialog>
<script src="{{ static_url('js/food.js') }}" defer></script>
```
In `app/routers/measurements.py` (the Task 4 `food` block) add `my_foods=session.scalars(select(Food).where(Food.owner_id == uid).order_by(Food.name)).all()` to the context update, importing `Food`.

`app/static/js/food.js`:
```js
// Food tab: the Add food dialog (search, create, quick add) and delete confirmations. The server validates everything.
(() => {
  const dialog = document.getElementById("food-dialog");
  if (!dialog) return;
  const form = dialog.querySelector("[data-food-form]");
  const mealInput = form.querySelector("[data-food-meal]");
  const modeInput = form.querySelector("[data-food-mode]");
  const idInput = form.querySelector("[data-food-id]");
  const search = form.querySelector("[data-food-search]");
  const results = form.querySelector("[data-food-results]");
  const picked = form.querySelector("[data-food-picked]");
  const panes = form.querySelectorAll("[data-food-pane]");
  let timer = null;

  document.querySelectorAll("[data-food-delete]").forEach((f) => f.addEventListener("submit", (event) => {
    if (!window.confirm("Delete this? This cannot be undone.")) event.preventDefault();
  }));

  function setMode(mode) {
    modeInput.value = mode;
    panes.forEach((pane) => {
      const on = pane.dataset.foodPane === mode || (mode === "quick" && pane.dataset.foodPane === "create");
      pane.hidden = !on;
      pane.querySelectorAll("input").forEach((input) => { input.disabled = !on; });
    });
    form.querySelector("[data-food-quick-note]").hidden = mode !== "quick";
    form.querySelector("[data-food-create-note]").hidden = mode === "quick";
  }
  dialog.querySelectorAll("input[name=food-mode]").forEach((radio) => radio.addEventListener("change", () => setMode(radio.value)));

  document.querySelectorAll("[data-food-add]").forEach((button) => button.addEventListener("click", () => {
    mealInput.value = button.dataset.meal;
    dialog.showModal();
    search.focus();
    runSearch();
  }));
  if (dialog.hasAttribute("data-open-on-load")) dialog.showModal();
  dialog.querySelector("[data-food-close]").addEventListener("click", () => dialog.close());

  function row(food) {
    const li = document.createElement("li");
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "food-result";
    pick.textContent = food.name + " (" + food.serving + ") " + Math.round(food.calories) + " kcal, P " + food.protein_g + " C " + food.carb_g + " F " + food.fat_g;
    pick.addEventListener("click", () => {
      idInput.value = food.id;
      picked.textContent = "Selected: " + food.name + " (" + food.serving + ")";
    });
    li.appendChild(pick);
    if (food.starter) {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "btn btn-ghost small";
      copy.textContent = "Copy to My foods";
      copy.addEventListener("click", async () => {
        const r = await fetch("/food/foods/" + food.id + "/copy", { method: "POST", credentials: "same-origin" });
        copy.textContent = r.ok ? "Copied" : "Could not copy";
        copy.disabled = true;
      });
      li.appendChild(copy);
    }
    return li;
  }

  async function runSearch() {
    const response = await fetch("/food/search?q=" + encodeURIComponent(search.value), { credentials: "same-origin" });
    if (!response.ok) return;
    results.replaceChildren(...(await response.json()).map(row));
  }
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(runSearch, 200); });

  form.addEventListener("submit", (event) => {
    if (modeInput.value === "existing" && !idInput.value) {
      event.preventDefault();
      picked.textContent = "Pick a food from the list first.";
    }
  });
  setMode("existing");
})();
```

CSS: append `.food-inline { display: inline-flex; gap: 4px; align-items: center; } .food-inline input[type=number] { width: 70px; } .food-results { list-style: none; padding: 0; margin: 8px 0; max-height: 220px; overflow: auto; display: grid; gap: 4px; } .food-result { text-align: left; width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); cursor: pointer; } .food-foodform { display: flex; gap: 6px; flex-wrap: wrap; } .food-modes { display: flex; gap: 16px; margin-bottom: 12px; }`.

- [ ] **Step 4: Run to verify it passes**, then the full suite. If a test calls `_fail` without the server-side page context for the Add dialog, fix the context, not the test.

- [ ] **Step 5: Browser check, then commit.** In the in-app browser, load the tab against a throwaway database: Add food opens, searching shows results, Create and Quick add panes switch, Update and Delete forms are present. Then:
```bash
git add app/food/logs.py app/food/foods.py app/routers/food.py app/routers/measurements.py app/templates/measurements/_food.html app/static/js/food.js app/static/css/app.css tests/test_food_logs.py
git commit -m "feat: log food (find, create, quick add), edit servings, delete, and manage My foods"
```

---

### Task 6: Backup, export and account deletion

**Files:**
- Modify: `app/backup/sections.py`, `app/backup/load.py`, `app/routers/settings.py`
- Create: `tests/test_food_backup.py`

**Interfaces:**
- Consumes: Tasks 1-5; `build_archive`, `read_archive`, `loader.load` (`ADD`, `REPLACE`).
- Produces: backup section key `food` (person level, not shareable) with tables `foods` (own rows) and `food_logs`; `REFS[("food_logs", "food_id")] = ("foods", ("name", "serving"))`; `MERGE_KEYS["foods"] = ("name", "serving")`.

- [ ] **Step 1: Write the failing tests** (`tests/test_food_backup.py`)

```python
from datetime import date

import pytest

from app.backup import load as loader
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from app.models import Food, FoodLog, User
from photo_helpers import other_client


def export(db, me, kind="backup"):
    return read_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=["food"]), max_bytes=50_000_000)


def run(db, me, archive, plan):
    return loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan=plan)


def seed(db, me):
    mine = Food(owner_id=me, source="mine", name="Test oats", serving="1 cup", calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4)
    egg = Food(owner_id=None, source="starter", name="Test starter egg", serving="1 large", calories=72, protein_g=6.3, carb_g=0.4, fat_g=4.8, fiber_g=0)
    db.add_all([mine, egg])
    db.commit()
    for food, meal in ((mine, "breakfast"), (egg, "lunch")):
        db.add(FoodLog(owner_id=me, eaten_on=date(2026, 10, 6), meal=meal, food_id=food.id, name=food.name, serving=food.serving, servings=2,
                       calories=food.calories * 2, protein_g=food.protein_g * 2, carb_g=food.carb_g * 2, fat_g=food.fat_g * 2, fiber_g=food.fiber_g * 2))
    db.commit()
    return mine, egg


def wipe(db, me):
    db.query(FoodLog).filter_by(owner_id=me).delete()
    db.query(Food).filter_by(owner_id=me).delete()
    db.commit()


def test_a_backup_carries_own_foods_and_logs_but_not_starter_foods(client, db, me):
    seed(db, me)
    archive = export(db, me)
    name = next(n for n in archive.names() if n.startswith(("sections/", "persons/")) and n.endswith("food.json"))
    tables = json.loads(archive.read(name))["tables"]
    assert [row["name"] for row in tables["foods"]] == ["Test oats"]                    # the starter food is not in the file
    assert sorted(row["name"] for row in tables["food_logs"]) == ["Test oats", "Test starter egg"]   # but its log (a snapshot) is


def test_food_can_never_be_in_a_share_file(client, db, me):
    seed(db, me)
    with pytest.raises(BackupError, match="cannot be put in a share file"):
        export(db, me, kind="share")


def test_loading_food_into_an_empty_account_restores_logs_and_relinks_starter_foods(client, db, me):
    mine, egg = seed(db, me)
    archive = export(db, me)
    wipe(db, me)
    run(db, me, archive, {"food": loader.ADD})
    db.expire_all()
    logs = {l.name: l for l in db.query(FoodLog).filter_by(owner_id=me)}
    assert set(logs) == {"Test oats", "Test starter egg"}
    assert logs["Test oats"].food_id is not None and logs["Test oats"].food_id == db.query(Food).filter_by(owner_id=me).one().id
    assert logs["Test starter egg"].food_id == egg.id            # found again by name and serving
    assert logs["Test oats"].calories == 300


def test_a_log_whose_starter_food_is_gone_keeps_its_snapshot(client, db, me):
    mine, egg = seed(db, me)
    archive = export(db, me)
    wipe(db, me)
    db.delete(db.get(Food, egg.id))
    db.commit()
    run(db, me, archive, {"food": loader.ADD})
    db.expire_all()
    log = db.query(FoodLog).filter_by(owner_id=me, name="Test starter egg").one()
    assert log.food_id is None and log.calories == 144


def test_replace_swaps_the_persons_food_data_and_leaves_starter_foods(client, db, me):
    seed(db, me)
    archive = export(db, me)
    db.add(FoodLog(owner_id=me, eaten_on=date(2026, 10, 7), meal="dinner", name="Extra", serving="x", servings=1, calories=1, protein_g=0, carb_g=0, fat_g=0, fiber_g=0))
    db.commit()
    run(db, me, archive, {"food": loader.REPLACE})
    db.expire_all()
    assert db.query(FoodLog).filter_by(owner_id=me).count() == 2 and db.query(Food).filter_by(source="starter").count() == 1


def test_food_loads_for_the_loader_never_for_the_name_in_the_file(client, db, me):
    seed(db, me)
    archive = export(db, me)
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        loader.load(db, archive, uid=other_id, username_key="photoother", is_admin=False, plan={"food": loader.ADD})
        db.expire_all()
        assert db.query(FoodLog).filter_by(owner_id=other_id).count() == 2 and db.query(Food).filter_by(owner_id=other_id).count() == 1


def test_deleting_an_account_removes_its_food_but_never_starter_foods(client, db, me):
    with other_client("foodgone"):
        gone = db.query(User).filter_by(username_key="foodgone").one()
        gone_id, gone_name = gone.id, gone.username
        seed(db, gone_id)
        r = client.post(f"/settings/admin/users/{gone_id}/delete", data={"username": gone_name}, follow_redirects=False)
        assert r.status_code == 303
    db.expire_all()
    assert db.query(Food).filter_by(owner_id=gone_id).count() == 0 and db.query(FoodLog).filter_by(owner_id=gone_id).count() == 0
    assert db.query(Food).filter_by(source="starter", name="Test starter egg").count() == 1


def test_the_backup_page_offers_food(client, db, me):
    assert "Food" in client.get("/backup").text
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`app/backup/sections.py`: add after the `body_photos` section
```python
    Section("food", "Food", PERSON, (
        Tbl("foods", "owner_id = :uid", share_drop=True),
        Tbl("food_logs", "owner_id = :uid", share_drop=True)),
        help="Your own foods and your food log. The built-in starter foods are not backed up. Never offered in a Share file."),
```
add `"food"` to `LOAD_ORDER` after `"body_photos"`; add to `REFS`: `("food_logs", "food_id"): ("foods", ("name", "serving")),`; add to `MERGE_KEYS`: `"foods": ("name", "serving"),` (check how `MERGE_KEYS` is declared and keep its shape).

`app/backup/load.py` in `_find`: next to the `inventory_items` special case add
```python
    if target == "foods":                      # a person's own food, or a built-in starter food: never someone else's
        clause = f"{clause} AND (owner_id IS NULL OR owner_id = :uid)"
        params["uid"] = ctx.uid
```
(keep the surrounding variable names exactly as they are in that function).

`app/routers/settings.py` `admin_delete_user`: next to the photo block (before `session.flush()`) add
```python
    for log in session.scalars(select(FoodLog).where(FoodLog.owner_id == target.id)):
        session.delete(log)
    for food in session.scalars(select(Food).where(Food.owner_id == target.id)):
        session.delete(food)
```
and import `Food, FoodLog` in the models import. (Logs are deleted first because they point at foods; a starter food is never touched because `owner_id` is NULL.)

- [ ] **Step 4: Run to verify it passes**, then the full suite (the registry guard test turns green now).

- [ ] **Step 5: Commit**
```bash
git add app/backup/sections.py app/backup/load.py app/routers/settings.py tests/test_food_backup.py
git commit -m "feat: food in personal backups and exports (never shared); removed with the account"
```

---

### Task 7: The starter foods (data and the tool that builds it)

**Files:**
- Create: `tools/build_starter_foods.py`, `tools/starter_food_list.txt`, `tests/test_food_starter_data.py`
- Modify: `app/food/starter_foods.json` (replace the seed)

**Interfaces:**
- Consumes: Task 3's `load_starter` row shape (`name, serving, serving_g, calories, protein_g, carb_g, fat_g, fiber_g`).
- Produces: `app/food/starter_foods.json` with about 300 foods; `tools/build_starter_foods.py` with `python tools/build_starter_foods.py --zip <FoodData_Central_sr_legacy_food_csv.zip> [--review]`.

**Source:** USDA FoodData Central, SR Legacy (public domain). Download the CSV bundle from the FoodData Central downloads page into the scratchpad (not the repo): `https://fdc.nal.usda.gov/download-datasets` ("SR Legacy", CSV). Tell the owner the filename and size before downloading.

- [ ] **Step 1: Write the failing data tests** (`tests/test_food_starter_data.py`)

```python
import json
import re
from pathlib import Path

import pytest

from app.food.foods import STARTER_PATH, load_starter, parse_food

ROWS = json.loads(Path(STARTER_PATH).read_text(encoding="utf-8"))


def test_the_starter_list_is_a_few_hundred_foods():
    assert 250 <= len(ROWS) <= 400


def test_every_row_is_valid_and_names_are_unique_per_serving():
    seen = set()
    for row in ROWS:
        values, errors = parse_food(row)
        assert errors == {}, (row["name"], errors)
        key = (values["name"].casefold(), values["serving"].casefold())
        assert key not in seen, key
        seen.add(key)


def test_numbers_are_plausible_by_the_atwater_rule():
    for row in ROWS:
        energy = 4 * row["protein_g"] + 4 * row["carb_g"] + 9 * row["fat_g"]
        if row["calories"] >= 20:                      # tiny values drift with rounding and alcohol/fiber handling
            assert 0.75 * row["calories"] <= energy <= 1.3 * row["calories"] + 15, (row["name"], row["calories"], energy)
        assert row["fiber_g"] <= row["carb_g"] + 0.5, row["name"]


def test_the_list_is_simple_everyday_food_with_sensible_servings():
    names = " ".join(r["name"].lower() for r in ROWS)
    for staple in ("egg", "chicken", "rice", "oat", "banana", "peanut butter", "tuna", "yogurt", "potato", "bread", "milk"):
        assert staple in names, staple
    assert all(re.search(r"\d", r["serving"]) or r["serving"].lower().startswith(("a ", "one ")) for r in ROWS)


def test_the_loader_accepts_the_whole_shipped_file_idempotently(db):
    first = load_starter(db)
    assert first["added"] + first["updated"] >= 0
    assert load_starter(db) == {"added": 0, "updated": 0}


def test_no_brand_names_ship_in_the_data():
    text = json.dumps(ROWS).lower()
    for word in ("mcdonald", "chobani", "kellogg", "ralston"):
        assert word not in text
```

- [ ] **Step 2: Run to verify it fails** (the 6-row seed is too small).

- [ ] **Step 3: Build the data**

`tools/build_starter_foods.py` reads the SR Legacy CSVs from the zip (`food.csv`: `fdc_id, description`; `food_nutrient.csv`: `fdc_id, nutrient_id, amount`; per 100 g; nutrient ids: Energy kcal 1008, Protein 1003, Total fat 1004, Carbohydrate 1005, Fiber 1079), then for each line of `tools/starter_food_list.txt` (`Display name | serving text | serving grams | match words`) picks the SR Legacy description that contains every match word, preferring the shortest, scales per-100 g values by `grams / 100`, rounds to one decimal and writes the JSON sorted by name. With `--review` it prints each line's matched USDA description and numbers so the choice can be checked by eye; a line with no match or an implausible Atwater total is reported and skipped (the run exits non-zero). The match words must be unambiguous, for example `egg | whole cooked hard-boiled`.

Author `tools/starter_food_list.txt` with 250-400 lines of simple, everyday foods that need no cooking skill, covering at least: eggs and egg whites; chicken (breast, thigh, rotisserie, canned); turkey and deli turkey; lean beef (ground 90/10, steak); pork (loin, bacon, ham); fish (tuna canned, salmon, tilapia, shrimp); dairy (milk, Greek yogurt, cottage cheese, cheese varieties, string cheese); breakfast (oatmeal, cereal basics, toast, bagel, English muffin); grains (white and brown rice, pasta, quinoa, tortillas, bread); potatoes and sweet potatoes; beans and lentils; nuts and nut butters; common fruit (banana, apple, orange, berries, grapes, melon); common vegetables (broccoli, carrots, spinach, green beans, corn, peppers, salad greens, tomatoes); frozen and canned basics; oils and spreads; simple snacks (popcorn, rice cakes, jerky, protein bar basics); drinks (milk, juice, protein shake basics). Each line has one household serving (for example `1 large`, `1 cup cooked`, `3 oz`) and its gram weight from standard portion tables.

- [ ] **Step 4: Run to verify it passes**, review the printed matches for any that look wrong (re-word the match words and rerun), then the full suite.

- [ ] **Step 5: Commit**
```bash
git add tools/build_starter_foods.py tools/starter_food_list.txt app/food/starter_foods.json tests/test_food_starter_data.py
git commit -m "feat: starter list of common foods (USDA SR Legacy, public domain) and the tool that builds it"
```

---

### Task 8: Roadmap note and final verification

**Files:** Modify `docs/ROADMAP.md`.

- [ ] **Step 1: Add a roadmap entry** before the `## Phase 8` marker (same style as the body-photos entry): "Food tracking, part A (built 2026-10-06)" with the spec and plan paths, what the Food tab does (replaces Macros; diet type and goal pickers; calorie limit; potential deficit = TDEE + logged workout burn - eaten with a pounds-per-week estimate; macro pie chart; fulfilment bars; four meals; My foods and the starter list; snapshots so history never moves; one calorie number shared with the Energy tab), what changed (the old Macros calculator is gone and its number now equals the Energy tab's TDEE plus the goal offset), and what is **not** built yet: part B (live USDA search) and part C (meal plans from simple foods, a library of ready-made diet plans, GLP-1 / Retatrutide shot-day guidance written in original words with the owner's reference pages linked).
- [ ] **Step 2: Full suite and scans**: the full suite green; `.venv/Scripts/python.exe /c/tmp/amide-scrub/denylist_scan.py` clean.
- [ ] **Step 3: Whole-branch review** (the executing-plans skill's final review) and the one fix pass; then commit and push:
```bash
git add docs/ROADMAP.md
git commit -m "docs: roadmap note for food tracking (part A)"
git push origin main
```


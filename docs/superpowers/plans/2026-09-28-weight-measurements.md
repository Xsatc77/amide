# Weight & Measurements (Phase 6, part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** build the Weight & Measurements page described in the spec — profile fields for the
macro calculator, a measurement/weigh-in entry form, a body silhouette, adjustable-range charts,
and reserved Journal/Labs placeholder tabs.

**Architecture:** new enums + `User` columns + a `BodyMeasurement` table (Task 1); two small
pure-function calculation modules with no database access (Task 2); Settings additions for the new
profile fields (Task 3); a new `app/routers/measurements.py` page with the entry form and tabbed
layout (Task 4); the silhouette and Macros tab wired into that page (Task 5); SVG charts with a
range selector (Task 6).

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic (SQLite) + Jinja2 + vanilla JS/hand-drawn SVG
(no external charting library — continuing this app's existing convention).

**Spec:** `docs/superpowers/specs/2026-09-28-weight-measurements-design.md`

## Global Constraints

- Imperial units only (lb/ft/in) — no metric support in this build.
- No water-intake logging/tracking UI — the water section only computes and displays the goal and
  its hourly cups/bottles pace.
- BMI and BF% are computed at read time, never stored as their own columns.
- `BodyMeasurement` visibility (and the new `User` profile fields, where relevant to a shared view)
  follows the exact `_protocol_query`/`_shared_protocol_query` pattern already established in
  `app/routers/protocols.py` (`ShareCategory.PERSONAL_DATA`) — own rows plus anyone who granted
  Personal Data sharing, never a global view.
- Every new enum follows the existing `LabeledEnum` tuple-value pattern (see `DispensingMethod`,
  `ActivityLevel`'s own definition in this plan) so its stored value can double as data (the
  activity multiplier, the goal's calorie offset) rather than needing a separate lookup table.
- This plan's prior features have repeatedly found the same three recurring test-fixture bugs:
  (1) inserting a seeded-name row unconditionally collides with a real seeded row under a unique
  constraint; (2) an unfiltered `select(User.id)` picks the wrong user under full-suite test-order
  pollution — pin via `User.username_key == "tester"`; (3) a POSIX-only `%-d` strftime flag crashes
  on Windows — use `{{ x.strftime('%b') }} {{ x.day }}` instead. Watch for all three.

## Review Focus

1. A bilateral measurement missing one side must not silently average with a stale/zero value.
2. The macro calculator's safe-floor clamp must actually floor at 1500/1200 kcal with a visible
   adjusted-notice, never silently show a below-floor target as unadjusted.
3. `DietPreset.CUSTOM` percentages that don't sum to 100 must be rejected with a clear error.
4. The Navy BF% formula's female branch requires `hips_in` — missing it must show "not enough
   data," never a wrong number from treating a missing hip value as zero.
5. Every profile field the calculator needs must be genuinely optional — a new user with none of
   them filled in must see a clear prompt on the Macros tab, never a crash, and every other tab
   must work fine regardless.

---

### Task 1: Migration + models

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0017_weight_measurements.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `BiologicalSex`, `ActivityLevel`, `MacroGoal`, `DietPreset` enums; `User.sex`/
  `.birth_date`/`.height_in`/`.activity_level`/`.macro_goal`/`.diet_preset`/`.custom_protein_pct`/
  `.custom_carb_pct`/`.custom_fat_pct`/`.water_goal_oz`; `BodyMeasurement` model. Every later task
  consumes these exact names.

- [ ] **Step 1: Write the failing test**

Read `tests/test_migrations.py`'s existing `test_0016_adds_vendor_management` for the exact
`_cfg`/`command`/`sqlite3` helper shape, and write a new test in the same style:

```python
def test_0017_adds_weight_measurements(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0016")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-28')")
        uid = c.execute("select id from users where username='A'").fetchone()[0]
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        user_cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert {"sex", "birth_date", "height_in", "activity_level", "macro_goal", "diet_preset",
               "custom_protein_pct", "custom_carb_pct", "custom_fat_pct", "water_goal_oz"} <= user_cols
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "body_measurements" in tables
        body_cols = {r[1] for r in c.execute("pragma table_info(body_measurements)")}
        assert {"owner_id", "measured_at", "weight_lbs", "systolic", "diastolic", "neck_in",
               "waist_in", "hips_in", "biceps_l_in", "biceps_r_in", "forearm_l_in", "forearm_r_in",
               "quad_l_in", "quad_r_in", "calf_l_in", "calf_r_in"} <= body_cols
        c.execute("insert into body_measurements(owner_id, measured_at, weight_lbs, created_at) "
                  "values (?, '2026-09-28', 180.5, '2026-09-28')", (uid,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into body_measurements(owner_id, measured_at, weight_lbs, created_at) "
                      "values (?, '2026-09-28', -5, '2026-09-28')", (uid,))
    command.downgrade(cfg, "0016")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "body_measurements" not in tables
        user_cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert "sex" not in user_cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py -k test_0017 -v`
Expected: FAIL — migration `0017` doesn't exist yet.

- [ ] **Step 3: Add the enums**

In `app/models.py`, add near the other `LabeledEnum` definitions (e.g. near `DispensingMethod`):

```python
class BiologicalSex(str, enum.Enum):
    MALE = "Male"
    FEMALE = "Female"


class ActivityLevel(LabeledEnum):
    SEDENTARY = ("1.2", "Sedentary — little or no exercise")
    LIGHTLY_ACTIVE = ("1.375", "Lightly active — 1-3 days/week")
    MODERATELY_ACTIVE = ("1.55", "Moderately active — 3-5 days/week")
    VERY_ACTIVE = ("1.725", "Very active — 6-7 days/week")
    EXTRA_ACTIVE = ("1.9", "Extra active — physical job or 2x/day training")


class MacroGoal(LabeledEnum):
    AGGRESSIVE_LOSS = ("-1000", "Aggressive fat loss")
    MODERATE_LOSS = ("-500", "Moderate fat loss")
    SLOW_LOSS = ("-250", "Slow fat loss")
    MAINTAIN = ("0", "Maintain current weight")
    SLOW_GAIN = ("250", "Slow muscle gain")
    MODERATE_GAIN = ("500", "Moderate muscle gain")
    AGGRESSIVE_GAIN = ("1000", "Aggressive muscle gain")


class DietPreset(LabeledEnum):
    BALANCED = ("balanced", "Balanced (30/40/30)")
    HIGH_PROTEIN = ("high_protein", "High protein (40/30/30)")
    LOW_CARB = ("low_carb", "Low carb (40/15/45)")
    KETO = ("keto", "Ketogenic (20/5/75)")
    CUSTOM = ("custom", "Custom ratio")
```

Check how `BiologicalSex`-like plain string enums (e.g. `Category`) are defined at the top of
`app/models.py` for the exact `class X(str, enum.Enum):` import/base-class shape, and how
`LabeledEnum`-based ones (e.g. `DispensingMethod`) are defined, before committing to the above —
match the file's real conventions exactly, not this sketch verbatim if it differs in a detail.

- [ ] **Step 4: Add the `User` columns**

In the existing `User` class, add alongside `default_discard_days`:

```python
    sex: Mapped[BiologicalSex | None] = mapped_column(_enum_column(BiologicalSex))
    birth_date: Mapped[date | None] = mapped_column(Date)
    height_in: Mapped[float | None] = mapped_column(Float)
    activity_level: Mapped[ActivityLevel | None] = mapped_column(_enum_column(ActivityLevel))
    macro_goal: Mapped[MacroGoal | None] = mapped_column(_enum_column(MacroGoal))
    diet_preset: Mapped[DietPreset | None] = mapped_column(_enum_column(DietPreset))
    custom_protein_pct: Mapped[int | None] = mapped_column(Integer)
    custom_carb_pct: Mapped[int | None] = mapped_column(Integer)
    custom_fat_pct: Mapped[int | None] = mapped_column(Integer)
    water_goal_oz: Mapped[int | None] = mapped_column(Integer)
```

- [ ] **Step 5: Add the `BodyMeasurement` model**

```python
class BodyMeasurement(Base):
    """One weigh-in/measurement session. Every field nullable -- log just weight some days, a full
    tape-measure session on others. Bilateral parts store both sides; the silhouette/charts show
    their average (Task 5), the entry's own detail view shows both raw numbers."""
    __tablename__ = "body_measurements"
    __table_args__ = (
        CheckConstraint("weight_lbs IS NULL OR weight_lbs > 0", name="ck_body_measurement_weight_pos"),
        CheckConstraint("systolic IS NULL OR systolic > 0", name="ck_body_measurement_systolic_pos"),
        CheckConstraint("diastolic IS NULL OR diastolic > 0", name="ck_body_measurement_diastolic_pos"),
        CheckConstraint("neck_in IS NULL OR neck_in > 0", name="ck_body_measurement_neck_pos"),
        CheckConstraint("waist_in IS NULL OR waist_in > 0", name="ck_body_measurement_waist_pos"),
        CheckConstraint("hips_in IS NULL OR hips_in > 0", name="ck_body_measurement_hips_pos"),
        CheckConstraint("biceps_l_in IS NULL OR biceps_l_in > 0", name="ck_body_measurement_biceps_l_pos"),
        CheckConstraint("biceps_r_in IS NULL OR biceps_r_in > 0", name="ck_body_measurement_biceps_r_pos"),
        CheckConstraint("forearm_l_in IS NULL OR forearm_l_in > 0", name="ck_body_measurement_forearm_l_pos"),
        CheckConstraint("forearm_r_in IS NULL OR forearm_r_in > 0", name="ck_body_measurement_forearm_r_pos"),
        CheckConstraint("quad_l_in IS NULL OR quad_l_in > 0", name="ck_body_measurement_quad_l_pos"),
        CheckConstraint("quad_r_in IS NULL OR quad_r_in > 0", name="ck_body_measurement_quad_r_pos"),
        CheckConstraint("calf_l_in IS NULL OR calf_l_in > 0", name="ck_body_measurement_calf_l_pos"),
        CheckConstraint("calf_r_in IS NULL OR calf_r_in > 0", name="ck_body_measurement_calf_r_pos"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    measured_at: Mapped[date] = mapped_column(Date, index=True)
    weight_lbs: Mapped[float | None] = mapped_column(Float)
    systolic: Mapped[int | None] = mapped_column(Integer)
    diastolic: Mapped[int | None] = mapped_column(Integer)
    neck_in: Mapped[float | None] = mapped_column(Float)
    waist_in: Mapped[float | None] = mapped_column(Float)
    hips_in: Mapped[float | None] = mapped_column(Float)
    biceps_l_in: Mapped[float | None] = mapped_column(Float)
    biceps_r_in: Mapped[float | None] = mapped_column(Float)
    forearm_l_in: Mapped[float | None] = mapped_column(Float)
    forearm_r_in: Mapped[float | None] = mapped_column(Float)
    quad_l_in: Mapped[float | None] = mapped_column(Float)
    quad_r_in: Mapped[float | None] = mapped_column(Float)
    calf_l_in: Mapped[float | None] = mapped_column(Float)
    calf_r_in: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

- [ ] **Step 6: Write the migration**

Create `migrations/versions/0017_weight_measurements.py`, following `0016_vendor_management.py`'s
exact style (`op.batch_alter_table` for the `users` columns is fine here — this table doesn't have
`vendors`' `COLLATE NOCASE` complication, so no raw-SQL rebuild is needed; verify this assumption
by checking whether `users.username`/`username_key` use a custom collation before assuming
`batch_alter_table` is safe — if they do, mirror `0016`'s raw-SQL rebuild approach instead):

```python
"""weight & measurements: User profile fields for the macro calculator, BodyMeasurement table

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sex', sa.Enum('Male', 'Female', name='biological_sex', native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('birth_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('height_in', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('activity_level', sa.Enum('1.2', '1.375', '1.55', '1.725', '1.9', name='activity_level', native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('macro_goal', sa.Enum('-1000', '-500', '-250', '0', '250', '500', '1000', name='macro_goal', native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('diet_preset', sa.Enum('balanced', 'high_protein', 'low_carb', 'keto', 'custom', name='diet_preset', native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('custom_protein_pct', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('custom_carb_pct', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('custom_fat_pct', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('water_goal_oz', sa.Integer(), nullable=True))

    op.create_table(
        'body_measurements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('measured_at', sa.Date(), nullable=False),
        sa.Column('weight_lbs', sa.Float(), nullable=True),
        sa.Column('systolic', sa.Integer(), nullable=True),
        sa.Column('diastolic', sa.Integer(), nullable=True),
        sa.Column('neck_in', sa.Float(), nullable=True),
        sa.Column('waist_in', sa.Float(), nullable=True),
        sa.Column('hips_in', sa.Float(), nullable=True),
        sa.Column('biceps_l_in', sa.Float(), nullable=True),
        sa.Column('biceps_r_in', sa.Float(), nullable=True),
        sa.Column('forearm_l_in', sa.Float(), nullable=True),
        sa.Column('forearm_r_in', sa.Float(), nullable=True),
        sa.Column('quad_l_in', sa.Float(), nullable=True),
        sa.Column('quad_r_in', sa.Float(), nullable=True),
        sa.Column('calf_l_in', sa.Float(), nullable=True),
        sa.Column('calf_r_in', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('weight_lbs IS NULL OR weight_lbs > 0', name='ck_body_measurement_weight_pos'),
        sa.CheckConstraint('systolic IS NULL OR systolic > 0', name='ck_body_measurement_systolic_pos'),
        sa.CheckConstraint('diastolic IS NULL OR diastolic > 0', name='ck_body_measurement_diastolic_pos'),
        sa.CheckConstraint('neck_in IS NULL OR neck_in > 0', name='ck_body_measurement_neck_pos'),
        sa.CheckConstraint('waist_in IS NULL OR waist_in > 0', name='ck_body_measurement_waist_pos'),
        sa.CheckConstraint('hips_in IS NULL OR hips_in > 0', name='ck_body_measurement_hips_pos'),
        sa.CheckConstraint('biceps_l_in IS NULL OR biceps_l_in > 0', name='ck_body_measurement_biceps_l_pos'),
        sa.CheckConstraint('biceps_r_in IS NULL OR biceps_r_in > 0', name='ck_body_measurement_biceps_r_pos'),
        sa.CheckConstraint('forearm_l_in IS NULL OR forearm_l_in > 0', name='ck_body_measurement_forearm_l_pos'),
        sa.CheckConstraint('forearm_r_in IS NULL OR forearm_r_in > 0', name='ck_body_measurement_forearm_r_pos'),
        sa.CheckConstraint('quad_l_in IS NULL OR quad_l_in > 0', name='ck_body_measurement_quad_l_pos'),
        sa.CheckConstraint('quad_r_in IS NULL OR quad_r_in > 0', name='ck_body_measurement_quad_r_pos'),
        sa.CheckConstraint('calf_l_in IS NULL OR calf_l_in > 0', name='ck_body_measurement_calf_l_pos'),
        sa.CheckConstraint('calf_r_in IS NULL OR calf_r_in > 0', name='ck_body_measurement_calf_r_pos'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_body_measurements_owner_id', 'body_measurements', ['owner_id'])
    op.create_index('ix_body_measurements_measured_at', 'body_measurements', ['measured_at'])


def downgrade() -> None:
    op.drop_index('ix_body_measurements_measured_at', table_name='body_measurements')
    op.drop_index('ix_body_measurements_owner_id', table_name='body_measurements')
    op.drop_table('body_measurements')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('water_goal_oz')
        batch_op.drop_column('custom_fat_pct')
        batch_op.drop_column('custom_carb_pct')
        batch_op.drop_column('custom_protein_pct')
        batch_op.drop_column('diet_preset')
        batch_op.drop_column('macro_goal')
        batch_op.drop_column('activity_level')
        batch_op.drop_column('height_in')
        batch_op.drop_column('birth_date')
        batch_op.drop_column('sex')
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_migrations.py -k test_0017 -v`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions (purely additive).

- [ ] **Step 9: Commit**

```bash
git add app/models.py migrations/versions/0017_weight_measurements.py tests/test_migrations.py
git commit -m "feat: add weight & measurements tables and macro-calculator profile fields"
```

---

### Task 2: Calculation modules (macros/TDEE, water goal, BMI, BF%)

**Files:**
- Create: `app/measurements/__init__.py` (empty)
- Create: `app/measurements/calculations.py`
- Test: `tests/test_measurements.py`

**Interfaces:**
- Produces: `bmr()`, `tdee()`, `target_calories()`, `macros_for_preset()`, `water_goal_oz()`,
  `water_pace()`, `bmi()`, `body_fat_pct()` — Task 5's page-rendering code calls all of these.

Pure functions only — no database access, no FastAPI imports.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_measurements.py`:

```python
import pytest

from app.measurements.calculations import (
    bmi, bmr, body_fat_pct, macros_for_preset, target_calories, tdee, water_goal_oz, water_pace,
)
from app.models import ActivityLevel, BiologicalSex, DietPreset, MacroGoal


def test_bmr_male():
    # 180 lb (~81.65 kg), 70 in (~177.8 cm), age 30, male.
    value = bmr(180, 70, 30, BiologicalSex.MALE)
    assert value == pytest.approx(1799.3, abs=1.0)


def test_bmr_female():
    value = bmr(140, 65, 28, BiologicalSex.FEMALE)
    assert value == pytest.approx(1373.9, abs=1.0)


def test_tdee_multiplies_by_activity_value():
    assert tdee(1800, ActivityLevel.SEDENTARY) == pytest.approx(1800 * 1.2)
    assert tdee(1800, ActivityLevel.EXTRA_ACTIVE) == pytest.approx(1800 * 1.9)


def test_target_calories_applies_goal_offset_without_flooring():
    calories, floored = target_calories(2500, MacroGoal.MODERATE_LOSS, BiologicalSex.MALE)
    assert calories == pytest.approx(2000) and floored is False


def test_target_calories_floors_at_1500_for_male():
    calories, floored = target_calories(1800, MacroGoal.AGGRESSIVE_LOSS, BiologicalSex.MALE)
    assert calories == 1500 and floored is True


def test_target_calories_floors_at_1200_for_female():
    calories, floored = target_calories(1500, MacroGoal.AGGRESSIVE_LOSS, BiologicalSex.FEMALE)
    assert calories == 1200 and floored is True


def test_macros_for_balanced_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.BALANCED)
    assert protein == pytest.approx(150) and carb == pytest.approx(200) and fat == pytest.approx(66.7, abs=0.1)


def test_macros_for_keto_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.KETO)
    assert protein == pytest.approx(100) and carb == pytest.approx(25) and fat == pytest.approx(166.7, abs=0.1)


def test_macros_for_custom_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.CUSTOM, custom=(35, 35, 30))
    assert protein == pytest.approx(175) and carb == pytest.approx(175) and fat == pytest.approx(66.7, abs=0.1)


def test_macros_for_custom_preset_rejects_ratios_not_summing_to_100():
    with pytest.raises(ValueError):
        macros_for_preset(2000, DietPreset.CUSTOM, custom=(35, 35, 20))


def test_macros_for_custom_preset_requires_custom_ratios():
    with pytest.raises(ValueError):
        macros_for_preset(2000, DietPreset.CUSTOM, custom=None)


def test_water_goal_default_is_half_bodyweight():
    assert water_goal_oz(200, None) == 100


def test_water_goal_uses_override_when_set():
    assert water_goal_oz(200, 120) == 120


def test_water_pace_breaks_down_by_hour():
    pace = water_pace(200)
    assert pace["oz_per_hour"] == pytest.approx(12.5)
    assert pace["cups_per_hour"] == pytest.approx(1.5625)
    assert pace["bottles_per_hour"] == pytest.approx(0.7396, abs=0.001)


def test_bmi():
    assert bmi(180, 70) == pytest.approx(25.83, abs=0.01)


def test_body_fat_pct_male():
    value = body_fat_pct(BiologicalSex.MALE, height_in=70, neck_in=15, waist_in=34)
    assert value == pytest.approx(13.0, abs=0.5)


def test_body_fat_pct_female_requires_hips():
    assert body_fat_pct(BiologicalSex.FEMALE, height_in=65, neck_in=13, waist_in=28) is None


def test_body_fat_pct_female_with_hips():
    value = body_fat_pct(BiologicalSex.FEMALE, height_in=65, neck_in=13, waist_in=28, hips_in=38)
    assert value is not None and 15 < value < 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_measurements.py -v`
Expected: FAIL — `ModuleNotFoundError: app.measurements`.

- [ ] **Step 3: Write `app/measurements/calculations.py`**

```python
"""Pure-function body-composition and nutrition calculations -- no database access. All formulas
and constants are documented inline since they're sourced from a specific reference calculator
(see the spec) rather than invented."""

import math

from app.models import ActivityLevel, BiologicalSex, DietPreset, MacroGoal

_KCAL_PER_GRAM = {"protein": 4, "carb": 4, "fat": 9}
_PRESET_RATIOS = {
    DietPreset.BALANCED: (0.30, 0.40, 0.30),
    DietPreset.HIGH_PROTEIN: (0.40, 0.30, 0.30),
    DietPreset.LOW_CARB: (0.40, 0.15, 0.45),
    DietPreset.KETO: (0.20, 0.05, 0.75),
}
_SAFE_FLOOR = {BiologicalSex.MALE: 1500, BiologicalSex.FEMALE: 1200}


def bmr(weight_lbs: float, height_in: float, age: int, sex: BiologicalSex) -> float:
    """Mifflin-St Jeor. Men: 10*kg + 6.25*cm - 5*age + 5. Women: same, -161 instead of +5."""
    kg = weight_lbs * 0.453592
    cm = height_in * 2.54
    base = 10 * kg + 6.25 * cm - 5 * age
    return base + (5 if sex == BiologicalSex.MALE else -161)


def tdee(bmr_value: float, activity_level: ActivityLevel) -> float:
    return bmr_value * float(activity_level.value)


def target_calories(tdee_value: float, goal: MacroGoal, sex: BiologicalSex) -> tuple[float, bool]:
    """Returns (calories, floor_was_applied)."""
    calories = tdee_value + int(goal.value)
    floor = _SAFE_FLOOR[sex]
    if calories < floor:
        return floor, True
    return calories, False


def macros_for_preset(calories: float, preset: DietPreset,
                      custom: tuple[int, int, int] | None = None) -> tuple[float, float, float]:
    """Returns (protein_g, carb_g, fat_g)."""
    if preset == DietPreset.CUSTOM:
        if custom is None:
            raise ValueError("Custom diet preset requires custom protein/carb/fat percentages.")
        if sum(custom) != 100:
            raise ValueError("Custom macro percentages must sum to 100.")
        protein_pct, carb_pct, fat_pct = (p / 100 for p in custom)
    else:
        protein_pct, carb_pct, fat_pct = _PRESET_RATIOS[preset]
    protein_g = (calories * protein_pct) / _KCAL_PER_GRAM["protein"]
    carb_g = (calories * carb_pct) / _KCAL_PER_GRAM["carb"]
    fat_g = (calories * fat_pct) / _KCAL_PER_GRAM["fat"]
    return protein_g, carb_g, fat_g


def water_goal_oz(weight_lbs: float, override_oz: int | None) -> int:
    return override_oz if override_oz is not None else round(weight_lbs / 2)


def water_pace(goal_oz: int, awake_hours: int = 16) -> dict:
    oz_per_hour = goal_oz / awake_hours
    return {
        "oz_per_hour": oz_per_hour,
        "cups_per_hour": oz_per_hour / 8,
        "bottles_per_hour": oz_per_hour / 16.9,
    }


def bmi(weight_lbs: float, height_in: float) -> float:
    return 703 * weight_lbs / (height_in ** 2)


def body_fat_pct(sex: BiologicalSex, height_in: float, neck_in: float, waist_in: float,
                 hips_in: float | None = None) -> float | None:
    """US Navy circumference method. Returns None for a female measurement missing hips_in --
    there is no valid formula without it, never guess with a zero."""
    if sex == BiologicalSex.MALE:
        denom = 1.0324 - 0.19077 * math.log10(waist_in - neck_in) + 0.15456 * math.log10(height_in)
    else:
        if hips_in is None:
            return None
        denom = (1.29579 - 0.35004 * math.log10(waist_in + hips_in - neck_in)
                + 0.22100 * math.log10(height_in))
    return 495 / denom - 450
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_measurements.py -v`
Expected: All PASS. If any `pytest.approx` value doesn't match (the sketch's expected numbers were
computed by hand for this plan, not run against real code — double-check each by computing it
yourself before assuming the implementation is wrong), recompute the expected value from the
formula itself and fix the test's own expected number, not the formula, unless you find an actual
transcription error against the spec's stated formula.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/measurements/ tests/test_measurements.py
git commit -m "feat: add macro/TDEE, water goal, BMI, and body-fat calculation functions"
```

---

### Task 3: Settings additions for the new profile fields

**Files:**
- Modify: `app/routers/settings.py`
- Modify: `app/templates/settings/settings.html`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `BiologicalSex`/`ActivityLevel`/`MacroGoal`/`DietPreset` (Task 1).
- Produces: `POST /settings/body-profile` — Task 5's Macros tab reads the fields this sets.

This task has no fully verbatim brief code, by design: read `app/routers/settings.py`'s existing
`/settings/discard-window` route (already used as a template by several earlier features in this
codebase) and mirror its exact shape for a new route handling all ten new fields at once (sex,
birth date, height, activity level, macro goal, diet preset, the three custom percentages, and the
water goal override). Read `app/templates/settings/settings.html`'s existing form conventions
(`{{ err(...) }}`, `has-error` class, `<select>` populated from an enum's `.label`/`.value`) before
writing the new form section.

- [ ] **Step 1: Write the failing tests**

```python
def test_body_profile_saves_all_fields(client, db):
    r = client.post("/settings/body-profile", data={
        "sex": "Male", "birth_date": "1990-01-15", "height_in": "70",
        "activity_level": "1.55", "macro_goal": "-500", "diet_preset": "balanced",
        "water_goal_oz": "100",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        me = s.scalar(select(User).where(User.username_key == "tester"))
        assert me.sex.value == "Male" and me.height_in == 70.0
        assert me.activity_level.value == "1.55" and me.macro_goal.value == "-500"
        assert me.diet_preset.value == "balanced" and me.water_goal_oz == 100


def test_body_profile_custom_diet_requires_percentages_summing_to_100(client, db):
    r = client.post("/settings/body-profile", data={
        "diet_preset": "custom", "custom_protein_pct": "40", "custom_carb_pct": "40",
        "custom_fat_pct": "10",
    })
    assert r.status_code == 422


def test_body_profile_fields_are_all_optional(client, db):
    r = client.post("/settings/body-profile", data={}, follow_redirects=False)
    assert r.status_code == 303
```

Fill in any additional fields the real route ends up needing based on how you structure the form,
and check `tests/test_settings.py`'s existing conventions for the `client`/`db` fixtures and
`SessionLocal`/`select`/`User` imports before assuming this exact shape is complete.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_settings.py -k body_profile -v`
Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Add the route and template section**

Mirror `/settings/discard-window`'s exact validation/error-handling shape. All ten fields are
optional (a blank field stays `None`/unchanged) except: if `diet_preset == "custom"`, the three
custom percentages become required and must sum to 100 (reuse
`app.measurements.calculations.macros_for_preset`'s own validation by calling it with a dummy
calorie value inside a `try/except ValueError` to get the exact same rule in one place, rather than
re-implementing the sum-to-100 check a second time — or add a small dedicated
`validate_custom_ratio(protein, carb, fat)` helper to `app/measurements/calculations.py` if
duplicating the check feels cleaner; your call, but don't silently diverge from Task 2's own rule).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_settings.py -k body_profile -v`
Expected: All PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/routers/settings.py app/templates/settings/settings.html tests/test_settings.py
git commit -m "feat: add body-profile settings for the macro calculator"
```

---

### Task 4: Measurements page — router, entry form, tabbed layout

**Files:**
- Create: `app/routers/measurements.py`
- Create: `app/templates/measurements/index.html`
- Modify: `app/main.py` (register router)
- Modify: `app/templates/base.html` (nav link)
- Test: `tests/test_measurements_page.py`

**Interfaces:**
- Consumes: `BodyMeasurement` (Task 1).
- Produces: `GET /measurements`, `POST /measurements` (new entry) — Task 5 and Task 6 both extend
  this same route/template with their own additional context (silhouette/macros, charts).

This task builds the page's skeleton and the entry form ONLY — no silhouette, no macros display,
no charts yet (Tasks 5 and 6 add those on top). Read `app/routers/protocols.py`'s
`_protocol_query`/`_shared_protocol_query` pair directly and mirror that exact shape for a new
`_measurement_query(uid)`/`_shared_measurement_query(uid)` pair scoped to `ShareCategory.
PERSONAL_DATA`. Read `app/templates/inventory/list.html` or `app/templates/vendors/detail.html` for
this app's established tabbed-section pattern (if one exists — check for a `<nav class="tabs">`-
style convention or similar before inventing a new tab UI) before building the Measurements /
Macros / Journal / Labs tab structure.

- [ ] **Step 1: Write the failing tests**

```python
def test_measurements_page_loads_with_empty_state(client, db):
    r = client.get("/measurements")
    assert r.status_code == 200
    assert "No measurements logged yet" in r.text or "Log a measurement" in r.text


def test_new_measurement_entry_all_fields_optional(client, db):
    r = client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "180"},
                    follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        me = s.scalar(select(User).where(User.username_key == "tester"))
        [bm] = s.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == me.id)).all()
        assert bm.weight_lbs == 180.0 and bm.neck_in is None


def test_new_measurement_entry_full_session(client, db):
    r = client.post("/measurements", data={
        "measured_at": "2026-09-28", "weight_lbs": "180", "systolic": "120", "diastolic": "80",
        "neck_in": "15.5", "waist_in": "34", "hips_in": "38",
        "biceps_l_in": "16", "biceps_r_in": "16.3",
        "forearm_l_in": "12", "forearm_r_in": "12.1",
        "quad_l_in": "22", "quad_r_in": "22.2",
        "calf_l_in": "15", "calf_r_in": "15.1",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        me = s.scalar(select(User).where(User.username_key == "tester"))
        [bm] = s.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == me.id)).all()
        assert bm.biceps_l_in == 16.0 and bm.biceps_r_in == 16.3


def test_new_measurement_rejects_non_positive_weight(client, db):
    r = client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "-5"})
    assert r.status_code == 422


def test_measurements_page_respects_personal_data_sharing(client, db):
    # implementer: register a second user (check tests/test_dashboard.py or tests/test_vendors_page.py
    # for the established second-user-registration helper), have them log a measurement, assert it
    # is absent from the first user's page; create a Share(category=PERSONAL_DATA) from that second
    # user to the first, assert it now appears (tagged with the owner's name, matching this app's
    # established shared-item display convention).
    ...
```

Fill in the `...` with a complete, real test body once you've read the established second-user +
`Share` construction pattern this codebase already uses in several other test files.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_measurements_page.py -v`
Expected: FAIL — 404s (route doesn't exist).

- [ ] **Step 3-4: Implement the router and template**

Implement `GET /measurements` (lists recent entries, empty-state message, the entry form) and
`POST /measurements` (parses all sixteen optional fields, defaults `measured_at` to today,
validates positivity the same way other numeric fields in this app are validated, creates one
`BodyMeasurement` row). Build the tabbed layout with Measurements/Macros/Journal/Labs tabs, the
latter two as static "Coming in a future update" placeholder cards (matching the Dashboard's own
established placeholder-card wording and pattern exactly).

- [ ] **Step 5: Register the router and nav link**

In `app/main.py`, add `measurements` to the router import list and
`app.include_router(measurements.router)`. In `app/templates/base.html`, add a nav link (e.g.
"Body" or "Weight & Measurements" — pick a concise label consistent with this app's other short nav
labels like "Inventory"/"Vendors"/"Protocols").

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_measurements_page.py -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 8: Commit**

```bash
git add app/routers/measurements.py app/templates/measurements/ app/main.py app/templates/base.html tests/test_measurements_page.py
git commit -m "feat: add Weight & Measurements page with entry form and tabbed layout"
```

---

### Task 5: Body silhouette + Macros tab

**Files:**
- Modify: `app/routers/measurements.py`
- Modify: `app/templates/measurements/index.html`
- Test: `tests/test_measurements_page.py`

**Interfaces:**
- Consumes: `app.measurements.calculations` (Task 2), Task 3's Settings profile fields, Task 4's
  route/template.

- [ ] **Step 1: Write the failing tests**

```python
def test_silhouette_shows_average_of_bilateral_measurement(client, db):
    client.post("/measurements", data={
        "measured_at": "2026-09-20", "biceps_l_in": "16", "biceps_r_in": "16",
    })
    client.post("/measurements", data={
        "measured_at": "2026-09-27", "biceps_l_in": "16", "biceps_r_in": "16.3",
    })
    t = html.unescape(client.get("/measurements").text)
    assert "16.15" in t  # average of the most recent entry's two sides


def test_silhouette_shows_change_since_previous_entry(client, db):
    client.post("/measurements", data={"measured_at": "2026-09-20", "waist_in": "34"})
    client.post("/measurements", data={"measured_at": "2026-09-27", "waist_in": "33.5"})
    t = html.unescape(client.get("/measurements").text)
    assert "-0.5" in t or "−0.5" in t


def test_macros_tab_shows_prompt_when_profile_incomplete(client, db):
    t = client.get("/measurements?tab=macros").text
    assert "profile" in t.lower() or "add your" in t.lower()


def test_macros_tab_computes_from_stored_profile_and_latest_weight(client, db):
    client.post("/settings/body-profile", data={
        "sex": "Male", "birth_date": "1996-01-01", "height_in": "70",
        "activity_level": "1.55", "macro_goal": "0", "diet_preset": "balanced",
    })
    client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "180"})
    r = client.get("/measurements?tab=macros")
    assert r.status_code == 200
    # implementer: assert the page shows a plausible calorie figure -- compute the expected TDEE
    # from app.measurements.calculations directly in the test (same inputs) and assert that number
    # (rounded) appears in the response text, rather than hard-coding a number computed by hand here.


def test_water_goal_and_pace_shown_on_measurements_tab(client, db):
    client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "200"})
    t = client.get("/measurements").text
    assert "100" in t  # default water goal: 200/2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_measurements_page.py -k "silhouette or macros_tab or water_goal" -v`
Expected: FAIL for the right reason (missing content, not a typo).

- [ ] **Step 3: Wire the silhouette**

For the most recent `BodyMeasurement` row, compute each bilateral measurement's average (skip
missing sides per Review Focus item 1 — if only one side exists for that entry, show that one side
alone, clearly labeled, never averaged with `None`/zero), and the delta against whichever earlier
entry most recently had a non-null value for that same field (not necessarily the immediately
previous row, if that row happened to skip that particular measurement). Render the SVG silhouette
inline in the template (a plain, abstract front-facing outline — reuse `app/templates/dosing/
today.html`'s injection-site SVG as your visual/structural reference for how this app already does
a body outline, adapting the point positions for these 7 measurement locations instead of injection
sites).

- [ ] **Step 4: Wire the Macros tab**

Read the signed-in user's profile fields (Task 3) and most recent `weight_lbs`. If `sex`,
`birth_date`, `height_in`, or `activity_level` is missing, show a clear prompt linking to Settings
instead of computing anything (Review Focus item 5) — never let a missing field raise an
unhandled exception. Otherwise call `bmr()` → `tdee()` → `target_calories()` → `macros_for_preset()`
in sequence and display the calorie target (with the floor-adjusted notice when `target_calories()`
returns `True`) and the macro breakdown. Also display the water goal (`water_goal_oz()` +
`water_pace()`) somewhere sensible on this tab or the Measurements tab — the spec doesn't pin down
which tab exactly; use your judgment, but be consistent about it and don't put it on both.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_measurements_page.py -v`
Expected: All PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add app/routers/measurements.py app/templates/measurements/ tests/test_measurements_page.py
git commit -m "feat: add body silhouette and Macros tab to the Weight & Measurements page"
```

---

### Task 6: Charts with adjustable range selector

**Files:**
- Modify: `app/routers/measurements.py`
- Modify: `app/templates/measurements/index.html`
- Test: `tests/test_measurements_page.py`

**Interfaces:**
- Consumes: `BodyMeasurement` history (Tasks 1/4).

- [ ] **Step 1: Write the failing tests**

```python
def test_charts_default_to_a_range(client, db):
    r = client.get("/measurements")
    assert r.status_code == 200


def test_charts_range_query_param_changes_window(client, db):
    old = date(2026, 1, 1)
    recent = date(2026, 9, 28)
    with SessionLocal() as s:
        me = s.scalar(select(User).where(User.username_key == "tester"))
        s.add(BodyMeasurement(owner_id=me.id, measured_at=old, weight_lbs=190))
        s.add(BodyMeasurement(owner_id=me.id, measured_at=recent, weight_lbs=180))
        s.commit()
    r_lifetime = client.get("/measurements?range=lifetime")
    r_7d = client.get(f"/measurements?range=7d&as_of={recent.isoformat()}")
    assert "190" in r_lifetime.text
    assert "190" not in r_7d.text  # outside the 7-day window from the pinned "as_of" date


def test_charts_reject_unknown_range_falls_back_to_default(client, db):
    r = client.get("/measurements?range=bogus")
    assert r.status_code == 200  # never a 500 on a garbage query param
```

Read `app/routers/calendar.py`'s own `view`/`date` query-param handling (the closest existing
precedent for a range/anchor-date-driven page in this app) before deciding the exact query-param
names and whether an `as_of` override is worth supporting for real (it's included above mainly to
make the test deterministic — decide whether real users need it or whether the test should instead
just use `date.today()` and accept a slightly less deterministic window).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_measurements_page.py -k "chart" -v`
Expected: FAIL — no range handling exists yet.

- [ ] **Step 3: Implement the range selector and SVG charts**

Add a `range` query parameter accepting exactly the seven values named in the spec (`7d`, `14d`,
`1mo`, `3mo`, `6mo`, `1yr`, `lifetime`), defaulting to one sensible choice (e.g. `3mo`) when absent
or unrecognized — never a 500 on a garbage value. Query `BodyMeasurement` rows within that window
for the signed-in user (and anyone sharing Personal Data with them, per Task 4's visibility
helpers), and render one simple SVG line chart per tracked series (weight, each measurement, BMI,
BF%, systolic/diastolic) — plot points scaled to the SVG's own viewBox from the queried rows'
dates/values, no external library.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_measurements_page.py -v`
Expected: All PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/routers/measurements.py app/templates/measurements/ tests/test_measurements_page.py
git commit -m "feat: add adjustable-range SVG charts to the Weight & Measurements page"
```

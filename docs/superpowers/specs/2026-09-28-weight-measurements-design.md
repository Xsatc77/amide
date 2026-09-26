# Weight & Measurements Design (Roadmap Phase 6, part 1 of 3)

**Goal:** the first of three Phase 6 sub-projects (Weight & Measurements, then Journal, then Labs —
each its own spec/plan/build cycle). A body-tracking page: scale weight, blood pressure, and
circumference measurements logged over time; a TDEE/macro calculator; a water-intake goal with an
hourly cups/bottles pace; a body silhouette showing each measurement's current value and trend; and
adjustable-range charts. Journal and Labs get reserved-but-placeholder tabs on this same page, so
its layout doesn't need reshuffling when they're built next.

**Deferred, explicitly out of scope for this spec:** actual water-intake logging (oz entry, or
clicking off cups/bottles as they're drunk) — this build only computes and displays the goal and
its pace; metric units (Imperial-only for now — lb/ft/in — matching this app's existing
English-labeled conventions; metric is a clean future addition, not built now); any BF%/BMI/TDEE
formula other than the ones named below; Journal and Labs themselves (separate specs).

## Data model

`User` gains the profile fields the calculator needs, following this app's existing
`default_discard_days`-style "a few extra nullable columns" pattern:

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

`User` additions:
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
    water_goal_oz: Mapped[int | None] = mapped_column(Integer)  # None -> bodyweight/2 computed default
```

New table:
```python
class BodyMeasurement(Base):
    """One weigh-in/measurement session. Every field nullable -- log just weight some days, a full
    tape-measure session on others. Bilateral parts store both sides; the silhouette/charts show
    their average, the entry's own detail view shows both raw numbers."""
    __tablename__ = "body_measurements"
    __table_args__ = (
        CheckConstraint("weight_lbs IS NULL OR weight_lbs > 0", name="ck_body_measurement_weight_pos"),
        # ... analogous positive-value CheckConstraints for every measurement column, and
        # systolic/diastolic each > 0 when set, mirroring this app's existing constraint style.
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

Sharing: `BodyMeasurement` (and the profile fields above) fold into the existing
`ShareCategory.PERSONAL_DATA` category — the same one Protocols already use — rather than a new
category. A page/query visibility helper mirrors the existing `_visible_items`-style pattern already
established for Inventory, scoped to `PERSONAL_DATA` instead of `INVENTORY`.

## Macro/TDEE calculator

New pure-function module `app/measurements/macros.py`, no database access:

```python
def bmr(weight_lbs: float, height_in: float, age: int, sex: BiologicalSex) -> float:
    """Mifflin-St Jeor. Weight/height converted to kg/cm internally; the equation itself is defined
    in metric. Men: 10*kg + 6.25*cm - 5*age + 5. Women: same, -161 instead of +5."""

def tdee(bmr_value: float, activity_level: ActivityLevel) -> float:
    """bmr * float(activity_level.value) -- the activity multiplier IS the enum's stored value
    (1.2 through 1.9), so this is a direct multiply, no separate lookup table to keep in sync."""

def target_calories(tdee_value: float, goal: MacroGoal, sex: BiologicalSex) -> tuple[float, bool]:
    """tdee + int(goal.value), clamped to a hard floor (1500 kcal male, 1200 kcal female) with a
    (calories, floor_was_applied) tuple so the UI can show the same visible "adjusted" notice the
    source calculator does."""

def macros_for_preset(calories: float, preset: DietPreset, custom: tuple[int, int, int] | None = None) -> tuple[float, float, float]:
    """Returns (protein_g, carb_g, fat_g). Preset ratios: balanced 30/40/30, high_protein 40/30/30,
    low_carb 40/15/45, keto 20/5/75 (protein/carb/fat). Protein and carbs are 4 kcal/g, fat is
    9 kcal/g. DietPreset.CUSTOM requires `custom` (three percentages) and raises ValueError if they
    don't sum to exactly 100 -- matching the source calculator's own refusal rule."""
```

The Weight & Measurements page's Macros tab reads the user's stored profile fields plus their most
recent `BodyMeasurement.weight_lbs`, computes live (no stored "calculation" row — this is a
read-time derivation, same philosophy as the Dashboard's cost snapshot), and shows the same
calories-then-macros breakdown the source site does, plus the floor-adjusted notice when it applies.
Age is derived from `birth_date` at render time, never stored redundantly.

## Water goal

```python
def water_goal_oz(weight_lbs: float, override_oz: int | None) -> int:
    """override_oz if the user set one, else round(weight_lbs / 2)."""

def water_pace(goal_oz: int, awake_hours: int = 16) -> dict:
    """Returns the hourly pace in three units: {"oz_per_hour": ..., "cups_per_hour": ...,
    "bottles_per_hour": ...} -- goal_oz / awake_hours, then / 8 for cups and / 16.9 for bottles.
    Displayed as e.g. "1.5 cups per hour, or 0.75 bottles per hour" (one decimal place), alongside
    the plain daily total ("Goal: 200 oz today")."""
```

No tracking/logging UI — this is display-only, per the explicit scope cut above.

## Body measurements + blood pressure entry

One form per session: weight, systolic/diastolic, and the seven measurement points (neck, biceps
L/R, forearms L/R, waist, hips, quads L/R, calf L/R) — every field optional, so a quick weigh-in
doesn't force filling in a full tape-measure session. Saves one `BodyMeasurement` row per submission
(not one row per field), dated to when it was taken (defaults to today, editable for a late entry).

The weight field carries an info icon (a small ⓘ, matching whatever hover/tap-for-tooltip pattern
this app already uses elsewhere, if any exists — otherwise a plain `title` attribute) with this
exact text: *"Best to weigh within 1 hour of waking, at least once a week, no clothing."*

## BF% and BMI (computed at read time, never stored)

```python
def bmi(weight_lbs: float, height_in: float) -> float:
    """703 * weight_lbs / height_in**2 -- standard BMI formula."""

def body_fat_pct(sex: BiologicalSex, height_in: float, neck_in: float, waist_in: float,
                 hips_in: float | None = None) -> float | None:
    """US Navy circumference method -- the only BF% formula computable from tape-measure data.
    Male: 495 / (1.0324 - 0.19077*log10(waist-neck) + 0.15456*log10(height)) - 450.
    Female (needs hips too, returns None if hips_in is missing): 495 / (1.29579 -
    0.35004*log10(waist+hips-neck) + 0.22100*log10(height)) - 450."""
```

Both are computed on demand wherever a measurement entry or chart point is displayed — never stored
as their own columns (a stored BF%/BMI would go stale the moment the underlying formula constants
are revisited, and it's cheap to recompute from data already on hand).

## Body silhouette

Reuses the exact pattern Daily Dosing's injection-site picker already established (a hand-drawn,
abstract front-facing SVG body outline with positioned points, no external art asset): one static
front-view silhouette with a label anchored near each of the 7 measurement points, each showing
`{{ current value }} ({{ +/- change since the previous entry }})` — e.g. "Biceps: 16.15" (+0.3")".
Bilateral measurements show the average of L/R on the silhouette; the underlying entry's own detail
view (a plain table row, not the silhouette) shows both raw sides. Neck/waist/hips (unilateral) show
their single value directly. Weight and blood pressure aren't body-part measurements and don't
appear on the silhouette — they get their own simple stat display above or beside it.

## Charts

Hand-drawn SVG line charts (no external charting library — continuing this app's existing
zero-external-JS-dependency convention, already followed by the Calculator's syringe visual and the
injection-site silhouette itself). One chart per tracked series (weight, each measurement,
BMI, BF%, blood pressure), each with the same range-selector control: **7 days, 14 days, 1 month,
3 months, 6 months, 1 year, lifetime**. Selecting a range re-queries and re-renders that chart's own
SVG points — a plain page reload or a small fetch-and-redraw, whichever fits this app's existing
convention for similar controls (check the Calendar's own view-switcher for precedent before
picking).

## Page layout

New router `app/routers/measurements.py`, new template `app/templates/measurements/index.html`
(or a small directory if the page grows), reachable from a new nav link. Tabbed layout:
**Measurements** (entry form, silhouette, charts) | **Macros** (calculator) | **Journal**
(placeholder card, "Coming in a future update") | **Labs** (placeholder card, same wording) — the
placeholder tabs exist now purely to reserve their layout slot, exactly matching the Dashboard's
own established placeholder-card precedent for not-yet-built widgets.

## Review focus

1. A bilateral measurement missing one side (e.g. only the left bicep was measured this session)
   must not silently average with a stale or zero value — the silhouette either shows the one side
   that exists (clearly labeled which) or omits that point for that entry, never a wrong average.
2. The macro calculator's safe-floor clamp must actually floor at 1500/1200 kcal and show the
   adjusted-notice, mirroring the source site's own hard limit — never silently show a target below
   the floor as if it were unadjusted.
3. `DietPreset.CUSTOM` with percentages that don't sum to 100 must be rejected with a clear error,
   never silently normalized or divided unevenly.
4. The Navy BF% formula's female branch requires `hips_in` — a female user's entry missing a hip
   measurement must show "not enough data" for BF%, never a wrong number computed by treating the
   missing hip value as zero.
5. Every profile field the calculator needs (sex, birth date, height, activity level) must be
   genuinely optional at the database level, with the Macros tab showing a clear "add your profile
   info to see this" prompt rather than crashing when one is missing — a new user shouldn't be
   blocked from using the rest of the page just because they haven't filled in the calculator's
   inputs yet.

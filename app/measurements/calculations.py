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


def water_pace_left(goal_oz: float, consumed_oz: float, hour: float, awake_until: int = 22) -> dict:
    """What it takes to reach the goal from the CURRENT level: the ounces still to drink over the waking hours that are left today
    (the day's drinking window ends at `awake_until`; the last hour is never counted as less than one so the figure stays sane late at night)."""
    remaining = max(0.0, goal_oz - consumed_oz)
    hours_left = min(16.0, max(1.0, awake_until - hour))
    oz_per_hour = remaining / hours_left
    return {"done": remaining <= 0, "remaining_oz": remaining, "hours_left": hours_left, "oz_per_hour": oz_per_hour,
            "cups_per_hour": oz_per_hour / 8, "bottles_per_hour": oz_per_hour / 16.9}


def bmi(weight_lbs: float, height_in: float | None) -> float | None:
    """Returns None when height is missing or non-positive -- there is no valid BMI without a
    real height, never divide by zero/negative and crash the caller."""
    if height_in is None or height_in <= 0:
        return None
    return 703 * weight_lbs / (height_in ** 2)


def body_fat_pct(sex: BiologicalSex, height_in: float, neck_in: float, waist_in: float,
                 hips_in: float | None = None) -> float | None:
    """US Navy circumference method. Returns None for a female measurement missing hips_in --
    there is no valid formula without it, never guess with a zero. Also returns None (rather than
    raising) whenever the log10 argument would be <= 0 -- e.g. waist == neck, or a badly-entered
    measurement -- since math.log10 of a non-positive number is undefined, not just imprecise."""
    if sex == BiologicalSex.MALE:
        log_arg = waist_in - neck_in
        if log_arg <= 0:
            return None
        denom = 1.0324 - 0.19077 * math.log10(log_arg) + 0.15456 * math.log10(height_in)
    else:
        if hips_in is None:
            return None
        log_arg = waist_in + hips_in - neck_in
        if log_arg <= 0:
            return None
        denom = (1.29579 - 0.35004 * math.log10(log_arg)
                + 0.22100 * math.log10(height_in))
    return 495 / denom - 450


def weight_since_start(points: list, start) -> tuple[float, object] | None:
    """The change in weight from the weigh-in at `start` to the latest one, as (change, the baseline's date); None when it cannot be said.

    `points` are (date, weight) pairs in the order recorded. The baseline is the last weigh-in on or before `start`, or, if there is none,
    the first one after it. Several weigh-ins on one day count as the last one recorded. Nothing to compare when the baseline is the latest."""
    if not points or start is None:
        return None
    ordered = sorted(points, key=lambda p: p[0])
    before = [p for p in ordered if p[0] <= start]
    baseline, latest = (before[-1] if before else ordered[0]), ordered[-1]
    if baseline[0] >= latest[0]:
        return None
    return round(latest[1] - baseline[1], 1), baseline[0]

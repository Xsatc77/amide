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

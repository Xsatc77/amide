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

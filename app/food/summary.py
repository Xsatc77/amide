"""A day of eating for the Food tab: the entries by meal, the totals, and (when the profile allows) the targets."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.food.targets import day_targets, deficit, fulfilment, sum_totals
from app.measurements import tdee
from app.models import FOOD_MEAL_LABELS, FOOD_MEALS, BiologicalSex, DietPreset, FoodLog, MacroGoal, User
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

    report = tdee.report(male=user.sex == BiologicalSex.MALE, age=_age(user.birth_date, date.today()), height_in=user.height_in,
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

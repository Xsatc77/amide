"""One line of numbers per day for the Journal: water, food calories, workout calories burned and the estimated deficit. A few queries
for all the days at once, never one set per day."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock
from app import water as water_log
from app.food import fiber as pill_fiber
from app.food.rings import macro_ring
from app.food.targets import deficit
from app.measurements import tdee
from app.models import BiologicalSex, FoodLog, User, WaterLog
from app.workouts import logging as workout_logging
from app.workouts import progress


def burn_by_day(session: Session, uid: int) -> dict[date, float]:
    from app.routers.workout_insights import load_logged     # imported here: that module imports routers and templates
    return progress.daily_net_kcal(load_logged(session, uid))


CARDIO_EQUIPMENT = "Conditioning/Cardio"


def workout_totals(session: Session, uid: int) -> dict[date, dict]:
    """{day: {"cardio_min", "lifted_lb"}} over every completed exercise: cardio minutes are the minutes logged on cardio machines and
    activities, and the weight lifted is sets x reps x load."""
    from app.routers.workout_insights import load_logged     # imported here: that module imports routers and templates
    totals: dict[date, dict] = {}
    for row in load_logged(session, uid):
        day = totals.setdefault(row.log_date, {"cardio_min": 0.0, "lifted_lb": 0.0})
        if row.equipment == CARDIO_EQUIPMENT and row.minutes:
            day["cardio_min"] += row.minutes
        if row.volume_lb:
            day["lifted_lb"] += row.volume_lb
    return totals


def maintenance(session: Session, user: User) -> tuple[float | None, str | None]:
    """(calories to hold weight, why not). It needs sex, birth date, height, activity level and a weigh-in."""
    missing = [label for attr, label in (("sex", "sex"), ("birth_date", "birth date"), ("height_in", "height"),
                                         ("activity_level", "activity level")) if getattr(user, attr) is None]
    if missing:
        return None, "Add your " + ", ".join(missing) + " to your profile for a deficit."
    weight = workout_logging.latest_body_weight(session, user.id)
    if weight is None:
        return None, "Log a weigh-in for a deficit."
    today, born = clock.today(), user.birth_date
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return tdee.report(male=user.sex == BiologicalSex.MALE, age=age, height_in=user.height_in, weight_lb=weight,
                       activity_factor=float(user.activity_level.value), life_stage=user.life_stage,
                       measured=tdee.measured_for(user)).tdee, None


def day_overviews(session: Session, user: User, days) -> dict[date, dict]:
    """For each of `days` that has water, food or a workout: {water_oz, eaten_kcal, burn_kcal, deficit, deficit_note}."""
    wanted = list(set(days))
    if not wanted:
        return {}
    per_day = water_log.day_water(session, user, min(wanted), max(wanted))
    water = {d: v[0] for d, v in per_day.items() if d in wanted}
    eaten = dict(session.execute(select(FoodLog.eaten_on, func.sum(FoodLog.calories)).where(
        FoodLog.owner_id == user.id, FoodLog.eaten_on.in_(wanted)).group_by(FoodLog.eaten_on)).all())
    macros = {d: (p, c, f, fi) for d, p, c, f, fi in session.execute(select(FoodLog.eaten_on, func.sum(FoodLog.protein_g), func.sum(FoodLog.carb_g), func.sum(FoodLog.fat_g), func.sum(FoodLog.fiber_g)).where(
        FoodLog.owner_id == user.id, FoodLog.eaten_on.in_(wanted)).group_by(FoodLog.eaten_on)).all()}
    pills = pill_fiber.by_day(session, user.id, min(wanted), max(wanted))
    burn = burn_by_day(session, user.id)
    work = workout_totals(session, user.id)
    base, note = maintenance(session, user)
    out = {}
    for day in wanted:
        w, e, b = water.get(day) or 0, eaten.get(day) or 0, burn.get(day) or 0
        cardio, lifted = work.get(day, {}).get("cardio_min", 0), work.get(day, {}).get("lifted_lb", 0)
        if not (w or e or b or cardio or lifted):
            continue
        out[day] = {"water_oz": round(w, 1) if w % 1 else int(w), "water_goal_oz": per_day[day][1] if day in per_day else None, "eaten_kcal": round(e), "macro_ring": macro_ring(*macros[day], extra_fiber_g=pills.get(day, 0)) if day in macros else None, "burn_kcal": round(b),
                    "cardio_min": round(cardio), "lifted_lb": round(lifted),
                    "deficit": deficit(base, b, e) if base is not None and e else None,
                    "deficit_note": note if base is None and e else None}
    return out

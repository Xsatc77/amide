"""One line of numbers per day for the Journal: water, food calories, workout calories burned and the estimated deficit. A few queries
for all the days at once, never one set per day."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock
from app.food.targets import deficit
from app.measurements import tdee
from app.models import BiologicalSex, FoodLog, User, WaterLog
from app.workouts import logging as workout_logging
from app.workouts import progress


def burn_by_day(session: Session, uid: int) -> dict[date, float]:
    from app.routers.workout_insights import load_logged     # imported here: that module imports routers and templates
    return progress.daily_net_kcal(load_logged(session, uid))


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
    water = dict(session.execute(select(WaterLog.logged_at, func.sum(WaterLog.ounces)).where(
        WaterLog.owner_id == user.id, WaterLog.logged_at.in_(wanted)).group_by(WaterLog.logged_at)).all())
    eaten = dict(session.execute(select(FoodLog.eaten_on, func.sum(FoodLog.calories)).where(
        FoodLog.owner_id == user.id, FoodLog.eaten_on.in_(wanted)).group_by(FoodLog.eaten_on)).all())
    burn = burn_by_day(session, user.id)
    base, note = maintenance(session, user)
    out = {}
    for day in wanted:
        w, e, b = water.get(day) or 0, eaten.get(day) or 0, burn.get(day) or 0
        if not (w or e or b):
            continue
        out[day] = {"water_oz": round(w, 1) if w % 1 else int(w), "eaten_kcal": round(e), "burn_kcal": round(b),
                    "deficit": deficit(base, b, e) if base is not None and e else None,
                    "deficit_note": note if base is None and e else None}
    return out

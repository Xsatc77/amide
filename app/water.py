"""A day's water goal as it stood that day. Each log keeps the goal it was logged against, so changing the goal (or losing weight, which moves the default)
never rewrites a past day. Logs from before this was recorded fall back to the current goal."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, WaterLog


def current_goal_oz(session: Session, user: User) -> int | None:
    """Your override if you set one, else half your latest weigh-in in pounds; None when there is neither."""
    from app.measurements.calculations import water_goal_oz
    from app.workouts import logging as workout_logging
    weight = workout_logging.latest_body_weight(session, user.id)
    if user.water_goal_oz is None and weight is None:
        return None
    return water_goal_oz(weight or 0, user.water_goal_oz)


def day_water(session: Session, user: User, since: date, last: date) -> dict[date, tuple[float, int | None]]:
    """{day: (ounces drunk, that day's goal)} for days with a log. The goal is the one on the day's last log, the current goal if that log predates the record."""
    rows = session.execute(select(WaterLog.logged_at, WaterLog.ounces, WaterLog.goal_oz).where(
        WaterLog.owner_id == user.id, WaterLog.logged_at >= since, WaterLog.logged_at <= last).order_by(WaterLog.logged_at, WaterLog.id)).all()
    totals: dict[date, float] = {}
    goals: dict[date, int | None] = {}
    for day, oz, goal in rows:
        totals[day] = totals.get(day, 0) + oz
        goals[day] = goal
    current = current_goal_oz(session, user) if any(g is None for g in goals.values()) else None
    return {day: (totals[day], goals[day] if goals[day] is not None else current) for day in totals}

"""A day's water goal as it stood that day. Each log keeps the goal it was logged against, so changing the goal (or losing weight, which moves the default)
never rewrites a past day. Logs from before this was recorded fall back to the current goal."""

from dataclasses import dataclass
from datetime import date, timedelta

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


@dataclass(frozen=True)
class DayRow:
    day: date
    oz: float
    goal: int | None
    pct: int | None          # share of that day's goal, whole percent
    status: str              # "compliant" | "short" | "none" (nothing logged) | "today" (not finished) | "nogoal"


def history(session: Session, user: User, today: date, days: int = 14) -> list[DayRow]:
    """The last `days` days up to today, oldest first: what was drunk, that day's goal, and whether it met the compliance rule (90% of the goal)."""
    from app.compliance import water_day_ok
    first = today - timedelta(days=days - 1)
    logged = day_water(session, user, first, today)
    current = current_goal_oz(session, user)
    out = []
    for n in range(days):
        day = first + timedelta(days=n)
        oz, goal = logged.get(day, (0.0, None))
        goal = goal if goal is not None else current
        pct = round(100 * oz / goal) if goal else None
        if day == today:
            status = "today"
        elif not goal:
            status = "nogoal"
        elif day not in logged:
            status = "none"
        else:
            status = "compliant" if water_day_ok(oz, goal) else "short"
        out.append(DayRow(day, oz, goal, pct, status))
    return out


def history_summary(rows: list[DayRow]) -> dict:
    """Finished days that met the rule, out of finished days (today is still open and counts for nothing yet)."""
    finished = [r for r in rows if r.status != "today"]
    return {"compliant": sum(1 for r in finished if r.status == "compliant"), "days": len(finished)}

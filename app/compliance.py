"""Compliance with your plans, for the dashboard's four bars: Protocol, H2O, Diet and Workout, each 0-100% over a chosen window.

Rules (all measured over completed days; today is still open, so it only counts once something for it is done):
- Protocol: doses logged (on time or late) out of the doses that were due. A dose due today and not yet logged is pending, not missed.
- H2O: a day is compliant when the water logged is within 10% of the goal (90% to 110%).
- Diet: a day is compliant when food was logged, calories are at or under the day's limit, and protein reaches 90% of its target.
- Workout: scheduled workout days that were logged, out of the scheduled days since the plan began.
From the first day something was recorded for a category, a day with nothing logged counts against it; before that the bar shows no data."""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.calendar.schedule import occurrences
from app.models import DoseLog, DoseStatus, FoodLog, Protocol, ProtocolItem, User, WaterLog, WorkoutLog, WorkoutPlan

WINDOWS = (7, 14, 30, 90, 180, 360, 0)               # 0 is lifetime
DEFAULT_WINDOW = 30
WATER_TOLERANCE = 0.10
PROTEIN_SHARE = 0.90


def parse_window(raw) -> int:
    text = str(raw or "").strip().lower()
    if text == "lifetime":
        return 0
    try:
        value = int(text)
    except ValueError:
        return DEFAULT_WINDOW
    return value if value in WINDOWS else DEFAULT_WINDOW


def window_label(window: int) -> str:
    return "Lifetime" if window == 0 else f"{window} days"


def water_day_ok(oz: float, goal: float) -> bool:
    return bool(goal) and goal * (1 - WATER_TOLERANCE) - 1e-9 <= oz <= goal * (1 + WATER_TOLERANCE) + 1e-9


def diet_day_ok(calories: float, protein_g: float, *, calorie_limit: float, protein_target: float) -> bool:
    return calories > 0 and calories <= calorie_limit + 1e-9 and protein_g >= PROTEIN_SHARE * protein_target - 1e-9


def _bar(key: str, label: str, good: int, total: int, note: str = "") -> dict:
    return {"key": key, "label": label, "good": good, "total": total, "pct": round(100 * good / total) if total else None, "note": note}


def _start(today: date, window: int, first: date | None) -> date | None:
    """The first day counted: the window's start, or the first recorded day if that is later. None when nothing was ever recorded."""
    if first is None:
        return None
    since = today - timedelta(days=window - 1) if window else first
    return max(since, first)


def _days(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


# ---------------------------------------------------------------- protocol

def _protocol_bar(session: Session, uid: int, today: date, window: int) -> dict:
    protocols = session.scalars(select(Protocol).where(Protocol.owner_id == uid).options(
        selectinload(Protocol.items).selectinload(ProtocolItem.peptide), selectinload(Protocol.items).selectinload(ProtocolItem.steps),
        selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs), selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item))).all()
    first = min((p.start_date for p in protocols), default=None)
    since = _start(today, window, first)
    if since is None or since > today:
        return _bar("protocol", "Protocol", 0, 0, "No protocol doses due yet")
    done = {(log.protocol_item_id, log.scheduled_date) for log in session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date >= since, DoseLog.scheduled_date <= today,
                              DoseLog.status.in_((DoseStatus.ON_TIME, DoseStatus.LATE))))}
    due = {(item.protocol_item_id, occ.date) for occ in occurrences(protocols, since, today) for item in occ.items}
    due = {key for key in due if key[1] < today or key in done}            # a dose still pending today is not missed yet
    if not due:
        return _bar("protocol", "Protocol", 0, 0, "No protocol doses due yet")
    return _bar("protocol", "Protocol", len(due & done), len(due), "Doses logged out of doses due")


# ---------------------------------------------------------------- water

def _water_goal(session: Session, user: User) -> int | None:
    from app.measurements.calculations import water_goal_oz
    from app.workouts import logging as workout_logging
    weight = workout_logging.latest_body_weight(session, user.id)
    if user.water_goal_oz is None and weight is None:
        return None
    return water_goal_oz(weight or 0, user.water_goal_oz)


def _water_bar(session: Session, user: User, today: date, window: int) -> dict:
    first = session.scalar(select(func.min(WaterLog.logged_at)).where(WaterLog.owner_id == user.id))
    if first is None:
        return _bar("h2o", "H2O", 0, 0, "No water logged yet")
    goal = _water_goal(session, user)
    if not goal:
        return _bar("h2o", "H2O", 0, 0, "Add a weigh-in or set a water goal to compare against")
    since, last = _start(today, window, first), today - timedelta(days=1)
    if since > last:
        return _bar("h2o", "H2O", 0, 0, "Not enough completed days yet")
    totals = dict(session.execute(select(WaterLog.logged_at, func.sum(WaterLog.ounces)).where(
        WaterLog.owner_id == user.id, WaterLog.logged_at >= since, WaterLog.logged_at <= last).group_by(WaterLog.logged_at)).all())
    days = list(_days(since, last))
    return _bar("h2o", "H2O", sum(1 for d in days if water_day_ok(totals.get(d, 0) or 0, goal)), len(days), f"Days within 10% of your {goal} oz goal")


# ---------------------------------------------------------------- diet

def _diet_targets(session: Session, user: User, today: date) -> tuple[float, float] | None:
    """(calorie limit, protein target) from the Food tab's targets, or None when the profile cannot give them yet."""
    from app.food.summary import day_summary
    summary = day_summary(session, user, today)
    return None if summary["status"] != "ok" else (summary["targets"].calories, summary["targets"].protein_g)


def _diet_bar(session: Session, user: User, today: date, window: int) -> dict:
    first = session.scalar(select(func.min(FoodLog.eaten_on)).where(FoodLog.owner_id == user.id))
    if first is None:
        return _bar("diet", "Diet", 0, 0, "No food logged yet")
    targets = _diet_targets(session, user, today)
    if targets is None:
        return _bar("diet", "Diet", 0, 0, "Complete your profile and add a weigh-in (Food tab) to set diet targets")
    limit, protein = targets
    since, last = _start(today, window, first), today - timedelta(days=1)
    if since > last:
        return _bar("diet", "Diet", 0, 0, "Not enough completed days yet")
    sums = {d: (cal or 0, pro or 0) for d, cal, pro in session.execute(
        select(FoodLog.eaten_on, func.sum(FoodLog.calories), func.sum(FoodLog.protein_g)).where(
            FoodLog.owner_id == user.id, FoodLog.eaten_on >= since, FoodLog.eaten_on <= last).group_by(FoodLog.eaten_on)).all()}
    days = list(_days(since, last))
    good = sum(1 for d in days if diet_day_ok(*sums.get(d, (0, 0)), calorie_limit=limit, protein_target=protein))
    return _bar("diet", "Diet", good, len(days), f"Days within {round(limit)} kcal with at least {round(PROTEIN_SHARE * 100)}% of {round(protein)} g protein")


# ---------------------------------------------------------------- workouts

def _workout_bar(session: Session, uid: int, today: date, window: int) -> dict:
    from app.routers.workouts import scheduled_workout_dates
    plans = session.scalars(select(WorkoutPlan).where(WorkoutPlan.owner_id == uid, WorkoutPlan.ended_on.is_(None))).all()
    first = min((p.started_on for p in plans), default=None)
    since = _start(today, window, first)
    if since is None or since > today:
        return _bar("workout", "Workout", 0, 0, "No active workout plan")
    scheduled = scheduled_workout_dates(session, uid, since, today)
    logged = {d for d in session.scalars(select(WorkoutLog.log_date).where(WorkoutLog.owner_id == uid, WorkoutLog.log_date >= since,
                                                                           WorkoutLog.log_date <= today))}
    scheduled = {d for d in scheduled if d < today or d in logged}         # today's workout still to do is not missed yet
    if not scheduled:
        return _bar("workout", "Workout", 0, 0, "No workout days scheduled yet")
    return _bar("workout", "Workout", len(scheduled & logged), len(scheduled), "Scheduled workout days completed")


def bars(session: Session, user: User, today: date, window: int) -> list[dict]:
    return [_protocol_bar(session, user.id, today, window), _water_bar(session, user, today, window),
            _diet_bar(session, user, today, window), _workout_bar(session, user.id, today, window)]

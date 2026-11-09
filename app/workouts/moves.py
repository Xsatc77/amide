"""Moving one workout session to another day, for that session only.

A plan day repeats on its weekdays; a move says "this one time, the session due on `from_date` happens on `to_date`".
Everything that asks which days have a workout (Today, the Dashboard week, the Calendar, compliance) goes through
`due_by_date`, so a move shows everywhere and a session moved away is not counted as missed."""
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import WEEKDAY_LETTERS, WorkoutLog, WorkoutMove, WorkoutPlan, WorkoutPlanDay

MAX_DAYS_AHEAD = 14


def active_days(session: Session, uid: int) -> list[WorkoutPlanDay]:
    return list(session.scalars(
        select(WorkoutPlanDay).join(WorkoutPlan)
        .where(WorkoutPlan.owner_id == uid, WorkoutPlan.ended_on.is_(None), WorkoutPlanDay.weekdays.isnot(None))).all())


def due_by_date(session: Session, uid: int, start: date, end: date) -> dict[date, set[int]]:
    """Plan-day ids due on each date in [start, end] (dates with none are left out), moves applied."""
    days = active_days(session, uid)
    due: dict[date, set[int]] = {}
    d = start
    while d <= end:
        letter = WEEKDAY_LETTERS[d.weekday()]
        ids = {day.id for day in days if letter in day.weekdays}
        if ids:
            due[d] = ids
        d += timedelta(days=1)
    live = {day.id for day in days}
    moves = session.scalars(select(WorkoutMove).where(
        WorkoutMove.owner_id == uid, or_(WorkoutMove.from_date.between(start, end), WorkoutMove.to_date.between(start, end)))).all()
    for m in moves:
        if m.plan_day_id not in live:
            continue
        if start <= m.from_date <= end and m.plan_day_id in due.get(m.from_date, ()):
            due[m.from_date].discard(m.plan_day_id)
        if start <= m.to_date <= end:
            due.setdefault(m.to_date, set()).add(m.plan_day_id)
    return {k: v for k, v in due.items() if v}


def free_dates(session: Session, uid: int, today: date) -> list[date]:
    """The days after today (up to MAX_DAYS_AHEAD) with no workout due and none logged: where a session can be moved."""
    end = today + timedelta(days=MAX_DAYS_AHEAD)
    busy = set(due_by_date(session, uid, today, end))
    busy |= set(session.scalars(select(WorkoutLog.log_date).where(
        WorkoutLog.owner_id == uid, WorkoutLog.log_date > today, WorkoutLog.log_date <= end)).all())
    return [today + timedelta(days=n) for n in range(1, MAX_DAYS_AHEAD + 1) if today + timedelta(days=n) not in busy]


def move_session(session: Session, uid: int, day_id: int, from_date: date, to_date: date, today: date) -> str | None:
    """Move the session of `day_id` due on `from_date` to `to_date`. Returns the reason it cannot be done, else None."""
    if to_date not in free_dates(session, uid, today):
        return "Pick one of the days offered: a day after today, within two weeks, with no workout on it."
    if day_id not in due_by_date(session, uid, from_date, from_date).get(from_date, set()):
        return "That workout is not due on that day."
    arrived = session.scalar(select(WorkoutMove).where(
        WorkoutMove.owner_id == uid, WorkoutMove.plan_day_id == day_id, WorkoutMove.to_date == from_date))
    if arrived is not None:                    # moved here before: it still left its first day
        arrived.to_date = to_date
    else:
        session.add(WorkoutMove(owner_id=uid, plan_day_id=day_id, from_date=from_date, to_date=to_date))
    session.commit()
    return None

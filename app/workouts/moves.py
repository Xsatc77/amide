"""Moving or cancelling one workout session, for that session only.

A plan day repeats on its weekdays; a move says "this one time, the session due on `from_date` happens on `to_date`".
Everything that asks which days have a workout (Today, the Dashboard week, the Calendar, compliance) goes through
`due_by_date`, so a move shows everywhere and a session moved away is not counted as missed.

A session can only move within MAX_DAYS_AHEAD days of its original date: later than that it would train the same body groups twice
in a row, so it is not offered, and an undone session counts as missed. Cancelling a session marks it missed on the spot."""
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import WEEKDAY_LETTERS, WorkoutCancel, WorkoutLog, WorkoutMove, WorkoutPlan, WorkoutPlanDay

MAX_DAYS_AHEAD = 7


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


def origin_date(session: Session, uid: int, day_id: int, on_date: date) -> date:
    """The date this session was first due: `on_date`, or where it came from when it was already moved here."""
    arrived = session.scalar(select(WorkoutMove).where(
        WorkoutMove.owner_id == uid, WorkoutMove.plan_day_id == day_id, WorkoutMove.to_date == on_date))
    return arrived.from_date if arrived is not None else on_date


def free_dates(session: Session, uid: int, today: date, origin: date | None = None) -> list[date]:
    """The days after today, within MAX_DAYS_AHEAD days of the session's original date, with no workout due and none logged."""
    origin = origin or today
    end = origin + timedelta(days=MAX_DAYS_AHEAD)
    first = max(today, origin) + timedelta(days=1)
    if first > end:
        return []
    busy = set(due_by_date(session, uid, first, end))
    busy |= set(session.scalars(select(WorkoutLog.log_date).where(
        WorkoutLog.owner_id == uid, WorkoutLog.log_date >= first, WorkoutLog.log_date <= end)).all())
    return [first + timedelta(days=n) for n in range((end - first).days + 1) if first + timedelta(days=n) not in busy]


def today_is_free(session: Session, uid: int, today: date) -> bool:
    """No workout is due today (cancelled ones still count) and none is logged."""
    return today not in due_by_date(session, uid, today, today) and session.scalar(
        select(WorkoutLog.id).where(WorkoutLog.owner_id == uid, WorkoutLog.log_date == today).limit(1)) is None


def coming_on(session: Session, uid: int, day: date, today: date) -> list[WorkoutPlanDay]:
    """Sessions due on a later `day` that could be done today instead (within MAX_DAYS_AHEAD days of their original date)."""
    if day <= today:
        return []
    by_id = {d.id: d for d in active_days(session, uid)}
    ids = due_by_date(session, uid, day, day).get(day, set())
    return [by_id[i] for i in sorted(ids, key=lambda i: (by_id[i].plan_id, by_id[i].position))
            if today <= origin_date(session, uid, i, day) + timedelta(days=MAX_DAYS_AHEAD)]


def coming_up(session: Session, uid: int, today: date) -> list[tuple[date, WorkoutPlanDay]]:
    """Sessions due in the next MAX_DAYS_AHEAD days that could be done today instead; empty when today is taken."""
    if not today_is_free(session, uid, today):
        return []
    end = today + timedelta(days=MAX_DAYS_AHEAD)
    due = due_by_date(session, uid, today + timedelta(days=1), end)
    by_id = {d.id: d for d in active_days(session, uid)}
    out = []
    for d in sorted(due):
        for i in sorted(due[d], key=lambda i: (by_id[i].plan_id, by_id[i].position)):
            if today <= origin_date(session, uid, i, d) + timedelta(days=MAX_DAYS_AHEAD):
                out.append((d, by_id[i]))
    return out


def cancelled_ids(session: Session, uid: int, on_date: date) -> set[int]:
    return set(session.scalars(select(WorkoutCancel.plan_day_id).where(WorkoutCancel.owner_id == uid, WorkoutCancel.on_date == on_date)).all())


def cancelled_dates(session: Session, uid: int, start: date, end: date) -> set[date]:
    """Dates in [start, end] whose every due session was cancelled: those days are missed even before they are over."""
    due = due_by_date(session, uid, start, end)
    out = set()
    for d in {c.on_date for c in session.scalars(select(WorkoutCancel).where(
            WorkoutCancel.owner_id == uid, WorkoutCancel.on_date.between(start, end))).all()}:
        if d in due and due[d] <= cancelled_ids(session, uid, d):
            out.add(d)
    return out


def move_session(session: Session, uid: int, day_id: int, from_date: date, to_date: date, today: date) -> str | None:
    """Move the session of `day_id` due on `from_date` to `to_date`. Returns the reason it cannot be done, else None."""
    if day_id not in due_by_date(session, uid, from_date, from_date).get(from_date, set()):
        return "That workout is not due on that day."
    origin = origin_date(session, uid, day_id, from_date)
    if to_date == today and from_date > today:                       # "Do Today": pull a coming session onto today, even if today already has one
        if today > origin + timedelta(days=MAX_DAYS_AHEAD):
            return "That session is more than 7 days from its original day, so it can’t be done today."
    elif to_date not in free_dates(session, uid, today, origin):
        return (f"Pick one of the days offered: a day after today with no workout on it, within {MAX_DAYS_AHEAD} days of the original session "
                "(later than that it would train the same body groups twice).")
    arrived = session.scalar(select(WorkoutMove).where(
        WorkoutMove.owner_id == uid, WorkoutMove.plan_day_id == day_id, WorkoutMove.to_date == from_date))
    if arrived is not None:                    # moved here before: it still left its first day
        arrived.to_date = to_date
    else:
        session.add(WorkoutMove(owner_id=uid, plan_day_id=day_id, from_date=from_date, to_date=to_date))
    session.commit()
    return None


def cancel_session(session: Session, uid: int, day_id: int, on_date: date) -> str | None:
    """Mark the session of `day_id` due on `on_date` as missed. Returns the reason it cannot be done, else None."""
    if day_id not in due_by_date(session, uid, on_date, on_date).get(on_date, set()):
        return "That workout is not due on that day."
    if day_id not in cancelled_ids(session, uid, on_date):
        session.add(WorkoutCancel(owner_id=uid, plan_day_id=day_id, on_date=on_date))
        session.commit()
    return None

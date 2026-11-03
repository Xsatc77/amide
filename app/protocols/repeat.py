"""Short courses that come round again (the Annual Short Term Protocol goal): the dates of each run, rolling a finished protocol on to its next run,
and the calendar reminders that lead up to a run. Nothing here is stored for the reminders: they are worked out from the protocol, so they follow
every roll-forward by themselves."""

import calendar
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

REPEAT_CHOICES = ((12, "Annually"), (6, "Semiannually"), (3, "Quarterly"))
MAX_SHORT_DAYS = 30

# Days before a run starts, and what to say. Dropped when it would land inside the run before.
REMINDERS = ((90, "Check stock"), (60, "Order missing stock"), (30, "Stock check"), (14, "General reminder"), (7, "7 Day Water Fast"))


def add_months(day: date, months: int) -> date:
    """`day` moved on by whole months, kept in range (Jan 31 + 1 month is the last day of February)."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def run_dates(anchor: date, months: int, days: int, run: int) -> tuple[date, date]:
    """(first day, last day) of run number `run` (1 is the first). Every run is counted from the first start, so month ends never drift."""
    start = add_months(anchor, months * (run - 1))
    return start, start + timedelta(days=days - 1)


def joins_run(base_months: int | None, item_months: int | None, run: int) -> bool:
    """Whether an item on its own cadence takes part in run number `run` of a protocol that repeats every `base_months` (the shortest of its items'
    cadences). In a quarterly protocol a quarterly item is in every run, a semiannual one in every second, an annual one in every fourth."""
    if not base_months or not item_months or item_months <= base_months:
        return True
    return (run - 1) % (item_months // base_months) == 0


def item_in_run(p, item, run: int | None = None) -> bool:
    """Whether `item` of protocol `p` is part of the run `p` is on now (or run number `run`). Everything is, outside a repeating short course."""
    if not is_repeating(p):
        return True
    return joins_run(p.repeat_months, item.repeat_months, p.run_number if run is None else run)


def is_repeating(p) -> bool:
    return bool(getattr(p, "short_days", None) and p.repeat_months and p.anchor_start)


def _repeating(session: Session, uid: int, **conditions):
    from app.models import Protocol
    query = select(Protocol).where(Protocol.owner_id == uid, Protocol.repeat_months.is_not(None), Protocol.ended_on.is_(None))
    if conditions.get("running_only"):
        query = query.where(Protocol.paused.is_(False))
    return [p for p in session.scalars(query) if is_repeating(p)]


def roll_forward(session: Session, uid: int, today: date) -> int:
    """Moves every finished repeating protocol of this person on to the run that is current or next (a protocol nobody opened for a year skips the
    runs in between). Paused and ended protocols are left alone. Returns how many were moved."""
    moved = 0
    for p in _repeating(session, uid, running_only=True):
        if p.end_date is None or p.end_date >= today:
            continue
        run = p.run_number
        while True:
            run += 1
            start, end = run_dates(p.anchor_start, p.repeat_months, p.short_days, run)
            if end >= today:
                break
        p.run_number, p.start_date, p.end_date = run, start, end
        moved += 1
    if moved:
        session.commit()
    return moved


def calendar_dates(session: Session, uid: int, first: date, last: date) -> dict[date, list[str]]:
    """Days in [first, last] to mark on the calendar: the countdown to this run and to the next one, each as "Protocol name: Check stock (90 days to
    go)". A paused or ended protocol has none."""
    marks: dict[date, list[str]] = {}
    for p in _repeating(session, uid, running_only=True):
        for run in (p.run_number, p.run_number + 1):
            start, _ = run_dates(p.anchor_start, p.repeat_months, p.short_days, run)
            before_end = run_dates(p.anchor_start, p.repeat_months, p.short_days, run - 1)[1] if run > 1 else None
            names = ", ".join(i.peptide.name for i in p.items if joins_run(p.repeat_months, i.repeat_months, run))
            for offset, text in REMINDERS:
                day = start - timedelta(days=offset)
                if before_end is not None and day <= before_end:
                    continue
                if first <= day <= last:
                    marks.setdefault(day, []).append(f"{p.name}: {text} ({offset} days to go)" + (f" for {names}" if names else ""))
    return marks

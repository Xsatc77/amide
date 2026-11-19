"""Short courses that come round again (the Annual Short Term Protocol goal): the dates of each run, rolling a finished protocol on to its next run,
and the calendar reminders that lead up to a run. Nothing here is stored for the reminders: they are worked out from the protocol, so they follow
every roll-forward by themselves."""

import calendar
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

REPEAT_CHOICES = ((12, "Annually"), (6, "Semiannually"), (4, "Triannually"), (3, "Quarterly"))
MAX_SHORT_DAYS = 90                   # a quarter: three 30-day months

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


def joins_run(base_months: int | None, item_months: int | None, run: int, first_run: int = 1) -> bool:
    """Whether an item on its own cadence takes part in run number `run` of a protocol that repeats every `base_months` (the shortest of its items'
    cadences). In a quarterly protocol a quarterly item is in every run, a semiannual one in every second, an annual one in every fourth.
    `first_run` is the run it starts in: a semiannual item that starts in run 2 is in runs 2, 4, 6."""
    if not base_months or not item_months or item_months <= base_months:
        return True
    return run >= first_run and (run - first_run) % (item_months // base_months) == 0


MONTH_DAYS = 30                       # a month of a short course is a 30-day block


def first_run_span(base_months: int | None, item_months: int | None) -> int:
    """How many choices an item has for where it first joins in: its runs when its cadence is a whole number of runs, else its months."""
    if not base_months or not item_months or item_months <= base_months:
        return 1
    return item_months // base_months if item_months % base_months == 0 else item_months


def item_offset(base_months: int | None, item_months: int | None, run: int, first_run: int = 1) -> int | None:
    """None when the item is not in run number `run`; else the days to add to its start day. An item whose cadence is a whole number of runs (a semiannual
    one in a quarterly course) is in some runs and starts at its own start day. One that is not (a triannual one in a quarterly course: months 2, 6, 10)
    lands in whichever month of a run its own months fall, so its start day is counted from the start of that month and `first_run` is its first month."""
    if not base_months or not item_months or item_months <= base_months or item_months % base_months == 0:
        return 0 if joins_run(base_months, item_months, run, first_run) else None
    month = base_months * (run - 1)
    for m in range(month, month + base_months):
        if m >= first_run - 1 and (m - (first_run - 1)) % item_months == 0:
            return (m - month) * MONTH_DAYS
    return None


def item_in_run(p, item, run: int | None = None) -> bool:
    """Whether `item` of protocol `p` is part of the run `p` is on now (or run number `run`). Everything is, outside a repeating short course."""
    return item_shift(p, item, run) is not None


def item_shift(p, item, run: int | None = None) -> int | None:
    """The days an item's own days are moved on within this run (0 for most items), or None when it is not in the run."""
    if not is_repeating(p):
        return 0
    return item_offset(p.repeat_months, item.repeat_months, p.run_number if run is None else run, getattr(item, "first_run", 1) or 1)


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


class Mark(str):
    """A calendar mark's text, which also knows the peptides it names (id, name) so the day view can link each to its library card."""
    head: str
    peptides: list

    def __new__(cls, head: str, peptides: list):
        mark = super().__new__(cls, head + (" for " + ", ".join(name for _, name in peptides) if peptides else ""))
        mark.head, mark.peptides = head, peptides
        return mark


def calendar_dates(session: Session, uid: int, first: date, last: date) -> dict[date, list[str]]:
    """Days in [first, last] to mark on the calendar: the countdown to this run and to the next one, each as "Protocol name: Check stock (90 days to
    go)". A paused or ended protocol has none."""
    marks: dict[date, list[str]] = {}
    for p in _repeating(session, uid, running_only=True):
        for run in (p.run_number, p.run_number + 1):
            start, _ = run_dates(p.anchor_start, p.repeat_months, p.short_days, run)
            before_end = run_dates(p.anchor_start, p.repeat_months, p.short_days, run - 1)[1] if run > 1 else None
            named = [(i.peptide.id, i.peptide.name) for i in p.items if item_offset(p.repeat_months, i.repeat_months, run, i.first_run or 1) is not None]
            for offset, text in REMINDERS:
                day = start - timedelta(days=offset)
                if before_end is not None and day <= before_end:
                    continue
                if first <= day <= last:
                    marks.setdefault(day, []).append(Mark(f"{p.name}: {text} ({offset} days to go)", named))
    return marks

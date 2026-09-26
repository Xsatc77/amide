"""Which doses fall on which dates. Pure functions over protocol objects (no database access).

Weeks run Sunday–Saturday. Week numbers follow the US convention: week 1 is the week containing Jan 1.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.models import WEEKDAY_LETTERS, Frequency, TimeOfDay
from app.protocols.status import current_step, current_week


@dataclass
class DueItem:
    peptide: str
    peptide_id: int
    protocol_item_id: int
    dose: float | None
    unit: str
    step: int | None  # titration step number, when one applies
    time_of_day: TimeOfDay
    route: str
    inventory: str | None


@dataclass
class Occurrence:
    """One protocol on one date, with everything due from it that day."""
    date: date
    protocol_id: int
    protocol_name: str
    items: list[DueItem] = field(default_factory=list)


def protocol_window(p) -> tuple[date, date | None] | None:
    """(first, last) dates a protocol can have doses; None while paused. Last is None when ongoing."""
    if p.paused:
        return None
    ends = [d for d in (p.end_date, p.ended_on) if d is not None]
    return p.start_date, (min(ends) if ends else None)


def is_due(item, start: date, day: date) -> bool:
    days = (day - start).days
    if days < 0:
        return False
    freq = item.frequency
    if freq is Frequency.DAILY:
        return True
    if freq is Frequency.EOD:
        return days % 2 == 0
    if freq is Frequency.EVERY_N_DAYS:
        return bool(item.every_n_days) and days % item.every_n_days == 0
    if freq is Frequency.WEEKDAYS:
        return WEEKDAY_LETTERS[day.weekday()] in (item.weekdays or "")
    if freq is Frequency.WEEKLY:
        return days % 7 == 0
    return False  # as needed: never scheduled


def _due_item(p, item, day: date) -> DueItem:
    dose, step_no = item.dose, None
    if p.titration_enabled and item.steps:
        step = current_step(item.steps, current_week(p.start_date, day))
        if step is not None:
            dose, step_no = step.dose, item.steps.index(step) + 1
    return DueItem(
        peptide=item.peptide.name, peptide_id=item.peptide_id, protocol_item_id=item.id, dose=dose,
        unit=item.dose_unit.value, step=step_no,
        time_of_day=item.time_of_day, route=item.route.label if hasattr(item.route, "label") else str(item.route),
        inventory=item.inventory_item.name if item.inventory_item else None,
    )


def _days(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def occurrences(protocols, first: date, last: date) -> list[Occurrence]:
    """Every protocol-date with something due between first and last (inclusive), by date then name."""
    out: list[Occurrence] = []
    for p in protocols:
        window = protocol_window(p)
        if window is None:
            continue
        start, end = window
        lo, hi = max(first, start), min(last, end) if end else last
        for day in _days(lo, hi):
            items = [_due_item(p, it, day) for it in p.items if is_due(it, p.start_date, day)]
            if items:
                out.append(Occurrence(day, p.id, p.name, items))
    return sorted(out, key=lambda o: (o.date, o.protocol_name.lower(), o.protocol_id))


def as_needed(protocols, day: date) -> list[tuple[object, list[DueItem]]]:
    """(protocol, as-needed items) for protocols running on `day`."""
    out = []
    for p in protocols:
        window = protocol_window(p)
        if window is None or day < window[0] or (window[1] and day > window[1]):
            continue
        items = [_due_item(p, it, day) for it in p.items if it.frequency is Frequency.AS_NEEDED]
        if items:
            out.append((p, items))
    return out


def week_start(day: date) -> date:
    """The Sunday on or before `day`."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def week_number(day: date) -> int:
    """US week number: weeks start Sunday; week 1 contains Jan 1 (even when it began in December)."""
    sunday = week_start(day)
    saturday = sunday + timedelta(days=6)
    year = saturday.year  # a week that reaches into January belongs to the new year
    week1_start = week_start(date(year, 1, 1))
    return (sunday - week1_start).days // 7 + 1


def missed_items(occs: list[Occurrence], logged: set[tuple[int, date]], today: date) -> list[tuple[Occurrence, DueItem]]:
    """Every (occurrence, item) pair whose scheduled date has fully passed with nothing logged for
    it. `logged` is the set of (protocol_item_id, scheduled_date) pairs that already have a DoseLog
    row (any status) -- callers build this from the database; this function itself never touches
    one."""
    out: list[tuple[Occurrence, DueItem]] = []
    for occ in occs:
        if occ.date >= today:
            continue
        for item in occ.items:
            if (item.protocol_item_id, occ.date) not in logged:
                out.append((occ, item))
    return out

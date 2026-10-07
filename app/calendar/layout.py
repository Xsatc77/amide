"""Month-grid layout: which weeks to show and, for each day, the individual dose-item marks to
draw (capped per day, with an overflow count past the cap) -- no cross-day spanning. Each mark is
one due item on one day, sorted by time of day so the same kind of thing (AM doses, then PM, etc.)
always lands in the same relative position day to day, without needing to remember a stable lane
per protocol the way the old spanning-bar layout did."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.calendar.schedule import DueItem, Occurrence, week_number, week_start
from app.models import TimeOfDay

_SLOT_ORDER = {m: n for n, m in enumerate(TimeOfDay)}


def initials(name: str) -> str:
    """A short, stable badge for a protocol name -- the first letter of each of its first 3 words,
    or the first 2 letters when it's a single word (e.g. "Weight Loss" -> "WL", "Testosterone" ->
    "TE"). Purely a legibility aid alongside the protocol's own color; two protocols can collide on
    the same initials, the color plus a click-through still disambiguates them."""
    words = name.split()
    if len(words) == 1:
        return name[:2].upper()
    return "".join(w[0] for w in words[:3]).upper()


@dataclass
class Mark:
    """One due dose item, on one day."""
    protocol_id: int
    protocol_name: str
    initials: str
    color: int
    protocol_item_id: int
    date: date


@dataclass
class MonthRow:
    week_start: date
    week_no: int
    days: list[date]
    marks: dict[date, list[Mark]] = field(default_factory=dict)  # capped at max_marks per day
    overflow: dict[date, int] = field(default_factory=dict)  # marks beyond the cap, per day


def month_weeks(anchor: date) -> list[list[date]]:
    """Weeks (Sunday first) covering the whole month of `anchor`."""
    first = anchor.replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    day, weeks = week_start(first), []
    while day <= last:
        weeks.append([day + timedelta(days=i) for i in range(7)])
        day += timedelta(days=7)
    return weeks


def _day_marks(occs: list[Occurrence], colors: dict[int, int]) -> dict[date, list[Mark]]:
    """Every due item, grouped by date and sorted by time of day (then protocol/item id for a
    stable order among items in the same slot)."""
    entries: dict[date, list[tuple[Occurrence, DueItem]]] = {}
    for o in occs:
        for it in o.items:
            entries.setdefault(o.date, []).append((o, it))
    by_date = {}
    for day, pairs in entries.items():
        pairs.sort(key=lambda pair: (
            _SLOT_ORDER.get(pair[1].time_of_day, len(_SLOT_ORDER)), pair[0].protocol_id, pair[1].protocol_item_id))
        by_date[day] = [
            Mark(o.protocol_id, o.protocol_name, initials(o.protocol_name), colors.get(o.protocol_id, 0),
                 it.protocol_item_id, day)
            for o, it in pairs
        ]
    return by_date


def month_rows(weeks: list[list[date]], occs: list[Occurrence], colors: dict[int, int],
               max_marks: int = 4) -> list[MonthRow]:
    """One row per week; each day gets up to `max_marks` marks (sorted by time of day) plus an
    overflow count for anything past the cap."""
    by_date = _day_marks(occs, colors)
    rows = []
    for week in weeks:
        row = MonthRow(week_start=week[0], week_no=week_number(week[0]), days=week)
        for day in week:
            marks = by_date.get(day, [])
            row.marks[day] = marks[:max_marks]
            if len(marks) > max_marks:
                row.overflow[day] = len(marks) - max_marks
        rows.append(row)
    return rows

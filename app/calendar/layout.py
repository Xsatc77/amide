"""Month-grid layout: which weeks to show and where each protocol's bars go."""

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.calendar.schedule import Occurrence, week_number, week_start


@dataclass
class Bar:
    """Consecutive due days of one protocol inside one week row."""
    protocol_id: int
    name: str
    color: int
    lane: int
    col_start: int  # 0 = Sunday
    col_end: int
    dates: list[date] = field(default_factory=list)


@dataclass
class MonthRow:
    week_start: date
    week_no: int
    days: list[date]
    bars: list[Bar] = field(default_factory=list)
    more: dict[int, int] = field(default_factory=dict)  # column -> protocols not drawn


def month_weeks(anchor: date) -> list[list[date]]:
    """Weeks (Sunday first) covering the whole month of `anchor`."""
    first = anchor.replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    day, weeks = week_start(first), []
    while day <= last:
        weeks.append([day + timedelta(days=i) for i in range(7)])
        day += timedelta(days=7)
    return weeks


def month_rows(weeks: list[list[date]], occs: list[Occurrence], colors: dict[int, int],
               max_lanes: int = 4) -> list[MonthRow]:
    """Each protocol keeps one lane for the month (in order of first appearance); lanes past
    `max_lanes` aren't drawn and are counted in `more` instead."""
    lanes: dict[int, int] = {}
    for o in occs:
        lanes.setdefault(o.protocol_id, len(lanes))
    names = {o.protocol_id: o.protocol_name for o in occs}
    due = {(o.protocol_id, o.date) for o in occs}

    rows = []
    for week in weeks:
        row = MonthRow(week_start=week[0], week_no=week_number(week[0]), days=week)
        for pid, lane in lanes.items():
            cols = [c for c, day in enumerate(week) if (pid, day) in due]
            if not cols:
                continue
            if lane >= max_lanes:
                for c in cols:
                    row.more[c] = row.more.get(c, 0) + 1
                continue
            run = [cols[0]]
            for c in cols[1:] + [None]:
                if c is not None and c == run[-1] + 1:
                    run.append(c)
                    continue
                row.bars.append(Bar(pid, names[pid], colors.get(pid, 0), lane, run[0], run[-1],
                                    [week[i] for i in run]))
                if c is not None:
                    run = [c]
        row.bars.sort(key=lambda b: (b.lane, b.col_start))
        rows.append(row)
    return rows

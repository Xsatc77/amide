"""The year chart of a Protocol Portfolio: for each peptide of a protocol, the months of the next twelve it is taken in.

The window is twelve whole months from the month the protocol's current run starts. A short course that comes round again shows every run that
begins inside it, each peptide only in the runs it joins; any other protocol shows its peptides from their own first dose to their last (or to the
protocol's end, or off the right edge when nothing ends them)."""

from datetime import date, timedelta

from app.calendar.schedule import item_edges
from app.protocols.repeat import add_months, is_repeating, item_offset, run_dates


def month_start(day: date) -> date:
    return day.replace(day=1)


def _spans_repeating(p, item, first: date, stop: date) -> list[tuple[date, date | None]]:
    spans = []
    for run in range(max(1, p.run_number - 1), p.run_number + 60):  # the run before the current one can still be running at the window's start
        run_start, run_end = run_dates(p.anchor_start, p.repeat_months, p.short_days, run)
        if run_start >= stop:
            break
        offset = item_offset(p.repeat_months, item.repeat_months, run, getattr(item, "first_run", 1) or 1)
        if offset is None:
            continue
        begin = run_start + timedelta(days=(item.start_day or 1) - 1 + offset)
        end = begin + timedelta(days=item.run_days - 1) if item.run_days else run_end
        if end >= first and begin < stop:
            spans.append((begin, end))
    return spans


def year_chart(p, today: date) -> dict | None:
    """{"months": [first day of each of 12 months], "rows": [{"name", "spans": [(first, last or None)]}]}, or None when the protocol has no items."""
    if not p.items:
        return None
    anchor = p.start_date or today
    first = month_start(anchor)
    stop = add_months(first, 12)
    edges = item_edges(p)
    rows, seen = [], {}
    for it in p.items:
        if is_repeating(p):
            spans = _spans_repeating(p, it, first, stop)
        else:
            begin, end = edges.get(it.id, (p.start_date, p.end_date))
            begin = begin or p.start_date
            spans = [(begin, end if end is not None else p.end_date)] if begin else []
            spans = [(b, e) for b, e in spans if (e is None or e >= first) and b < stop]
        name = it.peptide.name
        if name in seen:                                              # one peptide on two lines of the protocol: one row, all its spans
            seen[name]["spans"] += spans
            continue
        seen[name] = {"name": name, "spans": spans}
        rows.append(seen[name])
    return {"months": [add_months(first, i) for i in range(12)], "rows": rows}

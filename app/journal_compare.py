"""Journal, Compare: the four 1-5 ratings looked at one day of the week at a time, across the whole history. Pure functions over journal entries."""

from datetime import date, timedelta

RATINGS = ("mood", "energy", "sleep_quality", "appetite")
WEEKDAYS = (("mon", "Monday"), ("tue", "Tuesday"), ("wed", "Wednesday"), ("thu", "Thursday"), ("fri", "Friday"), ("sat", "Saturday"), ("sun", "Sunday"))
RANGES = (("all", "All History", None), ("1yr", "Last Year", 365), ("6mo", "Last 6 Months", 182), ("3mo", "Last 3 Months", 91))


def parse_weekday(raw, default: int) -> int:
    """0 (Monday) to 6 (Sunday) from "thu", "Thursday" or "3"; `default` for anything else."""
    text = (str(raw) if raw is not None else "").strip().lower()
    for number, (short, name) in enumerate(WEEKDAYS):
        if text in (short, name.lower(), str(number)):
            return number
    return default


def parse_range(raw) -> str:
    return raw if raw in {key for key, _, _ in RANGES} else "all"


def range_label(key: str) -> str:
    return next(label for k, label, _ in RANGES if k == key)


def entries_since(entries, today: date, key: str):
    days = next(d for k, _, d in RANGES if k == key)
    return list(entries) if days is None else [e for e in entries if e.entry_date >= today - timedelta(days=days)]


def _values(entries, rating, weekday=None) -> list[int]:
    return [getattr(e, rating) for e in entries if getattr(e, rating) is not None and (weekday is None or e.entry_date.weekday() == weekday)]


def weekday_points(entries, weekday: int) -> dict[str, list[tuple[date, float]]]:
    """For each rating, (day, value) for just the entries that fall on `weekday`."""
    return {r: sorted((e.entry_date, float(getattr(e, r))) for e in entries
                      if e.entry_date.weekday() == weekday and getattr(e, r) is not None) for r in RATINGS}


def weekday_stats(entries, weekday: int) -> list[dict]:
    """Per rating: how many entries that weekday has, their average, lowest and highest, the average over every day, and the difference between them."""
    out = []
    for r in RATINGS:
        mine, everyone = _values(entries, r, weekday), _values(entries, r)
        avg = sum(mine) / len(mine) if mine else None
        all_avg = sum(everyone) / len(everyone) if everyone else None
        out.append({"key": r, "count": len(mine), "avg": avg, "low": min(mine) if mine else None, "high": max(mine) if mine else None,
                    "all_avg": all_avg, "diff": avg - all_avg if avg is not None and all_avg is not None else None})
    return out


def weekday_averages(entries) -> dict[str, list[float | None]]:
    """For each rating, the average for each of the seven weekdays (Monday first); None where that weekday has no rating."""
    out = {}
    for r in RATINGS:
        row = []
        for weekday in range(7):
            values = _values(entries, r, weekday)
            row.append(sum(values) / len(values) if values else None)
        out[r] = row
    return out

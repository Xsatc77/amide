"""Aggregations over a person's logged exercises for the Progress tab: a single exercise's history, personal
records, weekly volume by body area, where the burn comes from, and the daily burn for the TDEE chart.

Pure functions over plain `LoggedExercise` rows (the router builds them from the stored snapshots), so changing a
plan or the exercise database never alters what these report."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

RANGES = {"30": 30, "90": 90, "365": 365, "all": None}


@dataclass(frozen=True)
class LoggedExercise:
    log_date: date
    name: str                    # the canonical exercise when matched, else the name as logged
    area: str | None
    equipment: str | None
    load_lb: float | None
    sets: int | None
    reps: int | None
    volume_lb: float | None
    net_kcal: float | None
    gross_kcal: float | None


@dataclass(frozen=True)
class SessionPoint:
    log_date: date
    top_load_lb: float | None
    volume_lb: float
    net_kcal: float


@dataclass(frozen=True)
class Record:
    name: str
    heaviest: tuple[float, date] | None
    most_reps: tuple[int, date] | None
    biggest_volume: tuple[float, date] | None


def range_start(key: str, today: date) -> date | None:
    days = RANGES.get(key, RANGES["90"])
    return today - timedelta(days=days - 1) if days else None


def in_range(rows: list[LoggedExercise], start: date | None, end: date) -> list[LoggedExercise]:
    return [r for r in rows if r.log_date <= end and (start is None or r.log_date >= start)]


def exercises_by_recency(rows: list[LoggedExercise]) -> list[str]:
    """Every exercise name logged, most recently used first (then A-Z)."""
    last: dict[str, date] = {}
    for r in rows:
        if r.name not in last or r.log_date > last[r.name]:
            last[r.name] = r.log_date
    return [n for n, _ in sorted(last.items(), key=lambda kv: (-kv[1].toordinal(), kv[0].casefold()))]


def exercise_history(rows: list[LoggedExercise], name: str) -> list[SessionPoint]:
    """One point per day this exercise was logged: the heaviest load, the total volume and the net kcal."""
    by_day: dict[date, list[LoggedExercise]] = defaultdict(list)
    for r in rows:
        if r.name == name:
            by_day[r.log_date].append(r)
    points = []
    for day in sorted(by_day):
        day_rows = by_day[day]
        loads = [r.load_lb for r in day_rows if r.load_lb]
        points.append(SessionPoint(day, max(loads) if loads else None,
                                   sum(r.volume_lb or 0 for r in day_rows), sum(r.net_kcal or 0 for r in day_rows)))
    return points


def personal_records(rows: list[LoggedExercise]) -> list[Record]:
    """Heaviest load, most reps in a set and biggest single-day volume per exercise, with the date each happened;
    ordered by name."""
    best: dict[str, dict] = defaultdict(dict)
    day_volume: dict[tuple[str, date], float] = defaultdict(float)
    for r in rows:
        slot = best[r.name]
        if r.load_lb and r.load_lb > slot.get("load", (0, None))[0]:
            slot["load"] = (r.load_lb, r.log_date)
        if r.reps and r.reps > slot.get("reps", (0, None))[0]:
            slot["reps"] = (r.reps, r.log_date)
        day_volume[(r.name, r.log_date)] += r.volume_lb or 0
    for (name, day), volume in day_volume.items():
        if volume > best[name].get("volume", (0, None))[0]:
            best[name]["volume"] = (volume, day)
    return [Record(n, s.get("load"), s.get("reps"), s.get("volume")) for n, s in sorted(best.items(), key=lambda kv: kv[0].casefold())]


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_volume_by_area(rows: list[LoggedExercise]) -> tuple[list[date], dict[str, list[float]]]:
    """Every Monday from the first logged week to the last (empty weeks included) and, per body area, that week's
    volume. Rows with no area or no volume are skipped."""
    usable = [r for r in rows if r.volume_lb and r.area]
    if not usable:
        return [], {}
    first, last = week_start(min(r.log_date for r in usable)), week_start(max(r.log_date for r in usable))
    weeks = [first + timedelta(weeks=i) for i in range((last - first).days // 7 + 1)]
    index = {w: i for i, w in enumerate(weeks)}
    by_area: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(weeks))
    for r in usable:
        by_area[r.area][index[week_start(r.log_date)]] += r.volume_lb
    totals = {area: sum(v) for area, v in by_area.items()}
    return weeks, {area: by_area[area] for area in sorted(by_area, key=lambda a: (-totals[a], a))}


def burn_by_equipment(rows: list[LoggedExercise]) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.net_kcal:
            totals[r.equipment or "Other"] += r.net_kcal
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def top_exercises_by_kcal(rows: list[LoggedExercise], limit: int = 10) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.net_kcal:
            totals[r.name] += r.net_kcal
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:limit]


def daily_net_kcal(rows: list[LoggedExercise]) -> dict[date, float]:
    totals: dict[date, float] = defaultdict(float)
    for r in rows:
        if r.net_kcal:
            totals[r.log_date] += r.net_kcal
    return dict(totals)

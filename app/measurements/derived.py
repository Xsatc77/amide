"""Metrics worked out from numbers a person already logged, so they cost no extra typing: the weight trend, fat and lean mass, waist
ratios, the days a dose started or changed, and ratios between lab results. Pure functions, no database and no web."""

from collections import defaultdict
from datetime import date, timedelta

_MIN_GAP, _MAX_GAP = 7, 10          # the weekly rate compares with an average 7 to 10 days earlier; anything else has no honest weekly rate


def rolling_average(points: list[tuple[date, float]], window: int = 7) -> list[tuple[date, float]]:
    """The mean weight over the `window` days ending on each weigh-in day (that day and the window - 1 before it). Several weigh-ins on
    one day count as that day's mean, so a second reading does not pull the average toward itself."""
    by_day: dict[date, list[float]] = defaultdict(list)
    for day, value in points:
        by_day[day].append(value)
    daily = {day: sum(v) / len(v) for day, v in by_day.items()}
    out = []
    for day in sorted(daily):
        inside = [value for d, value in daily.items() if 0 <= (day - d).days < window]
        out.append((day, sum(inside) / len(inside)))
    return out


def weekly_rate(averages: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Change per week in the average: each day's average against the latest earlier average that is 7 to 10 days back, scaled to a week.
    A day with nothing that far back (or only something much older) has no rate rather than a made-up one."""
    out = []
    ordered = sorted(averages)
    for i, (day, value) in enumerate(ordered):
        earlier = [(d, v) for d, v in ordered[:i] if _MIN_GAP <= (day - d).days <= _MAX_GAP]
        if earlier:
            prev_day, prev = max(earlier)
            out.append((day, round((value - prev) / (day - prev_day).days * 7, 2)))
    return out


def composition(weight_lb: float | None, body_fat_pct: float | None) -> tuple[float, float] | None:
    """(fat pounds, lean pounds): the weight split by an estimated body-fat percentage. None when either number is missing or the
    percentage is not between 0 and 100."""
    if not weight_lb or body_fat_pct is None or not 0 < body_fat_pct < 100:
        return None
    fat = round(weight_lb * body_fat_pct / 100, 1)
    return fat, round(weight_lb - fat, 1)


def _ratio(a, b) -> float | None:
    return round(a / b, 3) if a and b else None


def waist_to_height(waist_in: float | None, height_in: float | None) -> float | None:
    return _ratio(waist_in, height_in)


def waist_to_hip(waist_in: float | None, hips_in: float | None) -> float | None:
    return _ratio(waist_in, hips_in)


def _dose_text(value: float, unit: str | None) -> str:
    return f"{value:g} {unit}".strip() if unit else f"{value:g}"


def dose_markers(doses: list[tuple[date, str, float | None, str | None]]) -> list[dict]:
    """One marker for the first dose of each peptide and one for each later change of its dose, from (day, peptide, dose, unit) rows."""
    last: dict[str, float] = {}
    out = []
    for day, name, value, unit in sorted((r for r in doses if r[2] is not None), key=lambda r: (r[0], r[1])):
        if name not in last:
            out.append({"date": day, "label": f"{name} {_dose_text(value, unit)} (start)"})
        elif value != last[name]:
            out.append({"date": day, "label": f"{name} {_dose_text(value, unit)}"})
        last[name] = value
    return out


def homa_ir(insulin: float | None, glucose: float | None) -> float | None:
    """Insulin resistance index from fasting insulin (uIU/mL) and fasting glucose (mg/dL): insulin x glucose / 405."""
    return round(insulin * glucose / 405, 2) if insulin and glucose else None


def tg_hdl(triglycerides: float | None, hdl: float | None) -> float | None:
    return _ratio(triglycerides, hdl) and round(triglycerides / hdl, 2)


def non_hdl(total_cholesterol: float | None, hdl: float | None) -> float | None:
    return round(total_cholesterol - hdl) if total_cholesterol is not None and hdl is not None else None


def ldl_hdl(ldl: float | None, hdl: float | None) -> float | None:
    return _ratio(ldl, hdl) and round(ldl / hdl, 2)

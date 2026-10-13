"""The charts on the Measurements page that are worked out from other entries: the weight trend, fat and lean mass, waist ratios, workout
totals, and calories eaten against the deficit. Each is a dict {key, label, kind, chart[, a_label, b_label]} that the page lists in the
Overview dropdown and in the grid below it. `chart` is None when there is not enough data, and the page leaves that chart out.

The two chart builders are passed in (they live in the router module, which imports this one)."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.food import day_overview
from app.food.targets import deficit
from app.measurements import derived
from app.measurements.calculations import body_fat_pct
from app.models import DoseLog, DoseStatus, FoodLog, User

PAD_X, PAD_LEFT = 28, 46            # the chart builders' plot margins, needed to place a marker on the date axis


def attach_markers(chart: dict | None, markers: list[dict]) -> dict | None:
    """Put a marker on a line chart for each dose start or change that falls inside the chart's dates."""
    if chart is None or not markers:
        return chart
    span = (chart["max_d"] - chart["min_d"]).days or 1
    chart["markers"] = [{"x": round(PAD_LEFT + (m["date"] - chart["min_d"]).days / span * (chart["width"] - PAD_LEFT - PAD_X), 1),
                         "date": m["date"], "label": m["label"]}
                        for m in markers if chart["min_d"] <= m["date"] <= chart["max_d"]]
    return chart


def dose_markers_for(session: Session, uid: int) -> list[dict]:
    rows = session.execute(select(DoseLog.scheduled_date, DoseLog.peptide_name, DoseLog.dose_value, DoseLog.dose_unit).where(
        DoseLog.owner_id == uid, DoseLog.status.in_((DoseStatus.ON_TIME, DoseStatus.LATE)))).all()
    return derived.dose_markers([(d, name, value, unit.value if unit is not None else None) for d, name, value, unit in rows])


def _line(key, label, chart_fn, points, markers=()):
    return {"key": key, "label": label, "kind": "line", "chart": attach_markers(chart_fn(points), list(markers))}


def _after(points, start):
    return [(d, v) for d, v in points if start is None or d >= start]


def extra_series(session: Session, user: User | None, own_entries: list, window_start: date | None, u, chart_fn, dual_fn,
                 markers: list[dict]) -> list[dict]:
    """`own_entries` is the person's whole measurement history (the 7-day average needs the days before the window's start);
    `window_start` trims what is drawn."""
    if user is None:
        return []
    wl, per = u.weight_label, "per week"

    weights = [(r.measured_at, r.weight_lbs) for r in own_entries if r.weight_lbs]
    averages = derived.rolling_average(weights, 7)
    shown_avg = [(d, u.weight(v)) for d, v in _after(averages, window_start)]
    rates = [(d, u.weight(v)) for d, v in _after(derived.weekly_rate(averages), window_start)]
    out = [_line("weight_avg7", f"Weight, 7-day average ({wl})", chart_fn, shown_avg, markers),
           _line("weight_rate", f"Weight change ({wl} {per})", chart_fn, rates, markers)]

    fat_points, lean_points = [], []
    if user.height_in is not None and user.sex is not None:
        for r in own_entries:
            if r.weight_lbs and r.neck_in and r.waist_in:
                parts = derived.composition(r.weight_lbs, body_fat_pct(user.sex, user.height_in, r.neck_in, r.waist_in, r.hips_in))
                if parts:
                    fat_points.append((r.measured_at, u.weight(parts[0])))
                    lean_points.append((r.measured_at, u.weight(parts[1])))
    out += [_line("fat_mass", f"Fat mass, estimated ({wl})", chart_fn, _after(fat_points, window_start), markers),
            _line("lean_mass", f"Lean mass, estimated ({wl})", chart_fn, _after(lean_points, window_start), markers)]

    whtr = [(r.measured_at, derived.waist_to_height(r.waist_in, user.height_in)) for r in own_entries]
    whr = [(r.measured_at, derived.waist_to_hip(r.waist_in, r.hips_in)) for r in own_entries]
    out += [_line("whtr", "Waist-to-height ratio", chart_fn, _after([p for p in whtr if p[1] is not None], window_start), markers),
            _line("whr", "Waist-to-hip ratio", chart_fn, _after([p for p in whr if p[1] is not None], window_start), markers)]

    out += workout_series(session, user.id, window_start, u, chart_fn)
    out.append(calories_series(session, user, window_start, dual_fn))
    return out


def workout_series(session: Session, uid: int, window_start: date | None, u, chart_fn) -> list[dict]:
    totals = day_overview.workout_totals(session, uid)
    cardio = [(d, round(t["cardio_min"], 1)) for d, t in totals.items() if t["cardio_min"] > 0]
    lifted = [(d, u.weight(round(t["lifted_lb"]), 0)) for d, t in totals.items() if t["lifted_lb"] > 0]
    return [_line("cardio_min", "Cardio minutes (per workout day)", chart_fn, _after(cardio, window_start)),
            _line("lifted", f"Total weight lifted ({u.load_label} per workout day)", chart_fn, _after(lifted, window_start))]


def calories_series(session: Session, user: User, window_start: date | None, dual_fn) -> dict:
    """Calories eaten each day with the day's estimated deficit (daily need plus workouts, minus food; negative is a surplus)."""
    query = select(FoodLog.eaten_on, func.sum(FoodLog.calories)).where(FoodLog.owner_id == user.id).group_by(FoodLog.eaten_on)
    if window_start is not None:
        query = query.where(FoodLog.eaten_on >= window_start)
    eaten = {d: round(c) for d, c in session.execute(query).all() if c}
    base, _ = day_overview.maintenance(session, user)
    burn = day_overview.burn_by_day(session, user.id) if base is not None else {}
    deficits = [(d, deficit(base, burn.get(d, 0), e)["kcal"]) for d, e in eaten.items()] if base is not None else []
    chart = dual_fn(list(eaten.items()), deficits)
    return {"key": "cal_deficit", "label": "Calories eaten vs deficit (kcal)", "kind": "dual", "chart": chart,
            "a_label": "Eaten (solid)", "b_label": "Deficit (dashed; below zero is a surplus)"}

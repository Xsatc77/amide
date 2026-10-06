"""Calorie estimates for a logged exercise. Every MET comes from the 2024 Compendium of Physical Activities.

    active_min = sets * reps * seconds_per_rep / 60                (rep-based)
    rest_min   = max(sets - 1, 0) * rest_per_set_min
    gross      = MET * 3.5 * kg / 200 * active_min + 1.5 * 3.5 * kg / 200 * rest_min
    net        = (MET - 1) * 3.5 * kg / 200 * active_min + 0.5 * 3.5 * kg / 200 * rest_min
    volume_lb  = load * implements * reps * sets

A rep-based exercise uses the MET of its Compendium row (the exercise's own, or a resistance category the person
picks); a duration-based one (plank, bike, treadmill) the MET of its row, or of the Compendium table row for the
speed, grade, power or effort entered. Seconds per rep and rest per set are the workbook's assumptions (the Compendium
has no timing). Pure: numbers in, numbers out; a missing or impossible input yields no estimate and a list of what is
missing, never a guess."""

from dataclasses import dataclass

from app.workouts import exercise_db
from app.workouts.exercise_db import Exercise, SpeedBand

LB_PER_KG = 2.2046226218
REST_MET = 1.5


@dataclass(frozen=True)
class Burn:
    met: float
    active_min: float
    rest_min: float
    gross_kcal: float
    net_kcal: float
    volume_lb: float | None
    code: str | None = None          # the Compendium code that gave the MET
    band_label: str | None = None


def kg_from_lb(lb: float) -> float:
    return lb / LB_PER_KG


def band_for(table_key: str, value: float) -> SpeedBand:
    """The band whose minimum is the highest one at or below `value`; below every band uses the lowest."""
    rows = exercise_db.speed_tables()[table_key].rows
    chosen = rows[0]
    for row in rows:
        if row.min <= value:
            chosen = row
    return chosen


def _kcal_per_min(met: float, kg: float) -> float:
    return met * 3.5 * kg / 200


def _positive(value) -> bool:
    return value is not None and value > 0


def _table_band(ex: Exercise, *, speed_mph, grade_pct, watts, effort) -> tuple[SpeedBand | None, str | None]:
    """(the Compendium row for what was entered, what is missing). A table with a default row (bikes, rowing,
    elliptical, ski ergometer) never reports missing: no wattage or effort entered uses the default row."""
    table = exercise_db.speed_tables()[ex.speed_table]
    if table.basis == "effort":
        by_code = {row.code: row for row in table.rows}
        return by_code.get(effort or "") or table.default or table.rows[0], None
    value = {"speed_mph": speed_mph, "grade_pct": grade_pct, "watts": watts}[table.basis]
    if not _positive(value):
        if table.default is not None:
            return table.default, None
        return None, {"speed_mph": "speed", "grade_pct": "incline grade"}[table.basis]
    if table.basis == "grade_pct" and value < table.rows[0].min:
        return None, f"incline grade of at least {table.rows[0].min:g}%"
    return band_for(ex.speed_table, value), None


def estimate(ex: Exercise, *, body_weight_lb: float | None, sets: int | None = None, reps: int | None = None,
             load_lb: float | None = None, implements: int = 1, category: str | None = None,
             minutes: float | None = None, speed_mph: float | None = None, grade_pct: float | None = None,
             watts: float | None = None, effort: str | None = None) -> tuple[Burn | None, list[str]]:
    """(Burn, []) when everything needed is present, else (None, [names of what is missing])."""
    missing = []
    if not _positive(body_weight_lb):
        missing.append("body weight")
    if ex.is_duration:
        if not _positive(minutes):
            missing.append("minutes")
        met, code, label = ex.met, ex.code, None
        if ex.speed_table:
            band, absent = _table_band(ex, speed_mph=speed_mph, grade_pct=grade_pct, watts=watts, effort=effort)
            if absent:
                missing.append(absent)
            if band is not None:
                met, code, label = band.met, band.code, band.label
        if missing or met is None:
            return None, missing
        kg = kg_from_lb(body_weight_lb)
        return Burn(met, minutes, 0.0, _kcal_per_min(met, kg) * minutes, _kcal_per_min(met - 1, kg) * minutes, None,
                    code, label), []
    for label, value in (("sets", sets), ("reps per set", reps)):
        if not _positive(value):
            missing.append(label)
    if ex.sec_per_rep is None:
        missing.append("seconds per rep")
    chosen = exercise_db.category(category) if category else None
    met, code = (chosen.met, chosen.code) if chosen else (ex.met, ex.code)
    if met is None:
        missing.append("a Compendium category")
    if missing:
        return None, missing
    kg = kg_from_lb(body_weight_lb)
    active = sets * reps * ex.sec_per_rep / 60
    rest = max(sets - 1, 0) * (ex.rest_min or 0.0)
    gross = _kcal_per_min(met, kg) * active + _kcal_per_min(REST_MET, kg) * rest
    net = _kcal_per_min(met - 1, kg) * active + _kcal_per_min(REST_MET - 1, kg) * rest
    volume = load_lb * max(implements, 1) * reps * sets if _positive(load_lb) else None
    return Burn(met, active, rest, gross, net, volume, code), []

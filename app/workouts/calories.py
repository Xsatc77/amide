"""Calorie estimates for a logged exercise, following the owner's workbook ("Workout Log Estimator" sheet):

    active_min = sets * reps * seconds_per_rep / 60                (rep-based)
    rest_min   = max(sets - 1, 0) * rest_per_set_min
    gross      = MET * 3.5 * kg / 200 * active_min + 1.5 * 3.5 * kg / 200 * rest_min
    net        = (MET - 1) * 3.5 * kg / 200 * active_min + 0.5 * 3.5 * kg / 200 * rest_min
    volume_lb  = load * implements * reps * sets

A rep-based exercise takes its MET from its style (Heavy Strength 5, Hypertrophy 3.5, ...); a duration-based one
(plank, bike, treadmill) from its own MET, or from the Compendium speed table by speed or grade. Pure: numbers in,
numbers out; a missing or impossible input yields no estimate and a list of what is missing, never a guess."""

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
    code: str | None = None          # the Compendium code that gave the MET, for speed-table rows
    band_label: str | None = None


def kg_from_lb(lb: float) -> float:
    return lb / LB_PER_KG


def style_met(style: str | None) -> float:
    profile = exercise_db.styles().get(style or "")
    return profile.met if profile else exercise_db.styles()["Hypertrophy / General"].met


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


def estimate(ex: Exercise, *, body_weight_lb: float | None, sets: int | None = None, reps: int | None = None,
             load_lb: float | None = None, implements: int = 1, style: str | None = None,
             minutes: float | None = None, speed_mph: float | None = None,
             grade_pct: float | None = None) -> tuple[Burn | None, list[str]]:
    """(Burn, []) when everything needed is present, else (None, [names of what is missing])."""
    missing = []
    if not _positive(body_weight_lb):
        missing.append("body weight")
    if ex.is_duration:
        if not _positive(minutes):
            missing.append("minutes")
        met, band = ex.met, None
        if ex.speed_table:
            table = exercise_db.speed_tables()[ex.speed_table]
            value = speed_mph if table.basis == "speed_mph" else grade_pct
            if not _positive(value):
                missing.append("speed" if table.basis == "speed_mph" else "incline grade")
            elif table.basis == "grade_pct" and value < table.rows[0].min:
                missing.append(f"incline grade of at least {table.rows[0].min:g}%")
            else:
                band = band_for(ex.speed_table, value)
                met = band.met
        if missing or met is None:
            return None, missing
        kg = kg_from_lb(body_weight_lb)
        gross = _kcal_per_min(met, kg) * minutes
        net = _kcal_per_min(met - 1, kg) * minutes
        return Burn(met, minutes, 0.0, gross, net, None, band.code if band else ex.code,
                    band.label if band else None), []
    for label, value in (("sets", sets), ("reps per set", reps)):
        if not _positive(value):
            missing.append(label)
    if ex.sec_per_rep is None:
        missing.append("seconds per rep")
    if missing:
        return None, missing
    met = style_met(style or ex.style)
    kg = kg_from_lb(body_weight_lb)
    active = sets * reps * ex.sec_per_rep / 60
    rest = max(sets - 1, 0) * (ex.rest_min or 0.0)
    gross = _kcal_per_min(met, kg) * active + _kcal_per_min(REST_MET, kg) * rest
    net = _kcal_per_min(met - 1, kg) * active + _kcal_per_min(REST_MET - 1, kg) * rest
    volume = load_lb * max(implements, 1) * reps * sets if _positive(load_lb) else None
    return Burn(met, active, rest, gross, net, volume), []

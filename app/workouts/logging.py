"""Turns the workout log form into stored exercise logs with calorie estimates, and finds an exercise's history.

The form's rules live here, not in the route: reps and sets are exact whole numbers (never a range), loads are
non-negative, a style must be one the database knows, and an exercise added on the day must resolve to a database
exercise. A ticked exercise with a database match is estimated when it has what the calculation needs; otherwise it
is saved anyway with a note saying what is missing, so a log is never refused for lack of calorie inputs."""

import math
import re
from dataclasses import dataclass
from datetime import date as date_type

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BodyMeasurement, WeightUnit, WorkoutExercise, WorkoutExerciseLog, WorkoutLog, WorkoutPlanDay
from app.workouts import calories, exercise_db
from app.workouts.exercise_db import Exercise
from app.workouts.exercise_match import match_exercise

MAX_COUNT = 999
MAX_BODY_WEIGHT_LB = 1500.0
MAX_MINUTES = 1000.0
MAX_SPEED_MPH = 30.0
MAX_GRADE_PCT = 40.0
_FIELDS = ("weight_value", "weight_unit", "reps_value", "sets_value", "minutes_value", "speed_value",
           "grade_value", "style_value", "implements_value")


@dataclass
class ParsedRow:
    exercise_id: int | None
    name: str
    db: Exercise | None
    completed: bool
    load: float | None
    unit: WeightUnit | None
    sets: int | None
    reps: int | None
    minutes: float | None
    speed: float | None
    grade: float | None
    style: str | None
    implements: int

    @property
    def load_lb(self) -> float | None:
        if self.load is None:
            return None
        return self.load * calories.LB_PER_KG if self.unit == WeightUnit.KG else self.load


@dataclass
class ParsedLog:
    log_date: date_type
    body_weight_lb: float | None
    rows: list[ParsedRow]


def latest_body_weight(session: Session, uid: int) -> float | None:
    """The owner's most recent weigh-in that recorded a weight."""
    return session.scalar(
        select(BodyMeasurement.weight_lbs)
        .where(BodyMeasurement.owner_id == uid, BodyMeasurement.weight_lbs.is_not(None))
        .order_by(BodyMeasurement.measured_at.desc(), BodyMeasurement.id.desc()).limit(1))


def single_int(text: str | None) -> int | None:
    """A plan's "3" as 3; a range or free text ("3-4", "AMRAP") as None, so it pre-fills nothing."""
    return int(text) if text and text.strip().isdigit() and 1 <= int(text) <= MAX_COUNT else None


def _text(raw, key: str) -> str:
    return str(raw.get(key) or "").strip()


def _number(raw, key: str, label: str, *, positive: bool = False, maximum: float | None = None) -> float | None:
    text = _text(raw, key)
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise HTTPException(422, f"{label} must be a number.")
    if not math.isfinite(value) or value < 0 or (positive and value == 0) or (maximum and value > maximum):
        limit = f" and at most {maximum:g}" if maximum else ""
        raise HTTPException(422, f"{label} must be {'greater than 0' if positive else 'a number from 0 up'}{limit}.")
    return value


def _whole(raw, key: str, label: str) -> int | None:
    text = _text(raw, key)
    if not text:
        return None
    if not re.fullmatch(r"\d+", text) or not 1 <= int(text) <= MAX_COUNT:
        raise HTTPException(422, f"{label} must be one whole number from 1 to {MAX_COUNT}, not a range.")
    return int(text)


def _row(raw, suffix: str, *, exercise_id: int | None, name: str, db: Exercise | None, completed: bool) -> ParsedRow:
    get = lambda field: _text(raw, f"{field}{suffix}")           # noqa: E731 -- suffix is "[ID]" or "" with a prefix
    key = lambda field: f"{field}{suffix}"                       # noqa: E731
    unit_text = get("weight_unit")
    try:
        unit = WeightUnit(unit_text) if unit_text else None
    except ValueError:
        raise HTTPException(422, f"Unknown weight unit for {name}.")
    load = _number(raw, key("weight_value"), f"Weight for {name}")
    style = get("style_value") or None
    if style and style not in exercise_db.choosable_styles():
        raise HTTPException(422, f"Unknown style for {name}.")
    return ParsedRow(
        exercise_id=exercise_id, name=name, db=db, completed=completed, load=load,
        unit=(unit or WeightUnit.LB) if load is not None else unit,
        sets=_whole(raw, key("sets_value"), f"Sets for {name}"), reps=_whole(raw, key("reps_value"), f"Reps for {name}"),
        minutes=_number(raw, key("minutes_value"), f"Minutes for {name}", positive=True, maximum=MAX_MINUTES),
        speed=_number(raw, key("speed_value"), f"Speed for {name}", positive=True, maximum=MAX_SPEED_MPH),
        grade=_number(raw, key("grade_value"), f"Incline grade for {name}", maximum=MAX_GRADE_PCT),
        style=style, implements=_whole(raw, key("implements_value"), f"Implements for {name}") or 1)


def resolve_db(ex: WorkoutExercise) -> Exercise | None:
    """The database exercise a plan exercise is estimated from. A person-confirmed match (including a confirmed
    "no estimate for this one") is final; otherwise the stored match, then a fresh confident match by name."""
    if ex.db_exercise_confirmed:
        return exercise_db.get(ex.db_exercise)
    return exercise_db.get(ex.db_exercise) or match_exercise(ex.name).exercise


def _extra_rows(raw, orphans: dict[int, WorkoutExerciseLog] | None = None) -> list[ParsedRow]:
    """Exercises added on the day: 'extra-N-field' keys. A blank exercise name drops the row; a name must resolve.
    A row that carries `extra-N-keep` (the id of a saved row whose plan exercise was removed) and keeps its name is
    that saved row again: it keeps its done state and its database match, even a missing one, instead of being
    re-resolved as if it were new."""
    orphans = orphans or {}
    indexes = sorted({int(m.group(1)) for k in raw.keys() if (m := re.fullmatch(r"extra-(\d+)-\w+", k))})
    rows = []
    for i in indexes:
        typed = _text(raw, f"extra-{i}-exercise")
        if not typed:
            continue
        keep = _text(raw, f"extra-{i}-keep")
        saved = orphans.get(int(keep)) if keep.isdigit() else None
        if saved is not None and (saved.name or "").casefold() == typed.casefold():
            prefixed = {k.replace(f"extra-{i}-", "", 1): v for k, v in raw.items() if k.startswith(f"extra-{i}-")}
            rows.append(_row(prefixed, "", exercise_id=None, name=saved.name, db=exercise_db.get(saved.db_exercise),
                             completed=bool(saved.completed)))
            continue
        match = match_exercise(typed)
        if not match.confident:
            close = ", ".join(e.name for e in match.suggestions)
            hint = f" Closest: {close}." if close else " Pick an exercise from the list."
            raise HTTPException(422, f'"{typed}" is not in the exercise database.{hint}')
        prefixed = {k.replace(f"extra-{i}-", "", 1): v for k, v in raw.items() if k.startswith(f"extra-{i}-")}
        rows.append(_row(prefixed, "", exercise_id=None, name=match.exercise.name, db=match.exercise, completed=True))
    return rows


def parse_log_form(raw, day: WorkoutPlanDay, orphans: dict[int, WorkoutExerciseLog] | None = None) -> ParsedLog:
    """Everything on the form, validated, before the existing log is touched (a bad value never costs what was logged)."""
    try:
        log_date = date_type.fromisoformat(raw["log_date"])
    except (KeyError, ValueError):
        raise HTTPException(422, "A valid workout date (YYYY-MM-DD) is required.")
    body_weight = _number(raw, "body_weight_lb", "Body weight", positive=True, maximum=MAX_BODY_WEIGHT_LB)
    rows = []
    for ex in day.exercises:
        rows.append(_row(raw, f"[{ex.id}]", exercise_id=ex.id, name=ex.name, db=resolve_db(ex),
                         completed=raw.get(f"completed[{ex.id}]") == "on"))
    rows.extend(_extra_rows(raw, orphans))
    return ParsedLog(log_date, body_weight, rows)


def build_exercise_log(row: ParsedRow, body_weight_lb: float | None) -> WorkoutExerciseLog:
    """The stored row, with its estimate and everything the estimate used."""
    ex = row.db
    log = WorkoutExerciseLog(
        exercise_id=row.exercise_id, name=row.name, completed=row.completed, weight_value=row.load,
        weight_unit=row.unit, reps_value=row.reps, sets=row.sets, duration_min=row.minutes, speed_mph=row.speed,
        grade_pct=row.grade, implements=row.implements, body_weight_lb=body_weight_lb,
        db_exercise=ex.name if ex else None, area=ex.area if ex else None, equipment=ex.equipment if ex else None,
        style=(row.style or ex.style) if ex and not ex.is_duration else None,
        sec_per_rep=ex.sec_per_rep if ex else None, rest_min=ex.rest_min if ex else None)
    if not row.completed:
        return log
    if ex is None:
        log.kcal_note = "No matching exercise in the database, so no calorie estimate."
        return log
    burn, missing = calories.estimate(
        ex, body_weight_lb=body_weight_lb, sets=row.sets, reps=row.reps, load_lb=row.load_lb,
        implements=row.implements, style=row.style, minutes=row.minutes, speed_mph=row.speed, grade_pct=row.grade)
    if burn is None:
        log.kcal_note = "Enter " + " and ".join(missing) + " for a calorie estimate."
        return log
    log.met, log.gross_kcal, log.net_kcal, log.volume_lb = burn.met, burn.gross_kcal, burn.net_kcal, burn.volume_lb
    log.compendium_code = burn.code if ex.speed_table else None
    return log


def last_performance(session: Session, uid: int, db_exercise: str | None) -> WorkoutExerciseLog | None:
    """The most recent completed log of this database exercise by this owner, in any plan."""
    if not db_exercise:
        return None
    return session.scalar(
        select(WorkoutExerciseLog).join(WorkoutLog, WorkoutExerciseLog.workout_log_id == WorkoutLog.id)
        .where(WorkoutLog.owner_id == uid, WorkoutExerciseLog.db_exercise == db_exercise,
               WorkoutExerciseLog.completed.is_(True))
        .order_by(WorkoutLog.log_date.desc(), WorkoutExerciseLog.id.desc()).limit(1))


def form_row(session: Session, uid: int, ex: WorkoutExercise, prior: WorkoutExerciseLog | None) -> dict:
    """What the log form needs to draw one plan exercise: its database match, this date's saved log (if any), the
    last time the exercise was logged anywhere, and the values to show in the inputs."""
    db = resolve_db(ex)
    last = None if prior is not None else last_performance(session, uid, db.name if db else None)
    source = prior or last
    table = exercise_db.speed_tables().get(db.speed_table) if db and db.speed_table else None
    return {
        "ex": ex, "db": db, "prior": prior, "last": last, "speed_basis": table.basis if table else None,
        "speed_label": table.unit_label if table else None,
        "shown": {
            "weight_value": source.weight_value if source else None, "weight_unit": source.weight_unit if source else None,
            "sets": (source.sets if source and source.sets else None) or single_int(ex.sets_text),
            "reps": source.reps_value if source else None, "minutes": source.duration_min if source else None,
            "speed": source.speed_mph if source else None, "grade": source.grade_pct if source else None,
            "style": (source.style if source and source.style else None) or (db.style if db else None),
            "implements": (source.implements if source and source.implements else 1),
        },
    }

"""Workout Plans: manual/PDF creation, the shared review/edit screen, day-of-week scheduling,
and the one-Active-plan-at-a-time rule."""

import math
from datetime import date
from datetime import date as date_type
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import (
    WEEKDAY_LETTERS,
    WeightUnit,
    WorkoutExercise,
    WorkoutExerciseLog,
    WorkoutLog,
    WorkoutPlan,
    WorkoutPlanDay,
    WorkoutSource,
)
from app.templating import templates
from app.uploads import UploadError, save_workout_pdf
from app.workouts.pdf_parser import extract_text, parse_workout_pdf

router = APIRouter()

_WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # parallel to WEEKDAY_LETTERS


def workouts_due_today(session: Session, uid: int, today: date_type) -> list[WorkoutPlanDay]:
    """Active plans' days scheduled for today's weekday, excluding any already logged today."""
    letter = WEEKDAY_LETTERS[today.weekday()]
    days = session.scalars(
        select(WorkoutPlanDay).join(WorkoutPlan)
        .where(WorkoutPlan.owner_id == uid, WorkoutPlan.ended_on.is_(None),
              WorkoutPlanDay.weekdays.isnot(None))
    ).all()
    due = [d for d in days if letter in d.weekdays]
    logged_day_ids = {
        wl.plan_day_id for wl in session.scalars(
            select(WorkoutLog).where(WorkoutLog.owner_id == uid, WorkoutLog.log_date == today))
    }
    return [d for d in due if d.id not in logged_day_ids]


def scheduled_workout_dates(session: Session, uid: int, start: date_type, end: date_type) -> set[date_type]:
    """Every date in [start, end] on which an Active plan has a day scheduled -- regardless of
    whether it's already been logged (unlike workouts_due_today, which is specifically "still
    pending today"). Used only for a plain calendar marker, not for the Today list."""
    days = session.scalars(
        select(WorkoutPlanDay).join(WorkoutPlan)
        .where(WorkoutPlan.owner_id == uid, WorkoutPlan.ended_on.is_(None),
              WorkoutPlanDay.weekdays.isnot(None))
    ).all()
    dates = set()
    d = start
    while d <= end:
        letter = WEEKDAY_LETTERS[d.weekday()]
        if any(letter in day.weekdays for day in days):
            dates.add(d)
        d += timedelta(days=1)
    return dates


def _get_own_plan(session: Session, plan_id: int, uid: int) -> WorkoutPlan:
    p = session.get(WorkoutPlan, plan_id)
    if p is None or p.owner_id != uid:
        raise HTTPException(404, "Workout plan not found")
    return p


def _get_own_day(session: Session, plan_day_id: int, uid: int) -> WorkoutPlanDay:
    day = session.get(WorkoutPlanDay, plan_day_id)
    if day is None or day.plan.owner_id != uid:
        raise HTTPException(404, "Workout day not found")
    return day


def save_workout_plan(session: Session, plan: WorkoutPlan, name: str, days_data: list[dict]) -> WorkoutPlan:
    """Write `name` and reconcile `plan`'s days/exercises against `days_data` by row id.

    Each day/exercise dict may carry an "id" (the editor posts every existing row's real id as a
    hidden input; the PDF parser's output and rows added in the browser have none):
      - an entry whose id matches one of this plan's existing rows updates that row in place
        (label/position, or name/sets/reps/rest/position) -- never touching a day's `weekdays`;
      - an entry with no id (or an id that isn't one of this plan's own rows) creates a new row;
      - an existing row whose id isn't posted back at all is removed (delete-orphan), which
        cascades away that row's logged history -- the one intentionally destructive case,
        since the user removed the day/exercise itself.

    This supersedes the earlier clear-and-rebuild approach, which destroyed every day's
    `weekdays` schedule and (via the ON DELETE CASCADE foreign keys) all logged workout history
    on *any* save, even one that only fixed a typo."""
    plan.name = name
    # pop() so a (malformed) post repeating one id can never map two entries onto the same row.
    existing_days = {d.id: d for d in plan.days if d.id is not None}
    new_days = []
    for i, d in enumerate(days_data):
        day = existing_days.pop(d.get("id"), None) or WorkoutPlanDay()
        day.position = i
        day.label = d["label"] or f"Day {i + 1}"
        _reconcile_exercises(day, d["exercises"])
        new_days.append(day)
    for day in existing_days.values():  # not posted back: the user removed it
        plan.days.remove(day)
    plan.days = new_days
    return plan


def _reconcile_exercises(day: WorkoutPlanDay, exercises_data: list[dict]) -> None:
    """save_workout_plan's per-day counterpart: same update-by-id / create / remove rules."""
    existing = {ex.id: ex for ex in day.exercises if ex.id is not None}
    new_exercises = []
    for j, data in enumerate(exercises_data):
        ex = existing.pop(data.get("id"), None) or WorkoutExercise()
        ex.position = j
        ex.name = data["name"]
        ex.sets_text = data.get("sets_text")
        ex.reps_text = data.get("reps_text")
        ex.rest_text = data.get("rest_text")
        new_exercises.append(ex)
    day.exercises = new_exercises  # any existing exercise not in this list is delete-orphaned


def _activate(session: Session, plan: WorkoutPlan, uid: int) -> None:
    """Ends whichever other plan of this owner's is currently Active -- never more than one
    Active plan at a time."""
    previously_active = session.scalar(
        select(WorkoutPlan).where(WorkoutPlan.owner_id == uid, WorkoutPlan.id != plan.id,
                                  WorkoutPlan.ended_on.is_(None)))
    if previously_active is not None:
        previously_active.ended_on = date.today()


def _days_from_form(form: dict) -> tuple[str, list[dict]]:
    """Parses the editor form's bracketed-array field names (day_label[], exercise_name[N][],
    etc.) into (name, days_data) matching save_workout_plan's expected shape, including each
    row's posted id (day_id[] / exercise_id[N][], blank for a row added in the browser)."""
    name = form.get("name", ["Untitled Plan"])[0]
    labels = form.get("day_label[]", [])
    day_ids = form.get("day_id[]", [])
    days = []
    for i, label in enumerate(labels):
        ex_ids = form.get(f"exercise_id[{i}][]", [])
        ex_names = form.get(f"exercise_name[{i}][]", [])
        ex_sets = form.get(f"exercise_sets[{i}][]", [])
        ex_reps = form.get(f"exercise_reps[{i}][]", [])
        ex_rest = form.get(f"exercise_rest[{i}][]", [])
        exercises = [
            {"id": _form_id(ex_ids, j), "name": ex_names[j].strip(),
             "sets_text": _at(ex_sets, j) or None, "reps_text": _at(ex_reps, j) or None,
             "rest_text": _at(ex_rest, j) or None}
            for j in range(len(ex_names)) if ex_names[j].strip()
        ]
        days.append({"id": _form_id(day_ids, i), "label": label.strip(), "exercises": exercises})
    return name, days


def _at(values: list[str], i: int) -> str:
    return values[i] if i < len(values) else ""


def _form_id(values: list[str], i: int) -> int | None:
    """A posted row id, or None for a blank/missing/non-numeric one (a new row)."""
    raw = _at(values, i).strip()
    return int(raw) if raw.isdigit() else None


@router.get("/workouts")
def workouts_list(request: Request, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    plans = session.scalars(
        select(WorkoutPlan).where(WorkoutPlan.owner_id == uid).order_by(WorkoutPlan.created_at.desc())).all()
    return templates.TemplateResponse(request, "workouts/list.html", {"plans": plans})


@router.get("/workouts/new")
def workouts_new(request: Request):
    return templates.TemplateResponse(request, "workouts/edit.html", {"plan": None, "days": []})


@router.post("/workouts")
async def workouts_create(request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    raw = await request.form()
    form = {k: raw.getlist(k) for k in raw.keys()}
    name, days_data = _days_from_form(form)
    plan = WorkoutPlan(owner_id=uid, source=WorkoutSource.MANUAL, started_on=date.today())
    session.add(plan)
    save_workout_plan(session, plan, name, days_data)
    session.flush()
    _activate(session, plan, uid)
    session.commit()
    return RedirectResponse(f"/workouts/{plan.id}/edit", status_code=303)


@router.post("/workouts/upload")
async def workouts_upload(pdf: UploadFile = File(...), session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    try:
        filename = await save_workout_pdf(pdf)
    except UploadError as e:
        raise HTTPException(422, str(e))
    raw_bytes = (config.WORKOUT_PDF_DIR / filename).read_bytes()
    try:
        text = extract_text(raw_bytes)
    except Exception:  # noqa: BLE001 -- any malformed/encrypted PDF pypdf can't read
        # Never reject an upload outright: an unreadable PDF becomes an empty plan the user fills
        # in by hand in the editor (same as a readable one the parser found no days in).
        text = ""
    parsed = parse_workout_pdf(text)
    plan = WorkoutPlan(owner_id=uid, source=WorkoutSource.PDF, source_pdf_filename=filename,
                       started_on=date.today())
    session.add(plan)
    name = parsed["name"] or pdf.filename or "Imported Plan"
    save_workout_plan(session, plan, name, parsed["days"])
    session.flush()
    _activate(session, plan, uid)
    session.commit()
    return RedirectResponse(f"/workouts/{plan.id}/edit", status_code=303)


@router.get("/workouts/{plan_id}/edit")
def workouts_edit(plan_id: int, request: Request, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    has_logged_history = session.query(WorkoutLog).join(WorkoutPlanDay).filter(
        WorkoutPlanDay.plan_id == plan.id).first() is not None
    return templates.TemplateResponse(request, "workouts/edit.html", {
        "plan": plan, "days": plan.days, "has_logged_history": has_logged_history,
        "weekday_choices": list(zip(WEEKDAY_LETTERS, _WEEKDAY_NAMES)),
    })


@router.post("/workouts/{plan_id}")
async def workouts_update(plan_id: int, request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    raw = await request.form()
    form = {k: raw.getlist(k) for k in raw.keys()}
    name, days_data = _days_from_form(form)
    save_workout_plan(session, plan, name, days_data)
    session.commit()
    return RedirectResponse(f"/workouts/{plan.id}/edit", status_code=303)


@router.post("/workouts/{plan_id}/schedule")
async def workouts_schedule(plan_id: int, request: Request, session: Session = Depends(get_session),
                            uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    raw = await request.form()
    for day in plan.days:
        # One checkbox per weekday letter; stored in canonical WEEKDAY_LETTERS order regardless of
        # posted order, silently dropping anything that isn't a real weekday letter.
        checked = set(raw.getlist(f"weekdays[{day.id}][]"))
        day.weekdays = "".join(letter for letter in WEEKDAY_LETTERS if letter in checked) or None
    session.commit()
    return RedirectResponse(f"/workouts/{plan.id}/edit", status_code=303)


@router.post("/workouts/{plan_id}/activate")
def workouts_activate(plan_id: int, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    plan.ended_on = None
    _activate(session, plan, uid)
    session.commit()
    return RedirectResponse("/workouts", status_code=303)


@router.get("/workouts/day/{plan_day_id}/log")
def workouts_log_form(plan_day_id: int, request: Request, log_date: date_type | None = None,
                      session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    day = _get_own_day(session, plan_day_id, uid)
    log_date = log_date or date_type.today()
    # Re-opening an already-logged date shows what was logged (saving replaces that log), instead
    # of a blank form that would silently overwrite it with blanks.
    existing = session.scalar(
        select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id, WorkoutLog.log_date == log_date))
    prior = {el.exercise_id: el for el in existing.exercise_logs} if existing else {}
    return templates.TemplateResponse(request, "workouts/log.html", {
        "day": day, "log_date": log_date, "units": list(WeightUnit), "prior": prior,
    })


@router.post("/workouts/day/{plan_day_id}/log")
async def workouts_log_save(plan_day_id: int, request: Request, session: Session = Depends(get_session),
                            uid: int = Depends(current_user_id)):
    day = _get_own_day(session, plan_day_id, uid)
    raw = await request.form()
    try:
        log_date = date_type.fromisoformat(raw["log_date"])
    except (KeyError, ValueError):
        raise HTTPException(422, "A valid workout date (YYYY-MM-DD) is required.")

    # Parse every exercise's input before touching the existing log, so a bad value never costs
    # the user what was already logged for this date.
    exercise_logs = []
    for ex in day.exercises:
        weight_value = raw.get(f"weight_value[{ex.id}]")
        weight_unit = raw.get(f"weight_unit[{ex.id}]")
        reps_value = raw.get(f"reps_value[{ex.id}]")
        try:
            weight = float(weight_value) if weight_value else None
            if weight is not None and not math.isfinite(weight):
                raise ValueError
        except ValueError:
            raise HTTPException(422, f"Weight for {ex.name} must be a number.")
        try:
            reps = int(reps_value) if reps_value else None
        except ValueError:
            raise HTTPException(422, f"Reps for {ex.name} must be a whole number.")
        try:
            unit = WeightUnit(weight_unit) if weight_unit else None
        except ValueError:
            raise HTTPException(422, f"Unknown weight unit for {ex.name}.")
        exercise_logs.append(WorkoutExerciseLog(
            exercise_id=ex.id, completed=raw.get(f"completed[{ex.id}]") == "on",
            weight_value=weight, weight_unit=unit, reps_value=reps,
        ))

    existing = session.scalar(
        select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id, WorkoutLog.log_date == log_date))
    if existing is not None:
        session.delete(existing)
        session.flush()

    log = WorkoutLog(owner_id=uid, plan_day_id=day.id, log_date=log_date, exercise_logs=exercise_logs)
    session.add(log)
    session.commit()
    return RedirectResponse("/today", status_code=303)

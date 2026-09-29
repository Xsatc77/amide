"""Workout Plans: manual/PDF creation, the shared review/edit screen, day-of-week scheduling,
and the one-Active-plan-at-a-time rule."""

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
    """Write `name` and fully replace `plan`'s days/exercises from `days_data` (the parser's own
    output shape). Mirrors save_protocol's clear-and-rebuild idiom -- simplest way to reconcile
    arbitrary add/remove/reorder from either the manual editor or a re-parsed PDF."""
    plan.name = name
    plan.days.clear()
    session.flush()
    plan.days = [
        WorkoutPlanDay(
            position=i, label=d["label"] or f"Day {i + 1}",
            exercises=[
                WorkoutExercise(
                    position=j, name=ex["name"], sets_text=ex.get("sets_text"),
                    reps_text=ex.get("reps_text"), rest_text=ex.get("rest_text"))
                for j, ex in enumerate(d["exercises"])
            ],
        )
        for i, d in enumerate(days_data)
    ]
    return plan


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
    etc.) into (name, days_data) matching save_workout_plan's expected shape."""
    name = form.get("name", ["Untitled Plan"])[0]
    labels = form.get("day_label[]", [])
    days = []
    for i, label in enumerate(labels):
        ex_names = form.get(f"exercise_name[{i}][]", [])
        ex_sets = form.get(f"exercise_sets[{i}][]", [])
        ex_reps = form.get(f"exercise_reps[{i}][]", [])
        ex_rest = form.get(f"exercise_rest[{i}][]", [])
        exercises = [
            {"name": ex_names[j], "sets_text": ex_sets[j] or None,
             "reps_text": ex_reps[j] or None, "rest_text": ex_rest[j] or None}
            for j in range(len(ex_names)) if ex_names[j].strip()
        ]
        days.append({"label": label, "exercises": exercises})
    return name, days


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
    text = extract_text(raw_bytes)
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
        key = f"weekdays[{day.id}]"
        day.weekdays = raw.get(key) or None
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
    return templates.TemplateResponse(request, "workouts/log.html", {
        "day": day, "log_date": log_date or date_type.today(), "units": list(WeightUnit),
    })


@router.post("/workouts/day/{plan_day_id}/log")
async def workouts_log_save(plan_day_id: int, request: Request, session: Session = Depends(get_session),
                            uid: int = Depends(current_user_id)):
    day = _get_own_day(session, plan_day_id, uid)
    raw = await request.form()
    log_date = date_type.fromisoformat(raw["log_date"])

    existing = session.scalar(
        select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id, WorkoutLog.log_date == log_date))
    if existing is not None:
        session.delete(existing)
        session.flush()

    log = WorkoutLog(owner_id=uid, plan_day_id=day.id, log_date=log_date)
    session.add(log)
    for ex in day.exercises:
        weight_value = raw.get(f"weight_value[{ex.id}]")
        weight_unit = raw.get(f"weight_unit[{ex.id}]")
        reps_value = raw.get(f"reps_value[{ex.id}]")
        log.exercise_logs.append(WorkoutExerciseLog(
            exercise_id=ex.id,
            completed=raw.get(f"completed[{ex.id}]") == "on",
            weight_value=float(weight_value) if weight_value else None,
            weight_unit=WeightUnit(weight_unit) if weight_unit else None,
            reps_value=int(reps_value) if reps_value else None,
        ))
    session.commit()
    return RedirectResponse("/today", status_code=303)

"""Workout Plans: manual/PDF creation, the shared review/edit screen, day-of-week scheduling,
and the one-Active-plan-at-a-time rule."""

from datetime import date
from datetime import date as date_type
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config, uploads
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
from app.workouts import exercise_db
from app.workouts.export import workout_log_xlsx
from app.workouts.exercise_match import match_exercise
from app.workouts import logging as workout_logging
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


def week_status(session: Session, uid: int, today: date_type) -> list[dict]:
    """This calendar week (Mon-Sun) as a 7-day schedule strip for the Dashboard -- a day keeps
    showing its outcome after it's logged, rather than a "due today" list item disappearing the
    moment it's done (an early-morning workout would otherwise leave the panel looking empty for
    the rest of the day)."""
    monday = today - timedelta(days=today.weekday())
    week_end = monday + timedelta(days=6)
    scheduled = scheduled_workout_dates(session, uid, monday, week_end)
    logged_dates = {
        wl.log_date for wl in session.scalars(
            select(WorkoutLog).where(WorkoutLog.owner_id == uid,
                                     WorkoutLog.log_date >= monday, WorkoutLog.log_date <= week_end))
    }
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        if d not in scheduled:
            status = "rest"
        elif d in logged_dates:
            status = "done"
        elif d < today:
            status = "missed"
        else:
            status = "upcoming"
        days.append({"date": d, "label": _WEEKDAY_NAMES[i], "status": status, "is_today": d == today})
    return days


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


def _get_own_exercise(session: Session, exercise_id: int, uid: int) -> WorkoutExercise:
    ex = session.get(WorkoutExercise, exercise_id)
    if ex is None:
        raise HTTPException(404, "Exercise not found")
    day = session.get(WorkoutPlanDay, ex.day_id)
    if day is None or day.plan.owner_id != uid:
        raise HTTPException(404, "Exercise not found")
    return ex


def save_workout_plan(session: Session, plan: WorkoutPlan, name: str, days_data: list[dict]) -> WorkoutPlan:
    """Write `name` and reconcile `plan`'s days/exercises against `days_data` by row id.

    Each day/exercise dict may carry an "id" (the editor posts every existing row's real id as a
    hidden input; the PDF parser's output and rows added in the browser have none):
      - an entry whose id matches one of this plan's existing rows updates that row in place
        (label/position, or name/sets/reps/rest/position) -- never touching a day's `weekdays`;
      - an entry with no id (or an id that isn't one of this plan's own rows) creates a new row;
      - an existing row whose id isn't posted back at all is removed (delete-orphan). Its logged
        history is kept: logs hold their own snapshot and the foreign keys to the plan rows are
        ON DELETE SET NULL, so removing a day or exercise never deletes what was logged.

    This supersedes the earlier clear-and-rebuild approach, which destroyed every day's
    `weekdays` schedule on *any* save, even one that only fixed a typo."""
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
    """save_workout_plan's per-day counterpart: same update-by-id / create / remove rules. Each exercise is also matched
    to the exercise database (see app/workouts/exercise_match.py) unless a person already confirmed its match and has
    not renamed it; a renamed or new exercise starts unconfirmed."""
    existing = {ex.id: ex for ex in day.exercises if ex.id is not None}
    new_exercises = []
    for j, data in enumerate(exercises_data):
        ex = existing.pop(data.get("id"), None) or WorkoutExercise()
        renamed = ex.name != data["name"]
        ex.position = j
        ex.name = data["name"]
        ex.sets_text = data.get("sets_text")
        ex.reps_text = data.get("reps_text")
        ex.rest_text = data.get("rest_text")
        if renamed or not ex.db_exercise_confirmed:
            match = match_exercise(ex.name)
            ex.db_exercise = match.exercise.name if match.confident else None
            ex.db_exercise_confirmed = False
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


@router.get("/workouts/export.xlsx")
def workouts_export(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return Response(workout_log_xlsx(session, uid), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="amide-workout-log.xlsx"'})


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
        "suggestions": {ex.id: match_exercise(ex.name).suggestions
                        for day in plan.days for ex in day.exercises if not ex.db_exercise},
        "exercise_names": [e.name for e in exercise_db.all_exercises()],
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


@router.post("/workouts/exercises/{exercise_id}/match")
async def workouts_match_exercise(exercise_id: int, request: Request, session: Session = Depends(get_session),
                                  uid: int = Depends(current_user_id)):
    """Confirms which database exercise a plan exercise is, so its calories are estimated from it. A suggestion button
    posts `suggested`; the text box posts `db_exercise` (a database name or a common name). Blank clears the match
    and keeps it cleared ("no estimate for this one")."""
    ex = _get_own_exercise(session, exercise_id, uid)
    raw = await request.form()
    typed = str(raw.get("suggested") or raw.get("db_exercise") or "").strip()
    if typed:
        match = match_exercise(typed)
        if not match.confident:
            raise HTTPException(422, f'"{typed}" is not in the exercise database.')
        ex.db_exercise = match.exercise.name
    else:
        ex.db_exercise = None
    ex.db_exercise_confirmed = True
    plan_id = session.get(WorkoutPlanDay, ex.day_id).plan_id
    session.commit()
    return RedirectResponse(f"/workouts/{plan_id}/edit", status_code=303)


@router.post("/workouts/{plan_id}/end")
def workouts_end(plan_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Stop following a plan; it stays in the list (and can be made active again)."""
    plan = _get_own_plan(session, plan_id, uid)
    plan.ended_on = date.today()
    session.commit()
    return RedirectResponse("/workouts", status_code=303)


@router.post("/workouts/{plan_id}/delete")
def workouts_delete(plan_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Remove a plan, its days and exercises, and its imported PDF. Workouts already logged stay (their logs keep their own snapshots)."""
    plan = _get_own_plan(session, plan_id, uid)
    pdf = plan.source_pdf_filename
    session.delete(plan)
    session.commit()
    uploads.delete_workout_pdf(pdf)
    return RedirectResponse("/workouts", status_code=303)


@router.post("/workouts/{plan_id}/activate")
def workouts_activate(plan_id: int, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    plan = _get_own_plan(session, plan_id, uid)
    plan.ended_on = None
    _activate(session, plan, uid)
    session.commit()
    return RedirectResponse("/workouts", status_code=303)


@router.get("/workouts/log")
def workouts_log_picker(plan_day_id: int, log_date: date_type | None = None, session: Session = Depends(get_session),
                        uid: int = Depends(current_user_id)):
    """The Journal's Log Workout dialog posts here: validate the day is the person's own, then open its log form."""
    day = _get_own_day(session, plan_day_id, uid)
    target = f"/workouts/day/{day.id}/log"
    return RedirectResponse(f"{target}?log_date={log_date.isoformat()}" if log_date else target, status_code=303)


@router.get("/workouts/day/{plan_day_id}/log")
def workouts_log_form(plan_day_id: int, request: Request, log_date: date_type | None = None,
                      session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    day = _get_own_day(session, plan_day_id, uid)
    log_date = log_date or date_type.today()
    # Re-opening an already-logged date shows what was logged (saving replaces that log), instead
    # of a blank form that would silently overwrite it with blanks.
    existing = session.scalar(
        select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id, WorkoutLog.log_date == log_date))
    saved = existing.exercise_logs if existing else []
    prior = {el.exercise_id: el for el in saved if el.exercise_id is not None}
    rows = [workout_logging.form_row(session, uid, ex, prior.get(ex.id)) for ex in day.exercises]
    body_weight = next((el.body_weight_lb for el in saved if el.body_weight_lb), None)         or workout_logging.latest_body_weight(session, uid)
    net = sum(el.net_kcal for el in saved if el.net_kcal)
    gross = sum(el.gross_kcal for el in saved if el.gross_kcal)
    return templates.TemplateResponse(request, "workouts/log.html", {
        "day": day, "log_date": log_date, "units": list(WeightUnit), "rows": rows,
        "extras": [el for el in saved if el.exercise_id is None], "body_weight": body_weight,
        "categories": exercise_db.categories(), "category_codes": {c.code for c in exercise_db.categories()},
        "exercise_names": [e.name for e in exercise_db.all_exercises()],
        "net_kcal": net, "gross_kcal": gross, "has_estimate": any(el.net_kcal for el in saved),
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
    existing = session.scalar(
        select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id, WorkoutLog.log_date == log_date))
    orphans = {el.id: el for el in existing.exercise_logs if el.exercise_id is None} if existing else {}
    parsed = workout_logging.parse_log_form(raw, day, orphans)  # validates everything before touching the old log
    body_weight = parsed.body_weight_lb or workout_logging.latest_body_weight(session, uid)
    exercise_logs = [workout_logging.build_exercise_log(row, body_weight) for row in parsed.rows]

    if existing is not None:
        session.delete(existing)
        session.flush()

    log = WorkoutLog(owner_id=uid, plan_day_id=day.id, day_label=day.label, plan_name=day.plan.name,
                     log_date=parsed.log_date, exercise_logs=exercise_logs)
    session.add(log)
    session.commit()
    return RedirectResponse("/today", status_code=303)

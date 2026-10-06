import zoneinfo
from datetime import date, datetime, timezone as dt_timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.db import get_session
from app.models import (
    DoseLog, JournalEntry, JournalEntrySideEffect, JournalQuickNote, JournalSideEffect, Share, ShareCategory, User,
)

router = APIRouter()

RATING_FIELDS = ("mood", "energy", "sleep_quality")


def _journal_query(uid: int):
    """This user's journal entries (others' are never visible)."""
    return select(JournalEntry).where(JournalEntry.owner_id == uid).options(
        selectinload(JournalEntry.side_effects), selectinload(JournalEntry.quick_notes))


def _shared_journal_query(uid: int):
    """Journal entries owned by anyone who granted this user Personal Data sharing."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.PERSONAL_DATA)
    return select(JournalEntry).where(JournalEntry.owner_id.in_(shared_owner_ids)).options(
        selectinload(JournalEntry.side_effects), selectinload(JournalEntry.quick_notes))


def get_or_create_entry(session: Session, owner_id: int, entry_date: date) -> JournalEntry:
    """The existing row for this owner/date, or a newly flushed (not committed) one."""
    entry = session.scalar(
        _journal_query(owner_id).where(JournalEntry.entry_date == entry_date))
    if entry is not None:
        return entry
    entry = JournalEntry(owner_id=owner_id, entry_date=entry_date)
    session.add(entry)
    session.flush()
    return entry


def doses_for(session: Session, owner_id: int, entry_date: date) -> list[dict]:
    """That owner's logged doses on that date. For a shared entry, `owner_id` must be the entry's
    own owner (the sharing user), not the viewer -- the viewer's access to see the entry at all is
    already gated by the sharing query, so no additional check is needed here."""
    rows = session.scalars(
        select(DoseLog).where(DoseLog.owner_id == owner_id, DoseLog.scheduled_date == entry_date)).all()
    return [_dose_dict(r) for r in rows]


def _dose_dict(r) -> dict:
    return {"peptide_name": r.peptide_name, "dose_value": r.dose_value, "dose_unit": r.dose_unit, "status": r.status}


def workouts_for(session: Session, owner_id: int, entry_date: date) -> list[dict]:
    """That owner's completed workouts on that date, with their estimated calories (None when nothing was estimated).
    Query-time only, same ownership-check reasoning as doses_for: for a shared entry, owner_id must be the entry's own
    owner, not the viewer -- the viewer's access is already gated by the sharing query."""
    from app.models import WorkoutLog  # deferred: avoid a module-load cycle with app.routers.workouts
    rows = session.scalars(
        select(WorkoutLog).where(WorkoutLog.owner_id == owner_id, WorkoutLog.log_date == entry_date)
    ).all()
    return [_workout_dict(r) for r in rows]


def _workout_dict(r) -> dict:
    net = sum(el.net_kcal for el in r.exercise_logs if el.net_kcal)
    gross = sum(el.gross_kcal for el in r.exercise_logs if el.gross_kcal)
    return {
        "label": r.day_label or (r.plan_day.label if r.plan_day else "Workout"),
        "completed_count": sum(1 for el in r.exercise_logs if el.completed),
        "total_count": len(r.exercise_logs),
        "net_kcal": round(net) if net else None,
        "gross_kcal": round(gross) if gross else None,
    }


def _workout_only_views(session: Session, uid: int, entry_dates: set[date]) -> list[dict]:
    """Rows for the viewer's own days that have a logged workout but no journal entry, so a workout logged from the
    Journal always appears there. Shaped like _entry_view's output. Loaded in three queries however many days there
    are (the logs with their exercises, then that day's doses), not per day."""
    from app.models import DoseLog, WorkoutLog
    logs = session.scalars(
        select(WorkoutLog).where(WorkoutLog.owner_id == uid)
        .options(selectinload(WorkoutLog.exercise_logs), selectinload(WorkoutLog.plan_day))).all()
    by_day: dict[date, list] = {}
    for r in logs:
        if r.log_date not in entry_dates:
            by_day.setdefault(r.log_date, []).append(r)
    if not by_day:
        return []
    doses: dict[date, list] = {}
    for d in session.scalars(select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date.in_(list(by_day)))):
        doses.setdefault(d.scheduled_date, []).append(_dose_dict(d))
    return [{
        "date": day, "mood": None, "energy": None, "sleep_quality": None, "side_effects": [],
        "side_effects_other": None, "notes": None, "owner_name": None, "quick_notes": [],
        "doses": doses.get(day, []), "workouts": [_workout_dict(r) for r in day_logs],
    } for day, day_logs in by_day.items()]


def _workout_day_options(session: Session, uid: int) -> list[dict]:
    """The days of the viewer's active plan, for the Log Workout picker (there is only ever one active plan)."""
    from app.models import WorkoutPlan, WorkoutPlanDay
    rows = session.execute(
        select(WorkoutPlanDay.id, WorkoutPlanDay.label, WorkoutPlan.name)
        .join(WorkoutPlan, WorkoutPlanDay.plan_id == WorkoutPlan.id)
        .where(WorkoutPlan.owner_id == uid, WorkoutPlan.ended_on.is_(None))
        .order_by(WorkoutPlan.created_at.desc(), WorkoutPlanDay.position)).all()
    return [{"id": r[0], "label": r[1], "plan_name": r[2]} for r in rows]


def _local_time_str(dt: datetime, tz_name: str | None) -> str:
    """`dt` (stored/naive-UTC, mirroring app/routers/settings.py's `_format_last_login`) formatted
    as HH:MM in `tz_name` when given, else left as UTC."""
    aware = dt.replace(tzinfo=dt_timezone.utc)
    if tz_name:
        aware = aware.astimezone(zoneinfo.ZoneInfo(tz_name))
    return aware.strftime("%H:%M")


def _entry_view(entry: JournalEntry, doses: list[dict], workouts: list[dict], owner_name: str | None = None,
                viewer_tz: str | None = None) -> dict:
    return {
        "date": entry.entry_date,
        "mood": entry.mood,
        "energy": entry.energy,
        "sleep_quality": entry.sleep_quality,
        "side_effects": [se.side_effect.value for se in entry.side_effects],
        "side_effects_other": entry.side_effects_other,
        "notes": entry.notes,
        "owner_name": owner_name,
        "quick_notes": [
            {"noted_at": qn.noted_at, "time_display": _local_time_str(qn.noted_at, viewer_tz), "text": qn.text}
            for qn in entry.quick_notes
        ],
        "doses": doses,
        "workouts": workouts,
    }


def journal_tab_context(session: Session, viewer_uid: int) -> dict:
    viewer = session.get(User, viewer_uid)
    viewer_tz = viewer.timezone if viewer else None

    own_entries = session.scalars(
        _journal_query(viewer_uid).order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())).all()

    shared_entries = session.scalars(
        _shared_journal_query(viewer_uid).order_by(
            JournalEntry.entry_date.desc(), JournalEntry.id.desc())).all()
    owner_ids = {e.owner_id for e in shared_entries}
    owner_names = {}
    if owner_ids:
        owner_names = dict(session.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all())

    views = [_entry_view(e, doses_for(session, e.owner_id, e.entry_date),
                         workouts_for(session, e.owner_id, e.entry_date), viewer_tz=viewer_tz)
            for e in own_entries]
    views += [
        _entry_view(e, doses_for(session, e.owner_id, e.entry_date),
                   workouts_for(session, e.owner_id, e.entry_date), owner_name=owner_names.get(e.owner_id),
                   viewer_tz=viewer_tz)
        for e in shared_entries
    ]
    views += _workout_only_views(session, viewer_uid, {e.entry_date for e in own_entries})
    views.sort(key=lambda v: v["date"], reverse=True)

    # Today's own entry (if any), so the "New Entry" dialog can open pre-filled with what's already
    # there instead of blank -- re-saving must edit that same row, not silently wipe it (spec's
    # "Full entry form" section). Only the read side of get_or_create_entry's lookup; never create
    # a row here, since merely opening the dialog/viewing the tab must not touch the database.
    today_row = session.scalar(_journal_query(viewer_uid).where(JournalEntry.entry_date == date.today()))
    today_entry = (
        _entry_view(today_row, doses_for(session, viewer_uid, date.today()),
                   workouts_for(session, viewer_uid, date.today()), viewer_tz=viewer_tz)
        if today_row is not None else None
    )

    return {
        "journal_entries": views,
        "today_entry": today_entry,
        "journal_side_effects": list(JournalSideEffect),
        "today_doses": doses_for(session, viewer_uid, date.today()),
        "workout_day_options": _workout_day_options(session, viewer_uid),
        "today_iso": date.today().isoformat(),
    }


@router.post("/journal/entries")
async def save_journal_entry(request: Request, session: Session = Depends(get_session),
                             uid: int = Depends(current_user_id)):
    form = await request.form()

    def _raw(field: str) -> str:
        return str(form.get(field, "")).strip()

    errors: dict[str, str] = {}
    values: dict[str, int | None] = {}
    for field in RATING_FIELDS:
        raw = _raw(field)
        if not raw:
            values[field] = None
            continue
        try:
            value = int(raw)
        except ValueError:
            errors[field] = f"{field.replace('_', ' ').title()} must be a whole number between 1 and 5."
            continue
        if not 1 <= value <= 5:
            errors[field] = f"{field.replace('_', ' ').title()} must be between 1 and 5."
            continue
        values[field] = value

    posted_side_effects = [str(v) for v in form.getlist("side_effects")]
    valid_values = {se.value for se in JournalSideEffect}
    for v in posted_side_effects:
        if v not in valid_values:
            errors["side_effects"] = "Invalid side effect."

    if errors:
        # Re-render the Journal tab (same template/context as the normal GET), not a bare
        # HTTPException -- follows this app's established errors-dict-and-`err()`-macro convention
        # (see app/routers/measurements.py's create_measurement / _render).
        from app.routers import measurements  # deferred: measurements imports this module at load time
        return measurements._render(request, session, uid, tab="journal", errors=errors, status_code=422)

    entry = get_or_create_entry(session, uid, date.today())
    entry.mood = values["mood"]
    entry.energy = values["energy"]
    entry.sleep_quality = values["sleep_quality"]
    entry.side_effects_other = _raw("side_effects_other") or None
    entry.notes = _raw("notes") or None

    # Replace the child rows -- delete-then-recreate, matching this app's usual convention (e.g.
    # app/routers/protocols.py's save_protocol clearing p.goals/p.items before rebuilding them).
    entry.side_effects.clear()
    session.flush()
    entry.side_effects = [JournalEntrySideEffect(side_effect=JournalSideEffect(v)) for v in posted_side_effects]

    session.commit()
    return RedirectResponse("/measurements?tab=journal", status_code=303)


@router.post("/journal/quick-note")
async def add_quick_note(request: Request, session: Session = Depends(get_session),
                         uid: int = Depends(current_user_id)):
    form = await request.form()
    text = str(form.get("text", "")).strip()
    if not text:
        # Silent no-op (spec's "Quick-capture box" section) -- no blank timestamped row, and no
        # raw JSON error page; just bounce back to the Dashboard as if nothing was submitted.
        return RedirectResponse("/dashboard", status_code=303)

    entry = get_or_create_entry(session, uid, date.today())
    session.add(JournalQuickNote(entry_id=entry.id, text=text))
    session.commit()
    return RedirectResponse("/dashboard", status_code=303)

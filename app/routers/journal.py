from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.db import get_session
from app.models import JournalEntry, JournalEntrySideEffect, JournalSideEffect, Share, ShareCategory, User

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


def _entry_view(entry: JournalEntry, owner_name: str | None = None) -> dict:
    return {
        "date": entry.entry_date,
        "mood": entry.mood,
        "energy": entry.energy,
        "sleep_quality": entry.sleep_quality,
        "side_effects": [se.side_effect.value for se in entry.side_effects],
        "side_effects_other": entry.side_effects_other,
        "notes": entry.notes,
        "owner_name": owner_name,
    }


def journal_tab_context(session: Session, viewer_uid: int) -> dict:
    own_entries = session.scalars(
        _journal_query(viewer_uid).order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())).all()

    shared_entries = session.scalars(
        _shared_journal_query(viewer_uid).order_by(
            JournalEntry.entry_date.desc(), JournalEntry.id.desc())).all()
    owner_ids = {e.owner_id for e in shared_entries}
    owner_names = {}
    if owner_ids:
        owner_names = dict(session.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all())

    views = [_entry_view(e) for e in own_entries]
    views += [_entry_view(e, owner_name=owner_names.get(e.owner_id)) for e in shared_entries]
    views.sort(key=lambda v: v["date"], reverse=True)

    return {"entries": views, "journal_side_effects": list(JournalSideEffect)}


@router.post("/journal/entries")
async def save_journal_entry(request: Request, session: Session = Depends(get_session),
                             uid: int = Depends(current_user_id)):
    form = await request.form()

    def _raw(field: str) -> str:
        return str(form.get(field, "")).strip()

    values: dict[str, int | None] = {}
    for field in RATING_FIELDS:
        raw = _raw(field)
        if not raw:
            values[field] = None
            continue
        try:
            value = int(raw)
        except ValueError:
            raise HTTPException(422, f"{field} must be a whole number between 1 and 5.")
        if not 1 <= value <= 5:
            raise HTTPException(422, f"{field} must be between 1 and 5.")
        values[field] = value

    posted_side_effects = [str(v) for v in form.getlist("side_effects")]
    valid_values = {se.value for se in JournalSideEffect}
    for v in posted_side_effects:
        if v not in valid_values:
            raise HTTPException(422, "Invalid side effect.")

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

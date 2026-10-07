"""Library extras: reorder a goal's suggested stack, and each person's private notes and saved articles per peptide (Peptide Learning)."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.goals import GOALS, GOALS_BY_SLUG
from app.models import GoalPeptide, Peptide, PeptideNote
from app.templating import templates

router = APIRouter()


# ---------------------------------------------------------------- goal stacks

@router.get("/library/stacks")
def stacks_page(request: Request, session: Session = Depends(get_session)):
    names = {p.id: p.name for p in session.scalars(select(Peptide))}
    stacks = []
    for goal in GOALS:
        rows = session.scalars(select(GoalPeptide).where(GoalPeptide.goal == goal.slug).order_by(GoalPeptide.position, GoalPeptide.peptide_id)).all()
        stacks.append({"goal": goal, "entries": [(r.peptide_id, names.get(r.peptide_id, "?")) for r in rows]})
    return templates.TemplateResponse(request, "library/stacks.html", {"stacks": stacks})


@router.post("/library/stacks/{slug}/move")
async def move_in_stack(slug: str, request: Request, session: Session = Depends(get_session)):
    if slug not in GOALS_BY_SLUG:
        raise HTTPException(404, "Unknown goal")
    form = await request.form()
    direction = str(form.get("direction") or "")
    if direction not in ("up", "down"):
        raise HTTPException(422, "Direction must be up or down")
    try:
        peptide_id = int(str(form.get("peptide_id") or ""))
    except ValueError:
        raise HTTPException(404, "Peptide not found") from None
    rows = list(session.scalars(select(GoalPeptide).where(GoalPeptide.goal == slug).order_by(GoalPeptide.position, GoalPeptide.peptide_id)))
    index = next((i for i, r in enumerate(rows) if r.peptide_id == peptide_id), None)
    if index is None:
        raise HTTPException(404, "That peptide is not in this stack")
    swap = index - 1 if direction == "up" else index + 1
    if 0 <= swap < len(rows):
        rows[index], rows[swap] = rows[swap], rows[index]
    for position, row in enumerate(rows):                         # rewrite every position so ties and gaps never matter
        row.position = position
    session.commit()
    return RedirectResponse(f"/library/stacks#goal-{slug}", status_code=303)


# ---------------------------------------------------------------- learning notes

def _clean_link(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc or len(raw) > 500:
        raise ValueError("A web link must start with http:// or https://")
    return raw


@router.post("/library/{peptide_id}/notes")
async def add_note(peptide_id: int, request: Request, session: Session = Depends(get_session)):
    if session.get(Peptide, peptide_id) is None:
        raise HTTPException(404, "Peptide not found")
    form = await request.form()
    title, body = str(form.get("title") or "").strip(), str(form.get("body") or "").strip()
    if not title or len(title) > 200:
        raise HTTPException(422, "Give the note a title of up to 200 characters.")
    if len(body) > 10000:
        raise HTTPException(422, "A note can be at most 10,000 characters.")
    try:
        url = _clean_link(str(form.get("url") or ""))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    session.add(PeptideNote(owner_id=request.state.user.id, peptide_id=peptide_id, title=title, url=url, body=body or None))
    session.commit()
    return RedirectResponse(f"/library/{peptide_id}#learning", status_code=303)


@router.post("/library/notes/{note_id}/delete")
def delete_note(note_id: int, request: Request, session: Session = Depends(get_session)):
    note = session.get(PeptideNote, note_id)
    if note is None or note.owner_id != request.state.user.id:
        raise HTTPException(404, "Note not found")
    peptide_id = note.peptide_id
    session.delete(note)
    session.commit()
    return RedirectResponse(f"/library/{peptide_id}#learning", status_code=303)


@router.get("/library/learning")
def learning_page(request: Request, q: str = "", session: Session = Depends(get_session)):
    notes = session.scalars(select(PeptideNote).where(PeptideNote.owner_id == request.state.user.id).order_by(PeptideNote.created_at.desc(), PeptideNote.id.desc())).all()
    names = {p.id: p.name for p in session.scalars(select(Peptide).where(Peptide.id.in_({n.peptide_id for n in notes} or {0})))}
    needle = q.strip().casefold()
    shown = [n for n in notes if not needle or needle in " ".join([n.title, n.body or "", n.url or "", names.get(n.peptide_id, "")]).casefold()]
    return templates.TemplateResponse(request, "library/learning.html", {"notes": shown, "names": names, "q": q, "total": len(notes)})

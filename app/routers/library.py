import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.auth.deps import current_user_id
from app.db import get_session
from app.calculator import units as unit_math
from app.goals import GOALS
from app.library.price_lists.analysis import price_range, rematch_items
from app.library.price_lists.vendor_view import build_price_compare
from app.library.forms import parse_peptide_form, state_from_form, state_from_peptide
from app.models import DoseLog, DoseUnit, DosingTierLevel, GoalPeptide, Peptide, PeptideNote, PeptideSource, Protocol, ProtocolItem, User
from app.templating import templates

router = APIRouter()

# Card images are written by tools/import_cards.py as NNN.jpg; nothing else is ever served.
_CARD_IMAGE = re.compile(r"^\d{3}\.jpg$")


def _get_peptide(session: Session, peptide_id: int) -> Peptide:
    p = session.get(Peptide, peptide_id)
    if p is None:
        raise HTTPException(404, "Peptide not found")
    return p


def _goal_map(session: Session) -> dict[int, list[str]]:
    """peptide id -> goal slugs (in the order goals are listed)."""
    order = {g.slug: i for i, g in enumerate(GOALS)}
    out: dict[int, list[str]] = {}
    for gp in session.scalars(select(GoalPeptide)):
        out.setdefault(gp.peptide_id, []).append(gp.goal)
    return {pid: sorted(goals, key=lambda g: order.get(g, 99)) for pid, goals in out.items()}


def evidence_class(level: str | None) -> str:
    """CSS modifier for an evidence level tag."""
    text = (level or "").lower()
    if text.startswith("high"):
        return "ev-high"
    if text.startswith("moderate") or text.startswith("low to moderate"):
        return "ev-moderate"
    if text.startswith("low") or text.startswith("very low"):
        return "ev-low"
    return "ev-none"


templates.env.filters["evidence_class"] = evidence_class

_TIER_ORDER = {DosingTierLevel.BEGINNER: 0, DosingTierLevel.INTERMEDIATE: 1, DosingTierLevel.ADVANCED: 2}


# ---------------------------------------------------------------- pages

@router.get("/library")
def library_list(request: Request, session: Session = Depends(get_session)):
    peptides = session.scalars(select(Peptide).order_by(Peptide.name)).all()
    return templates.TemplateResponse(request, "library/list.html", {
        "peptides": peptides, "goals": GOALS, "goal_map": _goal_map(session),
        "added_sources": {PeptideSource.STARTER, PeptideSource.CUSTOM},
    })


@router.post("/library/{peptide_id}/delete")
def delete_library_entry(peptide_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Delete a library entry a person added. The library is shared, so only the administrator can; a shipped card cannot be deleted; and an
    entry that a protocol or dose log still uses is kept (the person is told what uses it)."""
    p = _get_peptide(session, peptide_id)
    me = session.get(User, uid)
    if not me.is_admin or p.source not in (PeptideSource.CUSTOM, PeptideSource.STARTER):
        raise HTTPException(403, "Only the administrator can delete a library entry that a person added")
    in_use = (session.scalar(select(ProtocolItem.id).where(ProtocolItem.peptide_id == p.id).limit(1)) is not None
              or session.scalar(select(DoseLog.id).where(DoseLog.peptide_id == p.id).limit(1)) is not None)
    if in_use:
        return RedirectResponse(f"/library/{p.id}?delete_blocked=1", status_code=303)
    session.delete(p)
    session.commit()
    return RedirectResponse("/library", status_code=303)


@router.get("/library/{peptide_id}")
def library_detail(peptide_id: int, request: Request, session: Session = Depends(get_session)):
    p = _get_peptide(session, peptide_id)
    used_in = session.scalars(
        select(Protocol).join(ProtocolItem)
        .where(ProtocolItem.peptide_id == p.id, Protocol.owner_id == request.state.user.id)
        .order_by(Protocol.start_date.desc()).distinct()).all()
    dosing_tiers = sorted(p.dosing_tiers, key=lambda t: _TIER_ORDER.get(t.level, 99))
    shown_range = price_range(session, p.id)
    calc_urls = {}
    for t in dosing_tiers:                           # a tier whose dose is a plain amount (250mcg, 1.5 mg, 3 IU) opens the calculator with it filled in
        parsed = unit_math.parse_dose_text(t.dose_text)
        if parsed:
            calc_urls[t.level] = f"/calculator?dose_value={parsed[0]:g}&dose_unit={parsed[1]}"
    my_notes = session.scalars(select(PeptideNote).where(PeptideNote.owner_id == request.state.user.id, PeptideNote.peptide_id == p.id)
                               .order_by(PeptideNote.created_at.desc(), PeptideNote.id.desc())).all()
    return templates.TemplateResponse(request, "library/detail.html", {
        "calc_urls": calc_urls, "my_notes": my_notes,
        "p": p, "card": p.card_details or {}, "goals": _goal_map(session).get(p.id, []), "used_in": used_in,
        "can_delete": bool(session.get(User, request.state.user.id).is_admin) and p.source in (PeptideSource.CUSTOM, PeptideSource.STARTER),
        "protocols_using": session.scalar(select(func.count(func.distinct(ProtocolItem.protocol_id))).where(ProtocolItem.peptide_id == p.id)) or 0,
        "dosing_tiers": dosing_tiers, "price_range": shown_range,
        "price_compare": build_price_compare(session, p.id, shown_range),
    })


def _render_edit(request: Request, p: Peptide, state: dict, errors: dict | None = None, status_code: int = 200):
    return templates.TemplateResponse(request, "library/edit.html", {
        "p": p, "f": state, "errors": errors or {}, "goals": GOALS, "units": list(DoseUnit),
    }, status_code=status_code)


@router.get("/library/{peptide_id}/edit")
def library_edit(peptide_id: int, request: Request, session: Session = Depends(get_session)):
    p = _get_peptide(session, peptide_id)
    return _render_edit(request, p, state_from_peptide(p, _goal_map(session).get(p.id, [])))


@router.post("/library/{peptide_id}")
async def library_update(peptide_id: int, request: Request, session: Session = Depends(get_session)):
    p = _get_peptide(session, peptide_id)
    form = await request.form()
    state = state_from_form({k: [str(v) for v in form.getlist(k)] for k in form.keys()})
    values, errors = parse_peptide_form(state)
    if errors:
        return _render_edit(request, p, state, errors, status_code=422)

    goals = values.pop("goals")
    for key, value in values.items():
        setattr(p, key, value)

    # Goal stacks: remove unticked goals; append newly ticked ones at the end of that goal's stack.
    current = {gp.goal: gp for gp in session.scalars(select(GoalPeptide).where(GoalPeptide.peptide_id == p.id))}
    for goal, gp in current.items():
        if goal not in goals:
            session.delete(gp)
    for goal in goals:
        if goal not in current:
            last = session.scalar(select(func.max(GoalPeptide.position)).where(GoalPeptide.goal == goal))
            session.add(GoalPeptide(goal=goal, peptide_id=p.id, position=(last if last is not None else -1) + 1))
    rematch_items(session)
    session.commit()
    return RedirectResponse(f"/library/{p.id}", status_code=303)


@router.get("/library/{peptide_id}/card")
def library_card_image(peptide_id: int, session: Session = Depends(get_session)):
    p = _get_peptide(session, peptide_id)
    if not p.card_image or not _CARD_IMAGE.match(p.card_image):
        raise HTTPException(404, "No card image")
    path = config.CARDS_DIR / p.card_image
    if not path.is_file():
        raise HTTPException(404, "Card image missing from disk")
    return FileResponse(path, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff"})

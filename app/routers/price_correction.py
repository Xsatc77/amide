"""Price correction (administrator only): which library card each vendor product name is matched to, with the means to correct it and to add the cards that are missing."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.library.price_lists import corrections
from app.models import User
from app.templating import templates

router = APIRouter()
URL = "/settings/price-correction"
MAX_FIELDS = 20000


def _admin(session: Session, uid: int) -> User:
    me = session.get(User, uid)
    if me is None or not me.is_admin:
        raise HTTPException(status_code=404)
    return me


def _page(request: Request, session: Session, *, saved: int | None = None, error: str = "", code: int = 200):
    groups = corrections.product_groups(session)
    return templates.TemplateResponse(request, "settings/price_correction.html", {
        "groups": groups, "peptides": corrections.peptide_options(session), "saved": saved, "error": error,
        "unmatched": sum(1 for g in groups if g.peptide_id is None),
    }, status_code=code)


def _by_prefix(form, prefix: str) -> dict[str, str]:
    return {k[len(prefix):]: str(v) for k, v in form.items() if k.startswith(prefix)}


@router.get(URL)
def price_correction(request: Request, saved: int | None = None, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    _admin(session, uid)
    return _page(request, session, saved=saved)


@router.post(URL)
async def save_corrections(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Commit the whole list at once. Anything chosen "Add as a new peptide" is then shown for inclusion."""
    _admin(session, uid)
    form = await request.form(max_fields=MAX_FIELDS)
    try:
        result = corrections.apply_changes(session, _by_prefix(form, "match__"))
    except corrections.CorrectionError as exc:
        session.rollback()
        return _page(request, session, error=str(exc), code=422)
    session.commit()
    candidates = corrections.add_candidates(session, result.to_add)
    if result.to_add:
        return templates.TemplateResponse(request, "settings/price_correction_add.html", {"candidates": candidates, "saved": result.saved, "error": ""})
    return RedirectResponse(f"{URL}?saved={result.saved}", status_code=303)


@router.post(URL + "/add")
async def add_peptides(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    _admin(session, uid)
    form = await request.form(max_fields=MAX_FIELDS)
    names = _by_prefix(form, "name__")
    picks = {key: names.get(key, "") for key in _by_prefix(form, "add__")}
    try:
        corrections.add_peptides(session, picks)
    except corrections.CorrectionError as exc:
        session.rollback()
        candidates = corrections.add_candidates(session, list(picks))
        return templates.TemplateResponse(request, "settings/price_correction_add.html", {"candidates": candidates, "saved": 0, "error": str(exc)}, status_code=422)
    session.commit()
    return RedirectResponse(f"{URL}?saved={len(picks)}", status_code=303)

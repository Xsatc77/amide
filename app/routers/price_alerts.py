from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.library.price_lists.analysis import ignore_product

router = APIRouter()


@router.post("/price-alerts/ignore")
async def ignore_new_peptide(request: Request, session: Session = Depends(get_session)):
    """Marks a product "not a peptide" so it stops raising a NEW PEPTIDE ALERT. The price data is shared by
    everyone on the installation, so only an administrator may do this (404 for anyone else)."""
    if not request.state.user.is_admin:
        raise HTTPException(404)
    name = str((await request.form()).get("name") or "").strip()
    if name:
        ignore_product(session, name)
        session.commit()
    return RedirectResponse("/dashboard", status_code=303)

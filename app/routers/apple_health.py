"""Apple Health: the person's own tokens for the Health Auto Export app (the receiver and the settings card come later)."""

import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.health import choices
from app.ingest import tokens
from app.models import IngestToken

router = APIRouter()
BACK = "/settings#integrations"

# A new token's secret is kept here for one look, per browser session: the redirect target reads it and it is gone.
_new_tokens: dict[str, tuple[str, str]] = {}
_lock = threading.Lock()


def take_new_token(session_id: str | None) -> tuple[str, str] | None:
    """(secret, label) of the token this browser just created, once; None afterwards."""
    if not session_id:
        return None
    with _lock:
        return _new_tokens.pop(session_id, None)


@router.post("/settings/apple-health/token")
async def create_token(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    token, secret = tokens.create_token(session, uid, str(form.get("label") or ""), scope="health")
    if request.state.session_id:
        with _lock:
            _new_tokens[request.state.session_id] = (secret, token.label)
    return RedirectResponse(BACK, status_code=303)


@router.post("/settings/apple-health/token/{token_id}/revoke")
def revoke_token(token_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    token = session.scalar(select(IngestToken).where(IngestToken.id == token_id, IngestToken.owner_id == uid, IngestToken.scope == "health"))
    if token is not None:
        tokens.revoke_token(session, token.id)      # someone else's token is left alone, and the answer is the same
    return RedirectResponse(BACK, status_code=303)


@router.post("/settings/apple-health/choices")
async def save_choices(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    choices.save_choices(session, uid, {str(v) for v in form.getlist("metric")}, {str(v) for v in form.getlist("offered")})
    return RedirectResponse(BACK, status_code=303)

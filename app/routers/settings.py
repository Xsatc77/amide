"""The /settings hub: User, Display, Integrations, Admin-if-admin sections."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import passwords, sessions
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import Colorway, User
from app.settings.rules import TIMEZONES, email_error, timezone_error
from app.templating import templates

router = APIRouter()


def _me(session: Session, uid: int) -> User:
    return session.get(User, uid)


def _render(request: Request, session: Session, *, errors: dict | None = None, status_code: int = 200):
    me = _me(session, request.state.user.id)
    return templates.TemplateResponse(request, "settings/settings.html", {
        "me": me, "errors": errors or {}, "timezones": TIMEZONES, "colorways": list(Colorway),
    }, status_code=status_code)


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(get_session)):
    return _render(request, session)


@router.post("/settings/username")
async def change_username(request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    form = await request.form()
    new_name = str(form.get("username", "")).strip()
    current_password = str(form.get("current_password", ""))
    me = _me(session, uid)
    errors: dict[str, str] = {}

    if not passwords.verify_password(me.password_hash, current_password):
        errors["current_password"] = "Current password is incorrect."
    if err := passwords.username_error(new_name):
        errors["username"] = err
    elif not errors:
        key = passwords.username_key(new_name)
        clash = session.scalar(select(User).where(User.username_key == key, User.id != me.id))
        if clash:
            errors["username"] = "That username is already taken."

    if errors:
        return _render(request, session, errors=errors, status_code=422)

    me.username, me.username_key = new_name, passwords.username_key(new_name)
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/password")
async def change_password(request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    form = await request.form()
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm = str(form.get("confirm", ""))
    me = _me(session, uid)

    errors: dict[str, str] = {}
    if not passwords.verify_password(me.password_hash, current_password):
        errors["current_password"] = "Current password is incorrect."
    if problems := passwords.password_errors(new_password, confirm):
        errors["new_password"] = " ".join(problems)
    if errors:
        return _render(request, session, errors=errors, status_code=422)

    me.password_hash = passwords.hash_password(new_password)
    session.commit()
    sessions.end_other_sessions(session, me.id, request.state.session_id)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/timezone")
async def change_timezone(request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    form = await request.form()
    mode = str(form.get("mode", "system"))
    tz = str(form.get("timezone", "")).strip() if mode == "manual" else ""

    if err := timezone_error(tz):
        return _render(request, session, errors={"timezone": err}, status_code=422)

    _me(session, uid).timezone = tz or None
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/email")
async def change_email(request: Request, session: Session = Depends(get_session),
                       uid: int = Depends(current_user_id)):
    form = await request.form()
    value = str(form.get("email", "")).strip()

    if err := email_error(value):
        return _render(request, session, errors={"email": err}, status_code=422)

    _me(session, uid).email = value or None
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/display")
async def change_display(request: Request, session: Session = Depends(get_session),
                         uid: int = Depends(current_user_id)):
    form = await request.form()
    value = str(form.get("colorway", "")).strip()

    colorway = None
    if value:
        try:
            colorway = Colorway(value)
        except ValueError:
            return _render(request, session, errors={"colorway": "Pick a colorway from the list."},
                          status_code=422)

    _me(session, uid).colorway = colorway
    session.commit()
    return RedirectResponse("/settings", status_code=303)

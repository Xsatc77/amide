"""The /settings hub: User, Display, Integrations, Admin-if-admin sections."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import zoneinfo
from datetime import timezone

from app import uploads
from app.auth import passwords, sessions
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import Colorway, InventoryItem, Protocol, User, Vendor
from app.settings.rules import TIMEZONES, email_error, timezone_error
from app.templating import templates
from app.users import user_rows

router = APIRouter()


def _me(session: Session, uid: int) -> User:
    return session.get(User, uid)


def _format_last_login(last_login, tz_name: str | None) -> str:
    if last_login is None:
        return "Never"
    if tz_name:
        aware = last_login.replace(tzinfo=timezone.utc)
        local = aware.astimezone(zoneinfo.ZoneInfo(tz_name))
        return f"{local.strftime('%Y-%m-%d %H:%M')} {tz_name}"
    return last_login.strftime("%Y-%m-%d %H:%M UTC")


def _render(request: Request, session: Session, *, errors: dict | None = None, status_code: int = 200):
    me = _me(session, request.state.user.id)
    context = {
        "me": me, "errors": errors or {}, "timezones": TIMEZONES, "colorways": list(Colorway),
    }
    if me.is_admin:
        users = user_rows(session)
        for row in users:
            row["last_login_display"] = _format_last_login(row["last_login"], me.timezone)
        context["users"] = users
    return templates.TemplateResponse(request, "settings/settings.html", context, status_code=status_code)


def _require_admin(request: Request, session: Session) -> User:
    me = _me(session, request.state.user.id)
    if not me.is_admin:
        raise HTTPException(status_code=404)
    return me


def _target_user(request: Request, session: Session, user_id: int) -> User:
    me = _require_admin(request, session)
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    if target.id == me.id:
        raise HTTPException(status_code=422, detail="You can't do that to your own account.")
    return target


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
        errors["username_current_password"] = "Current password is incorrect."
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
        errors["password_current_password"] = "Current password is incorrect."
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
    return RedirectResponse("/settings#display", status_code=303)


@router.post("/settings/admin/users/new")
async def admin_add_user(request: Request, session: Session = Depends(get_session)):
    _require_admin(request, session)
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm", ""))

    errors: dict[str, str] = {}
    if err := passwords.username_error(username):
        errors["new_username"] = err
    elif session.scalar(select(User).where(User.username_key == passwords.username_key(username))):
        errors["new_username"] = "That username is already taken."
    if problems := passwords.password_errors(password, confirm):
        errors["new_user_password"] = " ".join(problems)

    if errors:
        return _render(request, session, errors=errors, status_code=422)

    session.add(User(username=username, username_key=passwords.username_key(username),
                     password_hash=passwords.hash_password(password)))
    session.commit()
    return RedirectResponse("/settings#admin", status_code=303)


@router.post("/settings/admin/users/{user_id}/reset-password")
async def admin_reset_password(user_id: int, request: Request, session: Session = Depends(get_session)):
    target = _target_user(request, session, user_id)
    form = await request.form()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm", ""))

    if problems := passwords.password_errors(password, confirm):
        return _render(request, session, errors={"reset_password": " ".join(problems)}, status_code=422)

    target.password_hash = passwords.hash_password(password)
    sessions.clear_failures(target)
    session.commit()
    sessions.end_all_sessions(session, target.id)
    return RedirectResponse("/settings#admin", status_code=303)


@router.post("/settings/admin/users/{user_id}/remove-2fa")
async def admin_remove_2fa(user_id: int, request: Request, session: Session = Depends(get_session)):
    target = _target_user(request, session, user_id)
    target.totp_enabled, target.totp_secret, target.totp_last_step = False, None, None
    session.commit()
    return RedirectResponse("/settings#admin", status_code=303)


@router.post("/settings/admin/users/{user_id}/delete")
async def admin_delete_user(user_id: int, request: Request, session: Session = Depends(get_session)):
    me = _require_admin(request, session)
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    confirm_name = str(form.get("username", ""))

    if target.id == me.id:
        return _render(request, session, errors={"delete_user": "You can't delete your own account."},
                      status_code=422)
    if confirm_name != target.username:
        return _render(request, session, errors={"delete_user": "Type the username exactly to confirm."},
                      status_code=422)

    coa_filenames = []
    for item in session.scalars(select(InventoryItem).where(InventoryItem.owner_id == target.id)):
        if item.coa_filename:
            coa_filenames.append(item.coa_filename)
        session.delete(item)
    for protocol in session.scalars(select(Protocol).where(Protocol.owner_id == target.id)):
        session.delete(protocol)
    for vendor in session.scalars(select(Vendor).where(Vendor.owner_id == target.id)):
        session.delete(vendor)
    # Flush the owned rows' deletes first: nothing links User to these tables via an ORM relationship,
    # so SQLAlchemy's flush ordering doesn't know they must precede the user row, and SQLite's
    # per-statement FK check rejects deleting the user while a stale owner_id reference still exists.
    session.flush()
    session.delete(target)
    session.commit()
    for filename in coa_filenames:
        uploads.delete_coa(filename)
    return RedirectResponse("/settings#admin", status_code=303)

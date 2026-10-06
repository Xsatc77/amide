"""The /settings hub: User, Display, Integrations, Admin-if-admin sections."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import zoneinfo
from datetime import date, timezone

from app import uploads
from app.auth import passwords, sessions
from app.auth.deps import current_user_id
from app.db import get_session
from app.measurements.tdee import LIFE_STAGES
from app.measurements.calculations import macros_for_preset
from app.models import (
    ActivityLevel, BiologicalSex, Colorway, DietPreset, InventoryItem, LabPanel, MacroGoal, Order,
    Protocol, Share, ShareCategory, User, Vendor,
)
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
    other_users = session.scalars(
        select(User).where(User.id != me.id).order_by(User.username.collate("NOCASE"))).all()
    my_shares = {
        (s.grantee_id, s.category.value)
        for s in session.scalars(select(Share).where(Share.owner_id == me.id))
    }
    context = {
        "me": me, "errors": errors or {}, "timezones": TIMEZONES, "colorways": list(Colorway),
        "other_users": other_users, "my_shares": my_shares,
        "sexes": list(BiologicalSex), "activity_levels": list(ActivityLevel),
        "macro_goals": list(MacroGoal), "diet_presets": list(DietPreset), "life_stages": LIFE_STAGES,
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


@router.post("/settings/discard-window")
async def change_discard_window(request: Request, session: Session = Depends(get_session),
                                uid: int = Depends(current_user_id)):
    form = await request.form()
    raw = str(form.get("default_discard_days", "")).strip()
    try:
        days = int(raw)
        if days <= 0:
            raise ValueError
    except ValueError:
        return _render(request, session, errors={"default_discard_days": "Enter a whole number of days, greater than 0."},
                      status_code=422)

    _me(session, uid).default_discard_days = days
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/dashboard-thresholds")
async def change_dashboard_thresholds(request: Request, session: Session = Depends(get_session),
                                      uid: int = Depends(current_user_id)):
    form = await request.form()
    errors: dict[str, str] = {}
    parsed: dict[str, int] = {}
    for field in ("low_stock_default", "shipment_delay_days"):
        raw = str(form.get(field, "")).strip()
        try:
            value = int(raw)
            if value <= 0:
                raise ValueError
            parsed[field] = value
        except ValueError:
            errors[field] = "Enter a whole number of days, greater than 0."

    if errors:
        return _render(request, session, errors=errors, status_code=422)

    me = _me(session, uid)
    me.low_stock_default = parsed["low_stock_default"]
    me.shipment_delay_days = parsed["shipment_delay_days"]
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/body-profile")
async def change_body_profile(request: Request, session: Session = Depends(get_session),
                              uid: int = Depends(current_user_id)):
    form = await request.form()
    errors: dict[str, str] = {}

    def _raw(field: str) -> str:
        return str(form.get(field, "")).strip()

    def _parse_enum(field: str, enum_cls):
        raw = _raw(field)
        if not raw:
            return None
        try:
            return enum_cls(raw)
        except ValueError:
            errors[field] = "Pick a value from the list."
            return None

    def _parse_float(field: str):
        raw = _raw(field)
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            errors[field] = "Enter a number."
            return None

    def _parse_int(field: str):
        raw = _raw(field)
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            errors[field] = "Enter a whole number."
            return None

    sex = _parse_enum("sex", BiologicalSex)
    activity_level = _parse_enum("activity_level", ActivityLevel)
    macro_goal = _parse_enum("macro_goal", MacroGoal)
    diet_preset = _parse_enum("diet_preset", DietPreset)
    life_stage = _raw("life_stage") or None
    if life_stage is not None and life_stage not in LIFE_STAGES:
        errors["life_stage"] = "Pick a value from the list."

    birth_date = None
    raw_birth_date = _raw("birth_date")
    if raw_birth_date:
        try:
            birth_date = date.fromisoformat(raw_birth_date)
        except ValueError:
            errors["birth_date"] = "Enter a valid date."

    height_in = _parse_float("height_in")
    water_goal_oz = _parse_int("water_goal_oz")
    custom_protein_pct = _parse_int("custom_protein_pct")
    custom_carb_pct = _parse_int("custom_carb_pct")
    custom_fat_pct = _parse_int("custom_fat_pct")

    # Mirror the positivity-check convention used elsewhere in this file (e.g.
    # change_discard_window/change_dashboard_thresholds): optional numeric fields must be > 0 when
    # present, never silently accepted as 0/negative and left to crash a later BMI/body-fat
    # calculation (Review Focus item 1) or display as a nonsensical "-5 oz/day" (item 4).
    if height_in is not None and height_in <= 0 and "height_in" not in errors:
        errors["height_in"] = "Enter a number greater than 0."
    if water_goal_oz is not None and water_goal_oz <= 0 and "water_goal_oz" not in errors:
        errors["water_goal_oz"] = "Enter a whole number greater than 0."

    # Each custom macro percentage must independently be a valid percentage -- the sum-to-100
    # check below doesn't catch e.g. 150/-30/-20 (sums to 100 but negative carb/fat grams).
    for field, value in (("custom_protein_pct", custom_protein_pct),
                         ("custom_carb_pct", custom_carb_pct),
                         ("custom_fat_pct", custom_fat_pct)):
        if value is not None and not (0 <= value <= 100) and field not in errors:
            errors[field] = "Enter a percentage between 0 and 100."

    if birth_date is not None and birth_date > date.today() and "birth_date" not in errors:
        errors["birth_date"] = "Birth date can't be in the future."

    # Reuse Task 2's macros_for_preset validation (custom pcts required + must sum to 100) instead
    # of re-implementing the same rule here -- the dummy calorie value is discarded, only the
    # ValueError matters.
    if diet_preset == DietPreset.CUSTOM and not errors:
        custom = None
        if None not in (custom_protein_pct, custom_carb_pct, custom_fat_pct):
            custom = (custom_protein_pct, custom_carb_pct, custom_fat_pct)
        try:
            macros_for_preset(2000, DietPreset.CUSTOM, custom)
        except ValueError as e:
            errors["diet_preset"] = str(e)

    if errors:
        return _render(request, session, errors=errors, status_code=422)

    me = _me(session, uid)
    me.sex = sex
    me.birth_date = birth_date
    me.height_in = height_in
    me.activity_level = activity_level
    me.macro_goal = macro_goal
    me.diet_preset = diet_preset
    me.life_stage = life_stage
    me.custom_protein_pct = custom_protein_pct
    me.custom_carb_pct = custom_carb_pct
    me.custom_fat_pct = custom_fat_pct
    me.water_goal_oz = water_goal_oz
    session.commit()
    return RedirectResponse("/settings#user", status_code=303)


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


@router.post("/settings/sharing/{grantee_id}/{category}")
async def toggle_share(grantee_id: int, category: str, request: Request,
                       session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    try:
        cat = ShareCategory(category)
    except ValueError:
        raise HTTPException(status_code=422)
    if grantee_id == uid or session.get(User, grantee_id) is None:
        raise HTTPException(status_code=404)

    form = await request.form()
    # The button posts the state it wants, not "flip whatever it is now" -- a double-click, a
    # stale second tab, or a back-button resubmit must converge to the same state, never flip an
    # already-applied change back.
    want_on = str(form.get("on", "1")) == "1"

    existing = session.scalar(select(Share).where(
        Share.owner_id == uid, Share.grantee_id == grantee_id, Share.category == cat))
    if want_on and existing is None:
        session.add(Share(owner_id=uid, grantee_id=grantee_id, category=cat))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()  # a concurrent request already granted this -- already in the state we want
    elif not want_on and existing is not None:
        session.delete(existing)
        session.commit()
    return RedirectResponse("/settings#sharing", status_code=303)


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
    order_ids = set()
    for item in session.scalars(select(InventoryItem).where(InventoryItem.owner_id == target.id)):
        for li in item.order_items:
            if li.coa_filename:
                coa_filenames.append(li.coa_filename)
            order_ids.add(li.order_id)
        session.delete(item)
    # Own lab panels only -- this is about deleting THEIR data, not a sharing partner's panels this
    # user could merely view. Filenames are collected up front (same as coa_filenames above) so the
    # files can be unlinked from disk after the DB rows/user are gone, mirroring the COA pattern below.
    report_filenames = []
    for panel in session.scalars(select(LabPanel).where(LabPanel.owner_id == target.id)):
        if panel.report_filename:
            report_filenames.append(panel.report_filename)
        session.delete(panel)
    for protocol in session.scalars(select(Protocol).where(Protocol.owner_id == target.id)):
        session.delete(protocol)
    # Vendors are a shared resource now (see Vendor's docstring) -- keep the row, just clear
    # provenance. Deleting it would break other users' inventory items still pointing at it.
    for vendor in session.scalars(select(Vendor).where(Vendor.created_by_id == target.id)):
        vendor.created_by_id = None
    for share in session.scalars(select(Share).where(
            (Share.owner_id == target.id) | (Share.grantee_id == target.id))):
        session.delete(share)
    # Flush the owned rows' deletes first: nothing links User to these tables via an ORM relationship,
    # so SQLAlchemy's flush ordering doesn't know they must precede the user row, and SQLite's
    # per-statement FK check rejects deleting the user while a stale owner_id reference still exists.
    session.flush()
    for order_id in order_ids:
        order = session.get(Order, order_id)
        if order is not None and not order.items:
            session.delete(order)
    session.delete(target)
    session.commit()
    for filename in coa_filenames:
        uploads.delete_coa(filename)
    for filename in report_filenames:
        uploads.delete_lab_report(filename)
    return RedirectResponse("/settings#admin", status_code=303)

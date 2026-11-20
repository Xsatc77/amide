"""The /settings hub: User, Display, Integrations, Admin-if-admin sections."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import re
import secrets
import zoneinfo
from datetime import date, timezone

from app import version
from app import clock
from app import ai_summary, body_photos, config, links, reminders, units, uploads
from app.auth import audit, devices, passwords, registration, sessions
from app.auth.deps import current_user_id
from app.db import get_session
from app.measurements.tdee import LIFE_STAGES
from app.measurements.calculations import macros_for_preset
from app.inventory import avery94500
from app.inventory.labels import LABEL_SIZES
from app.inventory.label_layout import LayoutError, parse_layout
from app.shopping.build import parse_fee, saved_shipping
from app.models import (
    ActivityLevel, BiologicalSex, BodyPhoto, Colorway, Food, FoodLog, DietPreset, InventoryItem, LabPanel, MacroGoal, Order,
    Protocol, Share, ShareCategory, User, Vendor,
)
from app.models import UserMedicine, naive_utcnow
from app.settings.rules import TIMEZONES, email_error, timezone_error
from app.routers import apple_health
from app.templating import templates
from app.users import user_rows

router = APIRouter()


def _me(session: Session, uid: int) -> User:
    return session.get(User, uid)


async def _current_password_problem(request: Request, session: Session, me: User, password: str) -> str | None:
    """Check the 'current password' typed into a Settings form through the same lockout as sign-in: refused while the account is
    locked, a wrong one counts as a failed attempt (and can lock) and is logged (never the password). None when it is right."""
    now = sessions.now_utc()
    if sessions.is_locked(me, now):
        minutes = max(1, int((me.locked_until - now).total_seconds() // 60) + 1)
        return f"Too many attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''}."
    if await passwords.verify_password_async(me.password_hash, password):
        if me.failed_attempts:
            sessions.clear_failures(me)
            session.commit()
        return None
    sessions.record_failure(me, now)
    audit.record(session, "password_reauth_failed", request, me)
    if sessions.is_locked(me, now):
        audit.record(session, "lockout", request, me, f"locked for {config.LOCKOUT_MINUTES} minutes")
    session.commit()
    return "Current password is incorrect."


def _format_last_login(last_login, tz_name: str | None) -> str:
    if last_login is None:
        return "Never"
    if tz_name:
        aware = last_login.replace(tzinfo=timezone.utc)
        local = aware.astimezone(zoneinfo.ZoneInfo(tz_name))
        return f"{local.strftime('%m/%d/%Y %H:%M')} {tz_name}"
    return last_login.strftime("%m/%d/%Y %H:%M UTC")


def _review_fields():
    """The Progress Review thresholds as the form shows them: settings column, label, whole numbers only, limits, meaning, the review's own default."""
    from app.progress.review import RULE_FIELDS, Rules
    return [(column, label, whole, low, high, help_, getattr(Rules(), attr)) for column, attr, label, whole, low, high, help_ in RULE_FIELDS]


def _render(request: Request, session: Session, *, errors: dict | None = None, status_code: int = 200):
    me = _me(session, request.state.user.id)
    other_users = session.scalars(
        select(User).where(User.id != me.id).order_by(User.username.collate("NOCASE"))).all()
    my_shares = {                                           # (grantee, category) -> True once accepted, False while pending
        (s.grantee_id, s.category.value): s.accepted_at is not None
        for s in session.scalars(select(Share).where(Share.owner_id == me.id))
    }
    pending_for_me: list[dict] = []                        # offers waiting for my answer: they grant nothing until I accept
    shared_with_me: dict[int, dict] = {}                    # who shares data WITH me: username only, never an id-bearing profile
    for share, owner_name in session.execute(
            select(Share, User.username).join(User, User.id == Share.owner_id)
            .where(Share.grantee_id == me.id).order_by(User.username.collate("NOCASE"), Share.category)):
        if share.accepted_at is None:
            pending_for_me.append({"owner_id": share.owner_id, "username": owner_name, "category": share.category})
            continue
        entry = shared_with_me.setdefault(share.owner_id, {"owner_id": share.owner_id, "username": owner_name, "categories": []})
        entry["categories"].append(share.category)
    context = {
        "shared_with_me": list(shared_with_me.values()), "pending_for_me": pending_for_me,
        "me": me, "errors": errors or {}, "timezones": TIMEZONES, "colorways": list(Colorway),
        "other_users": other_users, "my_shares": my_shares,
        "sexes": list(BiologicalSex), "activity_levels": list(ActivityLevel),
        "macro_goals": list(MacroGoal), "diet_presets": list(DietPreset), "life_stages": LIFE_STAGES,
        "shipping": saved_shipping(me), "unit_choices": units.CHOICES,
        "medicines": session.scalars(select(UserMedicine).where(UserMedicine.owner_id == me.id)
                                     .order_by(UserMedicine.name.collate("NOCASE"))).all(),
        "label_sizes": {k: v[0] for k, v in LABEL_SIZES.items()},
        "db_revision": version.database_revision(session),
        "review_fields": _review_fields(),
        "ntfy_suggestion": secrets.token_urlsafe(18), "ntfy_short": bool(me.ntfy_topic and len(me.ntfy_topic) < 12),
        "ntfy_server": config.NTFY_SERVER, "ai_providers": ai_summary.PROVIDERS,
        "auto_backup_on": bool(config.BACKUP_PASSPHRASE), "auto_backup_days": config.BACKUP_EVERY_DAYS, "auto_backup_keep": config.BACKUP_KEEP,
        "calendar_feed_url": f"{str(request.base_url).rstrip('/')}/calendar/feed/{me.calendar_token}.ics" if me.calendar_token else None,
    }
    shown = apple_health.take_new_token(request.state.session_id)
    context["health"] = apple_health.card_data(session, me.id, str(request.base_url), shown[0] if shown else None)
    context["health"]["new_label"] = shown[1] if shown else None
    if me.is_admin:
        users = user_rows(session)
        for row in users:
            row["last_login_display"] = _format_last_login(row["last_login"], me.timezone)
        context["users"] = users
        context["registration"] = {"open": registration.is_open(session), "choice": registration.admin_choice(session),
                                   "forced": config.registration_override()}
        context["security_events"] = audit.recent(session, 50)
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

    if problem := await _current_password_problem(request, session, me, current_password):
        errors["username_current_password"] = problem
    if err := passwords.username_error(new_name):
        errors["username"] = err
    elif not errors:
        key = passwords.username_key(new_name)
        clash = session.scalar(select(User).where(User.username_key == key, User.id != me.id))
        if clash:
            errors["username"] = "That username is already taken."

    if errors:
        return _render(request, session, errors=errors, status_code=422)

    old_name = me.username
    me.username, me.username_key = new_name, passwords.username_key(new_name)
    audit.record(session, "username_changed", request, me, f"from {old_name} to {new_name}")
    session.commit()
    sessions.end_other_sessions(session, me.id, request.state.session_id)      # as after a password change
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
    if problem := await _current_password_problem(request, session, me, current_password):
        errors["password_current_password"] = problem
    if problems := passwords.password_errors(new_password, confirm):
        errors["new_password"] = " ".join(problems)
    if errors:
        return _render(request, session, errors=errors, status_code=422)

    me.password_hash = await passwords.hash_password_async(new_password)
    audit.record(session, "password_changed", request, me, "other sessions and remembered devices ended")
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


@router.post("/settings/reminders")
async def change_reminders(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Turn dose reminders (ntfy push messages) on or off and set the topic they go to."""
    form = await request.form()
    me = _me(session, uid)
    topic = str(form.get("ntfy_topic") or "").strip()
    enabled = bool(form.get("ntfy_enabled"))
    if enabled and not topic:
        topic = "amide-" + secrets.token_urlsafe(18).replace("_", "x")        # hard to guess: on the public ntfy server the name is the only secret
    # New topics need 12 or more characters (on the public ntfy server anyone who knows the name can read it); a shorter topic already saved
    # keeps working while it is left as it is.
    if topic and not (re.fullmatch(r"[A-Za-z0-9_-]{12,64}", topic) or (topic == me.ntfy_topic and re.fullmatch(r"[A-Za-z0-9_-]{6,64}", topic))):
        return _render(request, session, errors={"ntfy_topic": "Use 12 to 64 letters, digits, dashes or underscores."}, status_code=422)
    me.ntfy_enabled, me.ntfy_topic = enabled, topic or None
    session.commit()
    return RedirectResponse("/settings#reminders", status_code=303)


@router.post("/settings/usda-key")
async def change_usda_key(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Save or remove the person's own FoodData Central key (it turns on the live food search; it is never shown again)."""
    form = await request.form()
    me = _me(session, uid)
    if form.get("remove"):
        me.usda_api_key = None
    else:
        key = str(form.get("usda_api_key") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9]{20,64}", key):
            return _render(request, session, errors={"usda_api_key": "That doesn't look like a FoodData Central key (letters and digits, 20 or more)."}, status_code=422)
        me.usda_api_key = key
    session.commit()
    return RedirectResponse("/settings#usda", status_code=303)


@router.post("/settings/ai")
async def change_ai_agent(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Choose the AI agent that writes the Progress Review summary, or turn it off. The key is kept with the account and never shown again."""
    form = await request.form()
    me = _me(session, uid)
    provider = str(form.get("ai_provider") or "")
    if form.get("remove") or not provider:
        me.ai_provider = me.ai_model = me.ai_base_url = me.ai_api_key = None
        session.commit()
        return RedirectResponse("/settings#ai", status_code=303)
    if provider not in ai_summary.PROVIDERS:
        return _render(request, session, errors={"ai_provider": "Choose one of the agents listed."}, status_code=422)
    model = str(form.get("ai_model") or "").strip()[:80]
    base = str(form.get("ai_base_url") or "").strip().rstrip("/")[:200] if provider == "local" else ""      # only My own server uses an address
    key = str(form.get("ai_api_key") or "").strip()[:300]
    if base and (problem := ai_summary.base_url_error(base)):
        return _render(request, session, errors={"ai_base_url": problem}, status_code=422)
    if provider == "local" and not base:
        return _render(request, session, errors={"ai_base_url": "Enter the address of your server, such as http://192.168.1.5:11434/v1 for Ollama."}, status_code=422)
    if provider == "local" and (problem := ai_summary.address_problem(base, me)):
        return _render(request, session, errors={"ai_base_url": problem}, status_code=422)
    if not model and not ai_summary.PROVIDERS[provider]["model"]:
        return _render(request, session, errors={"ai_model": "Enter the model name this agent should use."}, status_code=422)
    if provider != me.ai_provider:
        me.ai_api_key = None                      # a key never carries over to a different agent
    if key:
        me.ai_api_key = key
    if ai_summary.PROVIDERS[provider]["key"] and not me.ai_api_key:
        return _render(request, session, errors={"ai_api_key": "This agent needs an API key."}, status_code=422)
    me.ai_provider, me.ai_model, me.ai_base_url = provider, model or None, base or None
    session.commit()
    return RedirectResponse("/settings#ai", status_code=303)


@router.post("/settings/reminders/test")
def test_reminder(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    me = _me(session, uid)
    if not me.ntfy_topic:
        raise HTTPException(422, "Turn reminders on first")
    try:
        reminders.send_ntfy(config.NTFY_SERVER, me.ntfy_topic, "Amide reminders are working", "You will get a message like this when a dose is due.")
    except Exception:
        raise HTTPException(502, "The reminder server could not be reached") from None
    return RedirectResponse("/settings#reminders", status_code=303)


@router.post("/settings/calendar-feed")
async def change_calendar_feed(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Make (or replace) the secret address of the private calendar feed, or turn the feed off."""
    form = await request.form()
    me = _me(session, uid)
    action = str(form.get("action") or "")
    if action == "create":
        me.calendar_token = secrets.token_urlsafe(32)
    elif action == "off":
        me.calendar_token = None
    else:
        raise HTTPException(422, "Unknown action")
    session.commit()
    return RedirectResponse("/settings#calendar-feed", status_code=303)


@router.post("/settings/labels")
async def change_label_settings(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    size = str(form.get("label_size") or "")
    if size not in LABEL_SIZES:
        return _render(request, session, errors={"label_size": "Pick one of the sizes in the list."}, status_code=422)
    me = _me(session, uid)
    layout = me.label_layout
    if size == "custom":
        try:
            layout = parse_layout(form).to_dict()
        except LayoutError as exc:
            return _render(request, session, errors={"label_layout": str(exc)}, status_code=422)
    me.auto_print_labels, me.label_size, me.label_layout = bool(form.get("auto_print_labels")), size, layout
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/avery-alignment")
async def change_avery_alignment(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """How far the printer lands off the Avery 94500 circles at #1 and #48 (inches; blank is 0). Saved as typed, kept within half an inch."""
    form = await request.form()
    values = {}
    for key in ("tl_x", "tl_y", "br_x", "br_y"):
        raw = str(form.get(key) or "").strip()
        try:
            v = float(raw) if raw else 0.0
        except ValueError:
            return _render(request, session, errors={"avery_align": "Each alignment number must be a number of inches, like 0.05 or -0.03."}, status_code=422)
        if abs(v) > avery94500.MAX_SHIFT:
            return _render(request, session, errors={"avery_align": f"Each alignment number must be within {avery94500.MAX_SHIFT:g} inch either way."}, status_code=422)
        values[key] = v
    _me(session, uid).avery_align = values if any(values.values()) else None
    session.commit()
    return RedirectResponse("/settings#avery-alignment", status_code=303)


MAX_MEDICINES = 60


@router.post("/settings/medicines")
async def add_medicine(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    name = " ".join(str(form.get("name") or "").split())
    notes = " ".join(str(form.get("notes") or "").split()) or None
    dose_text = " ".join(str(form.get("dose_text") or "").split()) or None
    errors = {}
    if not name:
        errors["medicine_name"] = "Enter the medicine's name."
    elif len(name) > 80 or (notes and len(notes) > 200) or (dose_text and len(dose_text) > 80):
        errors["medicine_name"] = "That is too long: 80 characters for the name, 200 for the note."
    else:
        have = session.scalars(select(UserMedicine).where(UserMedicine.owner_id == uid)).all()
        if any(m.name.casefold() == name.casefold() for m in have):
            errors["medicine_name"] = "That medicine is already on your list."
        elif len(have) >= MAX_MEDICINES:
            errors["medicine_name"] = f"Your list can hold {MAX_MEDICINES} medicines. Remove one first."
    if errors:
        return _render(request, session, errors=errors, status_code=422)
    session.add(UserMedicine(owner_id=uid, name=name, notes=notes, dose_text=dose_text))
    session.commit()
    return RedirectResponse("/settings#medicines", status_code=303)


@router.post("/settings/medicines/{medicine_id}/delete")
def delete_medicine(medicine_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    med = session.get(UserMedicine, medicine_id)
    if med is None or med.owner_id != uid:
        raise HTTPException(status_code=404)
    session.delete(med)
    session.commit()
    return RedirectResponse("/settings#medicines", status_code=303)


@router.post("/settings/shopping")
async def change_shopping_defaults(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    china, us = parse_fee(form.get("china_shipping", "")), parse_fee(form.get("us_shipping", ""))
    errors = {}
    if china is None:
        errors["china_shipping"] = "Enter a dollar amount from 0 to 10,000."
    if us is None:
        errors["us_shipping"] = "Enter a dollar amount from 0 to 10,000."
    if errors:
        return _render(request, session, errors=errors, status_code=422)
    me = _me(session, uid)
    me.shop_china_shipping_cents, me.shop_us_shipping_cents = round(china * 100), round(us * 100)
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

    units_ = units.for_user(request.state.user)
    height_in = units_.length_in(_parse_float("height_in"))
    water_goal_oz = _parse_int("water_goal_oz")
    if water_goal_oz is not None:
        water_goal_oz = round(units_.volume_in(water_goal_oz))
    surgery = bool(form.get("prior_gastric_surgery"))
    measured_tdee = _parse_int("measured_tdee") if surgery else None
    if measured_tdee is not None and not 800 <= measured_tdee <= 6000 and "measured_tdee" not in errors:
        errors["measured_tdee"] = "Enter calories per day between 800 and 6,000."
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

    if birth_date is not None and birth_date > clock.today() and "birth_date" not in errors:
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
    me.prior_gastric_surgery, me.measured_tdee = surgery, measured_tdee
    session.commit()
    return RedirectResponse("/settings#user", status_code=303)


@router.post("/settings/units")
async def change_units(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    value = str(form.get("units", "")).strip()
    if value not in (units.US, units.METRIC):
        return _render(request, session, errors={"units": "Pick one of the choices in the list."}, status_code=422)
    _me(session, uid).units = value
    session.commit()
    return RedirectResponse("/settings#display", status_code=303)


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
        # An offer, not a grant: accepted_at stays NULL until the grantee accepts (see app/sharing.py).
        session.add(Share(owner_id=uid, grantee_id=grantee_id, category=cat))
        audit.record(session, "share_granted", request, session.get(User, uid), f"{cat.value} offered to user {grantee_id}")
        try:
            session.commit()
        except IntegrityError:
            session.rollback()  # a concurrent request already granted this -- already in the state we want
    elif not want_on and existing is not None:
        session.delete(existing)
        audit.record(session, "share_removed", request, session.get(User, uid), f"{cat.value} from user {grantee_id}")
        session.commit()
    return RedirectResponse("/settings#sharing", status_code=303)


def _my_received_share(session: Session, uid: int, owner_id: int, category: str) -> Share | None:
    try:
        cat = ShareCategory(category)
    except ValueError:
        return None
    return session.scalar(select(Share).where(Share.owner_id == owner_id, Share.grantee_id == uid, Share.category == cat))


@router.post("/settings/shared-with-me/{owner_id}/{category}/accept")
def accept_received_share(owner_id: int, category: str, request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    """Accept an offered share: only the grantee can, and only on a row that is theirs (anything else is the same 404).
    Accepting twice changes nothing."""
    share = _my_received_share(session, uid, owner_id, category)
    if share is None:
        raise HTTPException(status_code=404)
    if share.accepted_at is None:
        share.accepted_at = naive_utcnow()
        audit.record(session, "share_accepted", request, session.get(User, uid), f"{share.category.value} from user {owner_id}")
        session.commit()
    return RedirectResponse("/settings#sharing", status_code=303)


@router.post("/settings/shared-with-me/{owner_id}/{category}/decline")
def decline_received_share(owner_id: int, category: str, request: Request, session: Session = Depends(get_session),
                           uid: int = Depends(current_user_id)):
    """Decline an offered share (deletes the pending row; the owner may offer again). Grantee only, otherwise 404."""
    share = _my_received_share(session, uid, owner_id, category)
    if share is None or share.accepted_at is not None:
        raise HTTPException(status_code=404)
    session.delete(share)
    audit.record(session, "share_declined", request, session.get(User, uid), f"{share.category.value} from user {owner_id}")
    session.commit()
    return RedirectResponse("/settings#sharing", status_code=303)


@router.post("/settings/shared-with-me/{owner_id}/{category}/remove")
def remove_received_share(owner_id: int, category: str, request: Request, session: Session = Depends(get_session),
                          uid: int = Depends(current_user_id)):
    """Stop receiving what someone shares with me: deletes the Share row where I am the grantee. Only my own row
    can be removed; anything else (unknown person, nothing shared, a bad category) is the same 404."""
    try:
        cat = ShareCategory(category)
    except ValueError:
        raise HTTPException(status_code=404)
    share = session.scalar(select(Share).where(
        Share.owner_id == owner_id, Share.grantee_id == uid, Share.category == cat))
    if share is None:
        raise HTTPException(status_code=404)
    session.delete(share)
    audit.record(session, "share_declined", request, session.get(User, uid), f"{cat.value} from user {owner_id}")
    session.commit()
    return RedirectResponse("/settings#sharing", status_code=303)


@router.post("/settings/admin/registration")
async def admin_registration(request: Request, session: Session = Depends(get_session)):
    me = _require_admin(request, session)
    form = await request.form()
    allow = str(form.get("allow", "")) == "1"
    registration.set_open(session, allow)
    audit.record(session, "registration_changed", request, me, "open" if allow else "closed")
    session.commit()
    return RedirectResponse("/settings#admin", status_code=303)


@router.post("/settings/admin/users/new")
async def admin_add_user(request: Request, session: Session = Depends(get_session)):
    me = _require_admin(request, session)
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

    new_user = User(username=username, username_key=passwords.username_key(username), password_hash=await passwords.hash_password_async(password))
    session.add(new_user)
    session.flush()
    links.add_starter_links(session, new_user.id)
    audit.record(session, "admin_user_created", request, me, f"created {new_user.username}")
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

    target.password_hash = await passwords.hash_password_async(password)
    sessions.clear_failures(target)
    audit.record(session, "admin_password_reset", request, _me(session, request.state.user.id), f"for {target.username}")
    session.commit()
    sessions.end_all_sessions(session, target.id)
    return RedirectResponse("/settings#admin", status_code=303)


@router.post("/settings/admin/users/{user_id}/remove-2fa")
async def admin_remove_2fa(user_id: int, request: Request, session: Session = Depends(get_session)):
    target = _target_user(request, session, user_id)
    target.totp_enabled, target.totp_secret, target.totp_last_step = False, None, None
    target.photo_2fa_required = False      # its authenticator is gone, so the photo setting cannot stay on
    audit.record(session, "admin_2fa_removed", request, _me(session, request.state.user.id), f"for {target.username}")
    session.commit()
    sessions.end_all_sessions(session, target.id)      # also forgets remembered devices and deletes the passkeys: a stolen session must not outlive this
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
    for log in session.scalars(select(FoodLog).where(FoodLog.owner_id == target.id)):
        session.delete(log)
    for food in session.scalars(select(Food).where(Food.owner_id == target.id)):
        session.delete(food)
    photo_filenames = []
    for photo in session.scalars(select(BodyPhoto).where(BodyPhoto.owner_id == target.id)):
        photo_filenames.append(photo.filename)
        session.delete(photo)
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
    audit.record(session, "admin_user_deleted", request, me, f"deleted {target.username}")
    session.delete(target)
    session.commit()
    for filename in coa_filenames:
        uploads.delete_coa(filename)
    for filename in report_filenames:
        uploads.delete_lab_report(filename)
    for filename in photo_filenames:
        body_photos.delete_file(filename)
    return RedirectResponse("/settings#admin", status_code=303)


EXPIRATION_FIELDS = (("lyophilized_shelf_days", "Lyophilized peptides"), ("default_discard_days", "Reconstituted peptides"), ("bac_bottle_days", "BAC Water"))


@router.post("/settings/expiration-defaults")
async def change_expiration_defaults(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """The three default expiration windows, in days. A blank box goes back to the app's own number; nothing already dated changes."""
    form = await request.form()
    values, errors = {}, {}
    for field, label in EXPIRATION_FIELDS:
        raw = str(form.get(field, "")).strip()
        if not raw:
            values[field] = None
            continue
        try:
            days = int(raw)
            if not 1 <= days <= 3650:
                raise ValueError
        except ValueError:
            errors[field] = "Enter a whole number of days from 1 to 3650."
        else:
            values[field] = days
    if errors:
        return _render(request, session, errors=errors, status_code=422)
    me = _me(session, uid)
    for field, days in values.items():
        setattr(me, field, days)
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/progress-review")
async def change_progress_review(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """The Progress Review flag thresholds. A blank box goes back to the review's own number; the review is rule-based, so these are the rules."""
    from app.progress.review import RULE_FIELDS
    form = await request.form()
    values, errors = {}, {}
    for column, _attr, _label, whole, low, high, _help in RULE_FIELDS:
        raw = str(form.get(column, "")).strip()
        if not raw:
            values[column] = None
            continue
        try:
            number = int(raw) if whole else float(raw)
            if not low <= number <= high:
                raise ValueError
        except ValueError:
            errors[column] = f"Enter {'a whole number' if whole else 'a number'} from {low:g} to {high:g}."
        else:
            values[column] = number
    if errors:
        return _render(request, session, errors=errors, status_code=422)
    me = _me(session, uid)
    for column, number in values.items():
        setattr(me, column, number)
    session.commit()
    return RedirectResponse("/settings", status_code=303)

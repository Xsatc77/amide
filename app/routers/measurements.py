from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.measurements.calculations import (bmr, macros_for_preset, target_calories, tdee,
                                           water_goal_oz, water_pace)
from app.models import BodyMeasurement, DietPreset, MacroGoal, Share, ShareCategory, User
from app.templating import templates

router = APIRouter()

TABS = ("measurements", "macros", "journal", "labs")

# Bilateral silhouette locations: (key, left column, right column, display label).
BILATERAL_LOCATIONS = (
    ("biceps", "biceps_l_in", "biceps_r_in", "Biceps"),
    ("forearm", "forearm_l_in", "forearm_r_in", "Forearms"),
    ("quad", "quad_l_in", "quad_r_in", "Quads"),
    ("calf", "calf_l_in", "calf_r_in", "Calves"),
)
# Single-sided silhouette locations: (key/column, display label).
UNILATERAL_LOCATIONS = (
    ("neck_in", "Neck"),
    ("waist_in", "Waist"),
    ("hips_in", "Hips"),
)

# Float fields, validated the same way (optional, must be > 0 when present) as the other numeric
# fields this app validates -- see app/routers/settings.py's change_body_profile.
FLOAT_FIELDS = (
    "weight_lbs", "neck_in", "waist_in", "hips_in",
    "biceps_l_in", "biceps_r_in", "forearm_l_in", "forearm_r_in",
    "quad_l_in", "quad_r_in", "calf_l_in", "calf_r_in",
)
INT_FIELDS = ("systolic", "diastolic")


def _measurement_query(uid: int):
    """This user's measurements (others' are never visible)."""
    return select(BodyMeasurement).where(BodyMeasurement.owner_id == uid)


def _shared_measurement_query(uid: int):
    """Measurements owned by anyone who granted this user Personal Data sharing."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.PERSONAL_DATA)
    return select(BodyMeasurement).where(BodyMeasurement.owner_id.in_(shared_owner_ids))


def _field_current_and_delta(entries: list[BodyMeasurement], field: str) -> tuple[float | None, float | None]:
    """`entries` is most-recent-first. Returns (current value, delta) for `field`, where delta is
    against whichever earlier entry most recently had a non-null value for that same field -- not
    necessarily the immediately-previous row, since a row may have skipped this field entirely."""
    if not entries:
        return None, None
    current = getattr(entries[0], field)
    if current is None:
        return None, None
    prior = None
    for entry in entries[1:]:
        value = getattr(entry, field)
        if value is not None:
            prior = value
            break
    delta = None if prior is None else round(current - prior, 2)
    return current, delta


def _silhouette_points(entries: list[BodyMeasurement]) -> dict | None:
    """One entry per silhouette location for the most recent BodyMeasurement row: its current
    value (averaged across both sides for a bilateral location when both sides are present) and
    its delta since the most recent prior entry with a non-null value for that field. A bilateral
    location missing one side shows that side alone, clearly labeled -- never averaged with
    None/zero (Review Focus item 1)."""
    if not entries:
        return None

    points: dict[str, dict] = {}
    for key, left_field, right_field, label in BILATERAL_LOCATIONS:
        left_val, left_delta = _field_current_and_delta(entries, left_field)
        right_val, right_delta = _field_current_and_delta(entries, right_field)
        if left_val is not None and right_val is not None:
            deltas = [d for d in (left_delta, right_delta) if d is not None]
            points[key] = {
                "label": label,
                "value": round((left_val + right_val) / 2, 2),
                "delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
                "side": None,
            }
        elif left_val is not None:
            points[key] = {"label": label, "value": left_val, "delta": left_delta, "side": "L"}
        elif right_val is not None:
            points[key] = {"label": label, "value": right_val, "delta": right_delta, "side": "R"}
        else:
            points[key] = {"label": label, "value": None, "delta": None, "side": None}

    for field, label in UNILATERAL_LOCATIONS:
        value, delta = _field_current_and_delta(entries, field)
        points[field] = {"label": label, "value": value, "delta": delta, "side": None}

    return points


def _macros_context(user: User, latest_weight: float | None) -> dict:
    """Never raises -- a missing required profile field or missing weight yields a `status` the
    template turns into a plain prompt instead of computing anything (Review Focus item 5)."""
    required = {
        "sex": user.sex, "birth_date": user.birth_date,
        "height_in": user.height_in, "activity_level": user.activity_level,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return {"status": "missing_profile", "missing_fields": missing}
    if latest_weight is None:
        return {"status": "missing_weight"}

    today = date.today()
    birth_date = user.birth_date
    age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

    goal = user.macro_goal or MacroGoal.MAINTAIN
    preset = user.diet_preset or DietPreset.BALANCED
    custom = None
    if preset == DietPreset.CUSTOM:
        if None in (user.custom_protein_pct, user.custom_carb_pct, user.custom_fat_pct):
            return {"status": "missing_custom_macros"}
        custom = (user.custom_protein_pct, user.custom_carb_pct, user.custom_fat_pct)

    bmr_value = bmr(latest_weight, user.height_in, age, user.sex)
    tdee_value = tdee(bmr_value, user.activity_level)
    calories, floored = target_calories(tdee_value, goal, user.sex)
    try:
        protein_g, carb_g, fat_g = macros_for_preset(calories, preset, custom)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    return {
        "status": "ok",
        "age": age,
        "calories": round(calories),
        "floored": floored,
        "protein_g": round(protein_g),
        "carb_g": round(carb_g),
        "fat_g": round(fat_g),
    }


def _render(request: Request, session: Session, uid: int, *, tab: str = "measurements",
           form: dict | None = None, errors: dict | None = None, status_code: int = 200):
    own_entries = session.scalars(
        _measurement_query(uid).order_by(BodyMeasurement.measured_at.desc(), BodyMeasurement.id.desc())).all()

    shared_entries = session.scalars(
        _shared_measurement_query(uid).order_by(BodyMeasurement.measured_at.desc(),
                                                 BodyMeasurement.id.desc())).all()
    owner_ids = {m.owner_id for m in shared_entries}
    owner_names = {}
    if owner_ids:
        owner_names = dict(session.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all())
    shared_views = [{"m": m, "owner_name": owner_names.get(m.owner_id)} for m in shared_entries]

    tab = tab if tab in TABS else "measurements"

    me_user = session.get(User, uid)
    latest_weight = own_entries[0].weight_lbs if own_entries else None
    water = None
    if latest_weight is not None:
        goal_oz = water_goal_oz(latest_weight, me_user.water_goal_oz if me_user else None)
        water = {"goal_oz": goal_oz, "pace": water_pace(goal_oz)}

    return templates.TemplateResponse(request, "measurements/index.html", {
        "entries": own_entries,
        "shared_views": shared_views,
        "form": form or {},
        "errors": errors or {},
        "today": date.today().isoformat(),
        "active_tab": tab,
        "silhouette": _silhouette_points(own_entries),
        "water": water,
        "macros": _macros_context(me_user, latest_weight) if me_user else {"status": "missing_profile", "missing_fields": []},
    }, status_code=status_code)


@router.get("/measurements")
def list_measurements(request: Request, tab: str = "measurements", session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    return _render(request, session, uid, tab=tab)


@router.post("/measurements")
async def create_measurement(request: Request, session: Session = Depends(get_session),
                             uid: int = Depends(current_user_id)):
    form = await request.form()
    form_values = {key: str(form.get(key, "")) for key in form.keys()}
    errors: dict[str, str] = {}

    def _raw(field: str) -> str:
        return str(form.get(field, "")).strip()

    def _parse_float(field: str) -> float | None:
        raw = _raw(field)
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            errors[field] = "Enter a number."
            return None
        if value <= 0:
            errors[field] = "Enter a number greater than 0."
            return None
        return value

    def _parse_int(field: str) -> int | None:
        raw = _raw(field)
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            errors[field] = "Enter a whole number."
            return None
        if value <= 0:
            errors[field] = "Enter a whole number greater than 0."
            return None
        return value

    measured_at_raw = _raw("measured_at")
    if measured_at_raw:
        try:
            measured_at = date.fromisoformat(measured_at_raw)
        except ValueError:
            errors["measured_at"] = "Enter a valid date."
            measured_at = date.today()
    else:
        measured_at = date.today()

    values = {field: _parse_float(field) for field in FLOAT_FIELDS}
    values.update({field: _parse_int(field) for field in INT_FIELDS})

    if errors:
        return _render(request, session, uid, form=form_values, errors=errors, status_code=422)

    session.add(BodyMeasurement(owner_id=uid, measured_at=measured_at, **values))
    session.commit()
    return RedirectResponse("/measurements", status_code=303)

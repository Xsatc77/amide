from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.models import BodyMeasurement, Share, ShareCategory, User
from app.templating import templates

router = APIRouter()

TABS = ("measurements", "macros", "journal", "labs")

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

    return templates.TemplateResponse(request, "measurements/index.html", {
        "entries": own_entries,
        "shared_views": shared_views,
        "form": form or {},
        "errors": errors or {},
        "today": date.today().isoformat(),
        "active_tab": tab,
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

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.measurements.calculations import (bmi, bmr, body_fat_pct, macros_for_preset,
                                           target_calories, tdee, water_goal_oz, water_pace)
from app.models import BodyMeasurement, DietPreset, MacroGoal, Share, ShareCategory, User
from app.routers import journal, labs
from app.templating import templates

router = APIRouter()

TABS = ("measurements", "macros", "journal", "labs")

# Chart range selector: exactly these seven values are accepted (spec). Anything else -- absent,
# malformed, or garbage -- falls back to DEFAULT_RANGE rather than ever raising. The day counts
# below are simple fixed-length lookback windows (not calendar-month arithmetic) since a chart
# window only needs a reasonable trailing span, not exact month boundaries.
RANGE_DAYS = {"7d": 7, "14d": 14, "1mo": 30, "3mo": 91, "6mo": 182, "1yr": 365}
RANGES = (*RANGE_DAYS, "lifetime")
DEFAULT_RANGE = "3mo"
RANGE_LABELS = {"7d": "7 Days", "14d": "14 Days", "1mo": "1 Month", "3mo": "3 Months",
               "6mo": "6 Months", "1yr": "1 Year", "lifetime": "Lifetime"}

# Series rendered as one small SVG line chart each, in this order.
CHART_FIELDS = (
    ("weight_lbs", "Weight (lbs)"),
    ("neck_in", "Neck (in)"),
    ("waist_in", "Waist (in)"),
    ("hips_in", "Hips (in)"),
    ("biceps_l_in", "Biceps L (in)"),
    ("biceps_r_in", "Biceps R (in)"),
    ("forearm_l_in", "Forearm L (in)"),
    ("forearm_r_in", "Forearm R (in)"),
    ("quad_l_in", "Quad L (in)"),
    ("quad_r_in", "Quad R (in)"),
    ("calf_l_in", "Calf L (in)"),
    ("calf_r_in", "Calf R (in)"),
)

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


def _field_current_and_delta(
    entries: list[BodyMeasurement], field: str,
) -> tuple[float | None, float | None, date | None]:
    """`entries` is most-recent-first. Returns (current value, delta, as_of) for `field`. `current`
    is the most recent NON-NULL value for this field -- not necessarily from entries[0], since a
    later entry may have skipped this field entirely (e.g. a weigh-in-only day after a full
    tape-measure session). `delta` is against whichever still-earlier entry most recently had a
    non-null value. `as_of` is the date `current` actually came from, but only when that isn't
    entries[0]'s own date -- i.e. only when the "current" value is stale relative to the most
    recent entry, so the template can flag it; None when current is already up to date."""
    if not entries:
        return None, None, None
    current = current_date = None
    current_idx = None
    for i, entry in enumerate(entries):
        value = getattr(entry, field)
        if value is not None:
            current, current_date, current_idx = value, entry.measured_at, i
            break
    if current is None:
        return None, None, None
    prior = None
    for entry in entries[current_idx + 1:]:
        value = getattr(entry, field)
        if value is not None:
            prior = value
            break
    delta = None if prior is None else round(current - prior, 2)
    as_of = current_date if current_idx != 0 else None
    return current, delta, as_of


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
        left_val, left_delta, left_as_of = _field_current_and_delta(entries, left_field)
        right_val, right_delta, right_as_of = _field_current_and_delta(entries, right_field)
        if left_val is not None and right_val is not None:
            deltas = [d for d in (left_delta, right_delta) if d is not None]
            # If either side's value came from an older entry than the most recent one, flag the
            # averaged value with that (earlier, more conservative) date.
            as_of_candidates = [d for d in (left_as_of, right_as_of) if d is not None]
            points[key] = {
                "label": label,
                "value": round((left_val + right_val) / 2, 2),
                "delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
                "side": None,
                "as_of": min(as_of_candidates) if as_of_candidates else None,
            }
        elif left_val is not None:
            points[key] = {"label": label, "value": left_val, "delta": left_delta, "side": "L",
                          "as_of": left_as_of}
        elif right_val is not None:
            points[key] = {"label": label, "value": right_val, "delta": right_delta, "side": "R",
                          "as_of": right_as_of}
        else:
            points[key] = {"label": label, "value": None, "delta": None, "side": None, "as_of": None}

    for field, label in UNILATERAL_LOCATIONS:
        value, delta, as_of = _field_current_and_delta(entries, field)
        points[field] = {"label": label, "value": value, "delta": delta, "side": None, "as_of": as_of}

    return points


def _range_window(range_param: str | None, as_of_param: str | None) -> tuple[str, date, date | None]:
    """(range_key, as_of, window_start). `range_key` is always one of RANGES -- an absent or
    unrecognized value silently falls back to DEFAULT_RANGE, never a 500. `as_of` mirrors
    calendar.py's `date` query param: an optional anchor override, parsed the same
    try/date.fromisoformat/except-fallback-to-today way, mainly useful for deterministic tests but
    also a legitimate way for a real user to pin the window's end date for historical review.
    `window_start` is None for 'lifetime' (no lower bound); the window is otherwise a fixed-length
    lookback ending at `as_of` -- entries are not upper-bounded by `as_of`, since this app allows
    logging a measurement dated slightly ahead of the server's own clock and that shouldn't make
    a just-logged entry vanish from its own chart."""
    range_key = range_param if range_param in RANGES else DEFAULT_RANGE
    try:
        as_of = date.fromisoformat(as_of_param) if as_of_param else date.today()
    except ValueError:
        as_of = date.today()
    days = RANGE_DAYS.get(range_key)
    start = as_of - timedelta(days=days) if days is not None else None
    return range_key, as_of, start


def _in_window(rows: list[BodyMeasurement], start: date | None) -> list[BodyMeasurement]:
    return rows if start is None else [r for r in rows if r.measured_at >= start]


def _scaled_points(points: list[tuple[date, float]], min_d: date, max_d: date, min_v: float,
                   max_v: float, width: int, height: int, pad_x: int, pad_y: int) -> list[tuple[float, float]]:
    span_d = (max_d - min_d).days or 1
    span_v = (max_v - min_v) or None
    coords = []
    for d, v in points:
        cx = pad_x + (d - min_d).days / span_d * (width - 2 * pad_x)
        cy = height / 2 if span_v is None else height - pad_y - (v - min_v) / span_v * (height - 2 * pad_y)
        coords.append((round(cx, 1), round(cy, 1)))
    return coords


def _chart(points: list[tuple[date, float]], *, width: int = 560, height: int = 160,
          pad_x: int = 28, pad_y: int = 16,
          band: list[tuple[date, float, float]] | None = None) -> dict | None:
    """A single-series line chart's plotted geometry, scaled to its own viewBox from `points`
    (most-recent-last order not required -- sorted here). None when there's nothing to plot.

    `band`, when given, is a list of (date, low, high) tuples -- one per plotted point, though the
    caller decides that -- used to draw a shaded reference-range polygon behind the line (Labs'
    per-marker charts use this for their user-entered reference ranges; Weight & Measurements'
    own charts never pass one). The band's own low/high values widen the value scale so the band
    itself is never clipped."""
    if not points:
        return None
    points = sorted(points)
    dates = [d for d, _ in points]
    values = [v for _, v in points]
    band_values = [v for _, lo, hi in band for v in (lo, hi)] if band else []
    min_d, max_d = min(dates), max(dates)
    min_v, max_v = min(values + band_values), max(values + band_values)
    coords = _scaled_points(points, min_d, max_d, min_v, max_v, width, height, pad_x, pad_y)
    result = {"width": width, "height": height, "points": coords,
             "poly": " ".join(f"{x},{y}" for x, y in coords), "band": None,
             "min_v": round(min_v, 1), "max_v": round(max_v, 1), "min_d": min_d, "max_d": max_d}
    if band:
        band_sorted = sorted(band)
        top = _scaled_points([(d, hi) for d, _, hi in band_sorted],
                             min_d, max_d, min_v, max_v, width, height, pad_x, pad_y)
        bottom = _scaled_points([(d, lo) for d, lo, _ in reversed(band_sorted)],
                                min_d, max_d, min_v, max_v, width, height, pad_x, pad_y)
        result["band"] = " ".join(f"{x},{y}" for x, y in top + bottom)
    return result


def _dual_chart(points_a: list[tuple[date, float]], points_b: list[tuple[date, float]], *,
                width: int = 560, height: int = 160, pad_x: int = 28, pad_y: int = 16) -> dict | None:
    """Two series (e.g. systolic/diastolic) sharing one date/value scale so they're comparable on
    the same chart."""
    if not points_a and not points_b:
        return None
    points_a, points_b = sorted(points_a), sorted(points_b)
    all_points = points_a + points_b
    dates = [d for d, _ in all_points]
    values = [v for _, v in all_points]
    min_d, max_d, min_v, max_v = min(dates), max(dates), min(values), max(values)
    a = _scaled_points(points_a, min_d, max_d, min_v, max_v, width, height, pad_x, pad_y)
    b = _scaled_points(points_b, min_d, max_d, min_v, max_v, width, height, pad_x, pad_y)
    return {"width": width, "height": height, "points_a": a, "points_b": b,
           "poly_a": " ".join(f"{x},{y}" for x, y in a), "poly_b": " ".join(f"{x},{y}" for x, y in b),
           "min_v": round(min_v, 1), "max_v": round(max_v, 1), "min_d": min_d, "max_d": max_d}


def _charts_context(own_windowed: list[BodyMeasurement], user: User | None, range_key: str) -> dict:
    """Chart data for every tracked series. ALL series -- weight, blood pressure, every
    measurement field, and the derived BMI/body-fat % -- draw only from the viewer's own windowed
    entries (`own_windowed`), never a sharing partner's: mixing two people's raw numbers into one
    line would zigzag between their different values and look like a real trend when it isn't. A
    sharing partner's data stays visible only in the separate "Shared with you" table, never in
    these charts. (BMI/body-fat % additionally need the viewer's own height/sex, which is a
    second, independent reason they could never have used a partner's rows.)"""
    series = [{"key": field, "label": label,
              "chart": _chart([(r.measured_at, getattr(r, field)) for r in own_windowed
                               if getattr(r, field) is not None])}
             for field, label in CHART_FIELDS]

    bp_a = [(r.measured_at, r.systolic) for r in own_windowed if r.systolic is not None]
    bp_b = [(r.measured_at, r.diastolic) for r in own_windowed if r.diastolic is not None]
    bp_chart = _dual_chart(bp_a, bp_b)

    bmi_chart = bf_chart = None
    bf_status = None
    if user is not None and user.height_in is not None:
        bmi_points = []
        for r in own_windowed:
            if r.weight_lbs is None:
                continue
            value = bmi(r.weight_lbs, user.height_in)
            if value is not None:
                bmi_points.append((r.measured_at, value))
        bmi_chart = _chart(bmi_points)
        if user.sex is not None:
            bf_points = []
            has_candidate_rows = False
            for r in own_windowed:
                if r.neck_in is None or r.waist_in is None:
                    continue
                has_candidate_rows = True
                pct = body_fat_pct(user.sex, user.height_in, r.neck_in, r.waist_in, r.hips_in)
                if pct is not None:
                    bf_points.append((r.measured_at, pct))
            bf_chart = _chart(bf_points)
            # Rows existed with the fields body_fat_pct() needs, but every one of them came back
            # None (invalid measurement, or missing hips for a female row) -- an explicit "not
            # enough data" status, not a silently-empty chart with no explanation.
            if has_candidate_rows and not bf_points:
                bf_status = "insufficient"

    return {"range": range_key, "range_options": RANGES, "range_labels": RANGE_LABELS,
           "series": series, "bp": bp_chart, "bmi": bmi_chart, "bf": bf_chart, "bf_status": bf_status}


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
           range_param: str | None = None, as_of_param: str | None = None,
           form: dict | None = None, errors: dict | None = None, status_code: int = 200,
           extra: dict | None = None):
    # Full history (unfiltered by chart range) -- silhouette/macros/water always reflect the
    # single most recent entry regardless of which chart window is selected.
    own_entries = session.scalars(
        _measurement_query(uid).order_by(BodyMeasurement.measured_at.desc(), BodyMeasurement.id.desc())).all()

    shared_entries = session.scalars(
        _shared_measurement_query(uid).order_by(BodyMeasurement.measured_at.desc(),
                                                 BodyMeasurement.id.desc())).all()
    owner_ids = {m.owner_id for m in shared_entries}
    owner_names = {}
    if owner_ids:
        owner_names = dict(session.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all())

    range_key, as_of, window_start = _range_window(range_param, as_of_param)
    own_windowed = _in_window(own_entries, window_start)
    shared_windowed = _in_window(shared_entries, window_start)
    shared_views = [{"m": m, "owner_name": owner_names.get(m.owner_id)} for m in shared_windowed]

    tab = tab if tab in TABS else "measurements"

    me_user = session.get(User, uid)
    # Most recent NON-NULL weight across all history, not just entries[0] -- a tape-measurement-only
    # follow-up entry (no weight) must not blank the water goal or the Macros tab's calorie calc
    # when an earlier entry actually logged a weight (Review Focus item 3).
    latest_weight, _, latest_weight_as_of = _field_current_and_delta(own_entries, "weight_lbs")
    water = None
    if latest_weight is not None:
        goal_oz = water_goal_oz(latest_weight, me_user.water_goal_oz if me_user else None)
        water = {"goal_oz": goal_oz, "pace": water_pace(goal_oz), "weight_as_of": latest_weight_as_of}

    context = {
        "entries": own_windowed,
        "shared_views": shared_views,
        "form": form or {},
        "errors": errors or {},
        "today": date.today().isoformat(),
        "active_tab": tab,
        "silhouette": _silhouette_points(own_entries),
        "water": water,
        "macros": _macros_context(me_user, latest_weight) if me_user else {"status": "missing_profile", "missing_fields": []},
        "charts": _charts_context(own_windowed, me_user, range_key),
        "as_of": as_of.isoformat(),
    }
    if tab == "journal":
        context.update(journal.journal_tab_context(session, uid))
    elif tab == "labs":
        context.update(labs.labs_tab_context(session, uid, range_key, window_start))
    if extra:
        context.update(extra)

    return templates.TemplateResponse(request, "measurements/index.html", context, status_code=status_code)


@router.get("/measurements")
def list_measurements(request: Request, tab: str = "measurements",
                      range_param: str = Query(DEFAULT_RANGE, alias="range"),
                      as_of_param: str | None = Query(None, alias="as_of"),
                      session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return _render(request, session, uid, tab=tab, range_param=range_param, as_of_param=as_of_param)


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

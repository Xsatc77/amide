from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config, photo_access
from app.auth import sessions
from app.auth.deps import current_user_id
from app.db import get_session
from app import units
from app.measurements.calculations import bmi, body_fat_pct, water_goal_oz, water_pace
from app.food import usda
from app.models import BodyMeasurement, DietPreset, Food, LoginSession, MacroGoal, Share, ShareCategory, User
from app.routers import journal, labs
from app.templating import templates

router = APIRouter()

TABS = ("measurements", "food", "journal", "labs")

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
    ("heart_rate_bpm", "Heart Rate (bpm)"),
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
INT_FIELDS = ("systolic", "diastolic", "heart_rate_bpm")


def display_entries(entries, u):
    """The entries as the person wants to read them: weight and tape measurements converted to their units (US rows pass
    through untouched). Copies, so nothing stored is changed; used for tables, charts and the silhouette, never for the
    BMI, body-fat and water maths, which stay in US units."""
    if not u.metric:
        return list(entries)
    from types import SimpleNamespace
    out = []
    for e in entries:
        view = SimpleNamespace(**{c.name: getattr(e, c.name) for c in e.__table__.columns})
        for field in FLOAT_FIELDS:
            value = getattr(view, field)
            if value is not None:
                setattr(view, field, u.weight(value) if field == "weight_lbs" else u.length(value))
        out.append(view)
    return out


def unit_label(label: str, u) -> str:
    return label.replace("(lbs)", f"({u.weight_label})").replace("(in)", f"({u.length_label})")


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
) -> tuple[float | None, float | None, float | None, date | None]:
    """`entries` is most-recent-first. Returns (current value, prior value, delta, as_of) for
    `field`. `current` is the most recent NON-NULL value for this field -- not necessarily from
    entries[0], since a later entry may have skipped this field entirely (e.g. a weigh-in-only day
    after a full tape-measure session). `prior`/`delta` are against whichever still-earlier entry
    most recently had a non-null value -- `prior` is that raw value (for a "last two measurements"
    hover display), `delta` is `current - prior` rounded for display. `as_of` is the date `current`
    actually came from, but only when that isn't entries[0]'s own date -- i.e. only when the
    "current" value is stale relative to the most recent entry, so the template can flag it; None
    when current is already up to date."""
    if not entries:
        return None, None, None, None
    current = current_date = None
    current_idx = None
    for i, entry in enumerate(entries):
        value = getattr(entry, field)
        if value is not None:
            current, current_date, current_idx = value, entry.measured_at, i
            break
    if current is None:
        return None, None, None, None
    prior = None
    for entry in entries[current_idx + 1:]:
        value = getattr(entry, field)
        if value is not None:
            prior = value
            break
    delta = None if prior is None else round(current - prior, 2)
    as_of = current_date if current_idx != 0 else None
    return current, prior, delta, as_of


# Which direction of change is "good" (colored green) for a silhouette location's value, per the
# owner's explicit request: waist/hips getting smaller (or holding steady) is the goal, so only an
# INCREASE is flagged red; the four limb locations are the opposite (growth is the goal), so only a
# DECREASE is flagged red. Neck is intentionally absent -- left uncolored for now.
_DECREASE_IS_GOOD = {"waist_in", "hips_in"}
_INCREASE_IS_GOOD = {"biceps", "forearm", "quad", "calf"}


def _trend(key: str, delta: float | None) -> str | None:
    """'good' | 'bad' | None (no color) for one point's delta, per the polarity rules above. A
    zero delta ("staying steady") counts as good on both sides of the rule, matching the owner's
    own wording ("if it stays the same make the numbers green")."""
    if delta is None:
        return None
    if key in _DECREASE_IS_GOOD:
        return "good" if delta <= 0 else "bad"
    if key in _INCREASE_IS_GOOD:
        return "good" if delta >= 0 else "bad"
    return None


def _silhouette_points(entries: list[BodyMeasurement], unit: str = "in") -> dict | None:
    """One entry per silhouette location for the most recent BodyMeasurement row: its current
    value (averaged across both sides for a bilateral location when both sides are present), the
    prior value it changed from, and the delta between them. A bilateral location missing one side
    shows that side alone, clearly labeled -- never averaged with None/zero (Review Focus item 1).

    Each point also carries `chart_key`, the Overview chart dropdown option it maps to when
    clicked: the field itself for a unilateral location or a single-side bilateral one, or the
    location's own `{key}_avg` averaged-series option (see `_charts_context`) when both sides are
    present and averaged -- clicking an averaged point shows the same average it's displaying,
    never an arbitrarily-picked side. And `trend` ('good'/'bad'/None), for coloring the displayed
    number -- see `_trend`."""
    if not entries:
        return None

    points: dict[str, dict] = {}
    for key, left_field, right_field, label in BILATERAL_LOCATIONS:
        left_val, left_prior, left_delta, left_as_of = _field_current_and_delta(entries, left_field)
        right_val, right_prior, right_delta, right_as_of = _field_current_and_delta(entries, right_field)
        if left_val is not None and right_val is not None:
            deltas = [d for d in (left_delta, right_delta) if d is not None]
            priors = [p for p in (left_prior, right_prior) if p is not None]
            # If either side's value came from an older entry than the most recent one, flag the
            # averaged value with that (earlier, more conservative) date.
            as_of_candidates = [d for d in (left_as_of, right_as_of) if d is not None]
            points[key] = {
                "label": label,
                "value": round((left_val + right_val) / 2, 2),
                "prior": round(sum(priors) / len(priors), 2) if priors else None,
                "delta": round(sum(deltas) / len(deltas), 2) if deltas else None,
                "side": None, "chart_key": f"{key}_avg",
                "as_of": min(as_of_candidates) if as_of_candidates else None,
            }
        elif left_val is not None:
            points[key] = {"label": label, "value": left_val, "prior": left_prior, "delta": left_delta,
                          "side": "L", "chart_key": left_field, "as_of": left_as_of}
        elif right_val is not None:
            points[key] = {"label": label, "value": right_val, "prior": right_prior, "delta": right_delta,
                          "side": "R", "chart_key": right_field, "as_of": right_as_of}
        else:
            points[key] = {"label": label, "value": None, "prior": None, "delta": None, "side": None,
                          "chart_key": f"{key}_avg", "as_of": None}

    for field, label in UNILATERAL_LOCATIONS:
        value, prior, delta, as_of = _field_current_and_delta(entries, field)
        points[field] = {"label": label, "value": value, "prior": prior, "delta": delta, "side": None,
                         "chart_key": field, "as_of": as_of}

    for key, pt in points.items():
        pt["trend"] = _trend(key, pt["delta"])
        pt["unit"] = unit

    return points


# Front-view body outlines, traced from the user-provided reference art (a filled front-view
# male/female silhouette pair) via Moore-neighbor contour tracing + Douglas-Peucker simplification
# -- not hand-drawn approximations. Normalized to a 320x440 viewBox, centered horizontally. See
# `docs/superpowers/specs/assets/body-silhouette-reference.jpg` for the source art.
_FEMALE_PATH = ("M 153.7,4.0 L 173.2,8.2 L 182.9,23.4 L 185.7,59.6 L 173.2,62.3 L 171.8,67.9 "
               "L 180.1,77.6 L 201.0,86.0 L 205.1,91.5 L 209.3,141.5 L 217.6,167.9 L 217.6,219.3 "
               "L 221.8,242.9 L 216.3,255.4 L 210.7,258.2 L 213.5,247.1 L 207.9,245.7 L 207.9,198.5 "
               "L 198.2,167.9 L 194.0,130.4 L 188.5,136.0 L 188.5,170.7 L 199.6,211.0 L 199.6,245.7 "
               "L 185.7,319.3 L 185.7,356.8 L 174.6,404.1 L 174.6,417.9 L 181.5,433.2 L 163.5,434.6 "
               "L 166.3,323.5 L 159.3,234.6 L 152.4,324.9 L 156.5,431.8 L 153.7,436.0 L 137.1,433.2 "
               "L 144.0,419.3 L 144.0,402.7 L 132.9,355.4 L 132.9,317.9 L 119.0,241.5 L 119.0,213.7 "
               "L 130.1,172.1 L 130.1,134.6 L 124.6,130.4 L 121.8,162.4 L 110.7,199.9 L 110.7,247.1 "
               "L 105.1,247.1 L 107.9,258.2 L 98.2,247.1 L 101.0,170.7 L 109.3,142.9 L 112.1,95.7 "
               "L 120.4,84.6 L 137.1,79.0 L 146.8,69.3 L 145.4,62.3 L 132.9,59.6 L 132.9,40.1 "
               "L 139.9,15.1 L 152.4,5.4 Z")
_MALE_PATH = ("M 150.2,4.0 L 167.2,6.6 L 177.7,26.3 L 169.8,63.1 L 204.0,77.5 L 214.5,89.3 "
             "L 232.9,177.3 L 231.6,233.8 L 226.3,246.9 L 217.1,256.1 L 221.1,229.8 L 218.4,225.9 "
             "L 214.5,236.4 L 211.9,223.3 L 218.4,199.6 L 207.9,173.4 L 206.6,149.8 L 194.8,130.1 "
             "L 189.5,157.6 L 198.7,258.7 L 192.2,294.2 L 194.8,349.3 L 185.6,416.3 L 198.7,434.7 "
             "L 175.1,436.0 L 168.5,426.8 L 169.8,380.9 L 164.6,359.8 L 167.2,311.3 L 162.0,300.8 "
             "L 156.7,232.5 L 151.5,298.1 L 146.2,309.9 L 143.6,428.1 L 137.0,436.0 L 113.4,434.7 "
             "L 127.8,415.0 L 118.6,358.5 L 121.3,298.1 L 114.7,266.6 L 114.7,225.9 L 123.9,164.2 "
             "L 118.6,130.1 L 106.8,148.4 L 105.5,170.8 L 95.0,195.7 L 95.0,208.8 L 100.3,219.3 "
             "L 98.9,236.4 L 92.4,225.9 L 96.3,254.8 L 87.1,248.2 L 87.1,141.9 L 97.6,90.7 "
             "L 106.8,78.8 L 143.6,61.8 L 135.7,26.3 L 141.0,11.9 L 148.8,5.3 Z")

# Anatomical landmark positions for the 7 measurement locations, read off the same traced points
# above (the right-side x, mirrored via 320-x for the left side) -- not independently estimated,
# so a point always sits on or very near the actual traced limb/torso edge at that height. Waist
# and hips are each nudged 20px (roughly two dot-diameters) off their literal traced position --
# waist down, hips up -- at the owner's explicit request, since the two landmarks sat close enough
# together to read ambiguously at a glance.
_SILHOUETTE_LANDMARKS = {
    "Female": {
        "neck_in": (160, 62), "waist_in": (160, 170), "hips_in": (160, 195),
        "biceps": (202, 110), "forearm": (213, 155), "quad": (193, 270), "calf": (180, 340),
    },
    "Male": {
        "neck_in": (160, 63), "waist_in": (160, 177.6), "hips_in": (160, 238.7),
        "biceps": (218, 110), "forearm": (228, 150), "quad": (195, 280), "calf": (189, 350),
    },
}

# Fixed label positions (never move regardless of the actual anatomical point), so labels never
# collide or overlap each other -- each is (x, y, text-anchor). Left margin for the 3 centerline
# locations, right margin for the 4 limb locations; a leader line is drawn from wherever the real
# data point is to whichever of these slots that location owns.
# Note: these are deliberately not round decade numbers (80, 100, 190, 200...) -- as literal SVG
# attribute values they'd otherwise collide with plausible test-fixture weight/measurement values
# (e.g. a test asserting "190" is absent from a range-filtered page would false-fail against a
# `y="190"` attribute that has nothing to do with the actual data).
# All 7 on the right margin (moved from a left/right split so every leader line reads the same
# direction) in roughly top-to-bottom anatomical order, evenly spaced to avoid overlap. x=386, well
# clear of the body outline's own rightmost point (~x=234) -- the viewBox is widened to 400 (from
# 320) to fit this margin without the label text running over the silhouette itself.
_LABEL_SLOTS = {
    "neck_in": (386, 51, "end"), "biceps": (386, 109, "end"), "forearm": (386, 167, "end"),
    "waist_in": (386, 226, "end"), "hips_in": (386, 284, "end"),
    "quad": (386, 342, "end"), "calf": (386, 399, "end"),
}


def _mirror(x: float) -> float:
    return 320 - x


def _silhouette_shape(sex: str | None) -> dict:
    """Front-view body outline (one traced path per sex) plus the (x, y) anatomical position for
    each of the 7 measurement locations. Falls back to the male outline/landmarks when `sex` is
    unset, matching this app's existing default assumption elsewhere (e.g. the Macros tab)."""
    key = sex if sex in _SILHOUETTE_LANDMARKS else "Male"
    landmarks = _SILHOUETTE_LANDMARKS[key]
    path = _FEMALE_PATH if key == "Female" else _MALE_PATH

    points = {"neck_in": {"l": landmarks["neck_in"]},
             "waist_in": {"l": landmarks["waist_in"]},
             "hips_in": {"l": landmarks["hips_in"]}}
    for loc in ("biceps", "forearm", "quad", "calf"):
        right = landmarks[loc]
        points[loc] = {"l": (_mirror(right[0]), right[1]), "r": right}

    return {"body_path": path, "points": points, "label_slots": _LABEL_SLOTS}


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


PAD_LEFT = 46        # room for the value labels beside the line charts' axis


def _scaled_points(points: list[tuple[date, float]], min_d: date, max_d: date, min_v: float,
                   max_v: float, width: int, height: int, pad_x: int, pad_y: int,
                   pad_l: int | None = None) -> list[tuple[float, float]]:
    pad_l = pad_x if pad_l is None else pad_l
    span_d = (max_d - min_d).days or 1
    span_v = (max_v - min_v) or None
    coords = []
    for d, v in points:
        cx = pad_l + (d - min_d).days / span_d * (width - pad_l - pad_x)
        cy = height / 2 if span_v is None else height - pad_y - (v - min_v) / span_v * (height - 2 * pad_y)
        coords.append((round(cx, 1), round(cy, 1)))
    return coords


def _y_axis(min_v: float, max_v: float, width: int, height: int, pad_x: int, pad_y: int, pad_l: int) -> dict:
    """Reference lines for the value axis: the low, middle and high of the plotted range (one line when flat)."""
    values = [max_v] if max_v == min_v else [max_v, (max_v + min_v) / 2, min_v]
    span = (max_v - min_v) or None
    return {"ticks": [{"value": f"{v:.1f}".rstrip("0").rstrip("."),
                       "y": round(height / 2 if span is None else height - pad_y - (v - min_v) / span * (height - 2 * pad_y), 1)}
                      for v in values],
            "left": pad_l, "right": width - pad_x}


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
    pad_l = PAD_LEFT
    coords = _scaled_points(points, min_d, max_d, min_v, max_v, width, height, pad_x, pad_y, pad_l)
    result = {"width": width, "height": height, "points": coords, "raw": points,
             "y_axis": _y_axis(min_v, max_v, width, height, pad_x, pad_y, pad_l),
             "poly": " ".join(f"{x},{y}" for x, y in coords), "band": None,
             "min_v": round(min_v, 1), "max_v": round(max_v, 1), "min_d": min_d, "max_d": max_d}
    if band:
        band_sorted = sorted(band)
        top = _scaled_points([(d, hi) for d, _, hi in band_sorted],
                             min_d, max_d, min_v, max_v, width, height, pad_x, pad_y, pad_l)
        bottom = _scaled_points([(d, lo) for d, lo, _ in reversed(band_sorted)],
                                min_d, max_d, min_v, max_v, width, height, pad_x, pad_y, pad_l)
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
    a = _scaled_points(points_a, min_d, max_d, min_v, max_v, width, height, pad_x, pad_y, PAD_LEFT)
    b = _scaled_points(points_b, min_d, max_d, min_v, max_v, width, height, pad_x, pad_y, PAD_LEFT)
    return {"width": width, "height": height, "points_a": a, "points_b": b,
           "y_axis": _y_axis(min_v, max_v, width, height, pad_x, pad_y, PAD_LEFT),
           "raw_a": points_a, "raw_b": points_b,
           "poly_a": " ".join(f"{x},{y}" for x, y in a), "poly_b": " ".join(f"{x},{y}" for x, y in b),
           "min_v": round(min_v, 1), "max_v": round(max_v, 1), "min_d": min_d, "max_d": max_d}


def _charts_context(own_windowed: list[BodyMeasurement], user: User | None, range_key: str, u=None) -> dict:
    """Chart data for every tracked series. ALL series -- weight, blood pressure, every
    measurement field, and the derived BMI/body-fat % -- draw only from the viewer's own windowed
    entries (`own_windowed`), never a sharing partner's: mixing two people's raw numbers into one
    line would zigzag between their different values and look like a real trend when it isn't. A
    sharing partner's data stays visible only in the separate "Shared with you" table, never in
    these charts. (BMI/body-fat % additionally need the viewer's own height/sex, which is a
    second, independent reason they could never have used a partner's rows.)"""
    u = u or units.Units()
    shown = display_entries(own_windowed, u)                   # what the lines show; BMI and body fat below use the stored US rows
    series = [{"key": field, "label": unit_label(label, u),
              "chart": _chart([(r.measured_at, getattr(r, field)) for r in shown
                               if getattr(r, field) is not None])}
             for field, label in CHART_FIELDS]

    # One averaged L+R series per bilateral location, for the Overview chart only (not the "All
    # measurements" grid) -- matches exactly what the Body silhouette's own averaged point shows,
    # so clicking that point can jump to the same average rather than an arbitrarily-picked side.
    # Only entries with BOTH sides logged that day count -- never averaged with a missing side.
    bilateral_avg = [
        {"key": f"{key}_avg", "label": f"{label} (avg)",
         "chart": _chart([(r.measured_at, (getattr(r, left_field) + getattr(r, right_field)) / 2)
                          for r in shown
                          if getattr(r, left_field) is not None and getattr(r, right_field) is not None])}
        for key, left_field, right_field, label in BILATERAL_LOCATIONS
    ]

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
           "series": series, "bilateral_avg": bilateral_avg, "bp": bp_chart, "bmi": bmi_chart,
           "bf": bf_chart, "bf_status": bf_status}


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
    latest_weight, _, _, latest_weight_as_of = _field_current_and_delta(own_entries, "weight_lbs")
    water = None
    if latest_weight is not None:
        goal_oz = water_goal_oz(latest_weight, me_user.water_goal_oz if me_user else None)
        water = {"goal_oz": goal_oz, "pace": water_pace(goal_oz), "weight_as_of": latest_weight_as_of}

    u = units.for_user(me_user)
    shared_views = [{"m": display_entries([v["m"]], u)[0], "owner_name": v["owner_name"]} for v in shared_views]
    context = {
        "entries": display_entries(own_windowed, u),
        "shared_views": shared_views,
        "form": form or {},
        "errors": errors or {},
        "today": date.today().isoformat(),
        "active_tab": tab,
        "silhouette": _silhouette_points(display_entries(own_entries, u), u.length_label),
        "silhouette_shape": _silhouette_shape(me_user.sex.value if me_user and me_user.sex else None),
        "water": water,
        "charts": _charts_context(own_windowed, me_user, range_key, u),
        "as_of": as_of.isoformat(),
    }
    if tab == "journal":
        edit_raw = (extra or {}).get("journal_edit") or request.query_params.get("edit")
        try:
            edit_day = journal.parse_entry_date(edit_raw) if edit_raw else None
        except ValueError:
            edit_day = None
        context.update(journal.journal_tab_context(session, uid, edit_day))
        context["journal_open"] = edit_day is not None
    elif tab == "labs":
        context.update(labs.labs_tab_context(session, uid, range_key, window_start))
    if tab == "measurements" and me_user:
        login_row = session.get(LoginSession, request.state.session_id) if request.state.session_id else None
        context.update(photo_access.page_context(session, me_user, login_row, sessions.now_utc()))
        context["open_photo_dialog"] = request.query_params.get("add_photo") == "1"
    if tab == "food" and me_user:
        from app.food import summary as food_summary
        day = food_summary.parse_day(request.query_params.get("date")) or date.today()
        if isinstance((extra or {}).get("food_date"), date):
            day = extra["food_date"]
        context.update(food=food_summary.day_summary(session, me_user, day), food_day=day,
                       food_prev=(day - timedelta(days=1)).isoformat(), food_next=(day + timedelta(days=1)).isoformat(),
                       diet_presets=list(DietPreset), macro_goals=list(MacroGoal), me=me_user, usda_enabled=bool(usda.key_for(me_user)),
                       my_foods=session.scalars(select(Food).where(Food.owner_id == uid).order_by(Food.name)).all())
    if extra:
        context.update(extra)

    return templates.TemplateResponse(request, "measurements/index.html", context, status_code=status_code)


@router.get("/measurements")
def list_measurements(request: Request, tab: str = "measurements",
                      range_param: str | None = Query(None, alias="range"),
                      as_of_param: str | None = Query(None, alias="as_of"),
                      session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    if tab == "macros":                    # the old Macros tab is now the Food tab
        return RedirectResponse("/measurements?tab=food", status_code=303)
    if range_param is None and tab == "labs":          # labs are drawn a few times a year: show all of them unless a range is picked
        range_param = "lifetime"
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

    u = units.for_user(request.state.user)
    values = {field: _parse_float(field) for field in FLOAT_FIELDS}
    for field in FLOAT_FIELDS:                                          # typed in the person's units, stored in US units
        if values[field] is not None:
            values[field] = u.weight_in(values[field]) if field == "weight_lbs" else u.length_in(values[field])
    values.update({field: _parse_int(field) for field in INT_FIELDS})

    if errors:
        return _render(request, session, uid, form=form_values, errors=errors, status_code=422)

    session.add(BodyMeasurement(owner_id=uid, measured_at=measured_at, **values))
    session.commit()
    return RedirectResponse("/measurements", status_code=303)

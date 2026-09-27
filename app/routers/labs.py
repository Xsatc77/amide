from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import UploadFile

from app import uploads
from app.auth.deps import current_user_id
from app.db import get_session
from app.models import LabMarker, LabPanel, LabResult, Share, ShareCategory, User
from app.routers.journal import doses_for

router = APIRouter()


def _lab_query(uid: int):
    """This user's lab panels (others' are never visible)."""
    return select(LabPanel).where(LabPanel.owner_id == uid).options(selectinload(LabPanel.results))


def _shared_lab_query(uid: int):
    """Lab panels owned by anyone who granted this user Personal Data sharing."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.PERSONAL_DATA)
    return select(LabPanel).where(LabPanel.owner_id.in_(shared_owner_ids)).options(
        selectinload(LabPanel.results))


def _get_visible_panel(session: Session, panel_id: int, uid: int) -> LabPanel:
    """The panel for `panel_id`, but only if it's the viewer's own or shared with them -- never a
    global lookup by id alone."""
    panel = session.scalar(_lab_query(uid).where(LabPanel.id == panel_id))
    if panel is None:
        panel = session.scalar(_shared_lab_query(uid).where(LabPanel.id == panel_id))
    if panel is None:
        raise HTTPException(404, "Lab panel not found")
    return panel


# ---------------------------------------------------------------- display helpers

def _result_view(r: LabResult) -> dict:
    marker_label = r.marker_other if r.marker is LabMarker.OTHER else r.marker.value
    out_of_range = None
    if r.range_low is not None and r.range_high is not None:
        out_of_range = not (r.range_low <= r.value <= r.range_high)
    return {
        "marker_label": marker_label,
        "value": r.value,
        "unit": r.unit,
        "range_low": r.range_low,
        "range_high": r.range_high,
        "out_of_range": out_of_range,
    }


def _panel_view(session: Session, p: LabPanel, owner_name: str | None = None) -> dict:
    # That panel's OWN owner/draw-date -- for a shared panel, always the panel owner (the sharing
    # user), never the viewer, and always that panel's own `drawn_at`, never today. Mirrors the
    # cross-owner scoping rule Journal's `doses_for` already established for shared entries.
    doses = doses_for(session, p.owner_id, p.drawn_at)
    return {
        "id": p.id,
        "drawn_at": p.drawn_at,
        "notes": p.notes,
        "report_filename": p.report_filename,
        "owner_name": owner_name,
        "results": [_result_view(r) for r in p.results],
        "active_protocols": [
            {"peptide_name": d["peptide_name"], "dose_value": d["dose_value"], "dose_unit": d["dose_unit"]}
            for d in doses
        ],
    }


def _in_window(panels: list[LabPanel], window_start: date | None) -> list[LabPanel]:
    return panels if window_start is None else [p for p in panels if p.drawn_at >= window_start]


def _labs_charts(own_panels: list[LabPanel], window_start: date | None) -> list[dict]:
    """One trend chart per marker, built ONLY from the viewer's OWN panels (a sharing partner's
    results stay visible in the panel list but never get plotted into the viewer's own chart --
    same rule Weight & Measurements' own charts follow), windowed by the shared range selector.
    Grouped by marker, using `marker_other` as the effective key for "Other" markers so two
    differently-named custom markers are never merged into one chart. Reuses measurements.py's
    own `_chart` geometry builder (deferred import -- measurements.py imports this module at load
    time) rather than duplicating its date/value scaling math a second time."""
    from app.routers.measurements import _chart  # deferred: avoid the module-level import cycle

    grouped: dict[str, list[dict]] = {}
    for panel in _in_window(own_panels, window_start):
        for r in panel.results:
            key = r.marker_other if r.marker is LabMarker.OTHER else r.marker.value
            grouped.setdefault(key, []).append({
                "drawn_at": panel.drawn_at, "value": r.value,
                "range_low": r.range_low, "range_high": r.range_high,
            })

    charts = []
    for label, points in grouped.items():
        points.sort(key=lambda p: p["drawn_at"])
        # A single point isn't a trend -- no chart at all below 2 points in the selected window.
        if len(points) < 2:
            continue
        # The shaded reference-range band is included only when EVERY point in this chart
        # carries both bounds -- a mix of some-bounds/no-bounds points would misleadingly imply
        # a band that doesn't apply to every plotted date.
        band = None
        if all(p["range_low"] is not None and p["range_high"] is not None for p in points):
            band = [(p["drawn_at"], p["range_low"], p["range_high"]) for p in points]
        chart = _chart([(p["drawn_at"], p["value"]) for p in points], band=band)
        if chart is not None:
            charts.append({"marker_label": label, "chart": chart})
    charts.sort(key=lambda c: c["marker_label"])
    return charts


def labs_tab_context(session: Session, viewer_uid: int, range_key: str = "lifetime",
                     window_start: date | None = None) -> dict:
    own_panels = session.scalars(
        _lab_query(viewer_uid).order_by(LabPanel.drawn_at.desc(), LabPanel.id.desc())).all()

    shared_panels = session.scalars(
        _shared_lab_query(viewer_uid).order_by(LabPanel.drawn_at.desc(), LabPanel.id.desc())).all()
    owner_ids = {p.owner_id for p in shared_panels}
    owner_names = {}
    if owner_ids:
        owner_names = dict(session.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all())

    panels = [_panel_view(session, p) for p in own_panels]
    panels += [_panel_view(session, p, owner_name=owner_names.get(p.owner_id)) for p in shared_panels]
    panels.sort(key=lambda v: v["drawn_at"], reverse=True)

    return {"panels": panels, "lab_markers": list(LabMarker),
           "lab_charts": {"range": range_key, "series": _labs_charts(own_panels, window_start)}}


# ---------------------------------------------------------------- routes

@router.get("/labs/panels/{panel_id}/report")
def get_lab_report(panel_id: int, session: Session = Depends(get_session),
        uid: int = Depends(current_user_id)):
    panel = _get_visible_panel(session, panel_id, uid)
    if not panel.report_filename:
        raise HTTPException(404, "This panel has no report attached")
    path = uploads.lab_report_path(panel.report_filename)
    if not path.exists():
        raise HTTPException(404, "Lab report file is missing from disk")
    return FileResponse(path, media_type=uploads.media_type(panel.report_filename),
                        headers={"X-Content-Type-Options": "nosniff"},
                        content_disposition_type="inline")


@router.post("/labs/panels")
async def create_lab_panel(request: Request, session: Session = Depends(get_session),
                           uid: int = Depends(current_user_id)):
    form = await request.form()

    def _raw(field: str) -> str:
        return str(form.get(field, "")).strip()

    errors: dict[str, str] = {}

    drawn_at_raw = _raw("drawn_at")
    drawn_at: date | None = None
    if not drawn_at_raw:
        errors["drawn_at"] = "Draw date is required."
    else:
        try:
            drawn_at = date.fromisoformat(drawn_at_raw)
        except ValueError:
            errors["drawn_at"] = "Enter a valid date."

    markers = form.getlist("marker[]")
    values_raw = form.getlist("value[]")
    units = form.getlist("unit[]")
    range_lows = form.getlist("range_low[]")
    range_highs = form.getlist("range_high[]")
    marker_others = form.getlist("marker_other[]")

    if not markers:
        errors["rows"] = "At least one result row is required."

    def _at(items: list, i: int) -> str:
        return str(items[i]).strip() if i < len(items) else ""

    parsed_results: list[dict] = []
    posted_rows: list[dict] = []
    for i, marker_raw in enumerate(markers):
        marker_raw = str(marker_raw).strip()
        value_raw = _at(values_raw, i)
        unit = _at(units, i)
        range_low_raw = _at(range_lows, i)
        range_high_raw = _at(range_highs, i)
        marker_other_raw = _at(marker_others, i)

        # Recorded up front, before any validation, so a re-render on error can rebuild every row
        # exactly as posted -- including rows that themselves have no error (Review Focus: a bulk
        # form must not discard already-correct rows just because one other row failed).
        posted_rows.append({
            "marker": marker_raw, "value": value_raw, "unit": unit,
            "range_low": range_low_raw, "range_high": range_high_raw, "marker_other": marker_other_raw,
        })

        try:
            marker = LabMarker[marker_raw]
        except KeyError:
            errors[f"marker_{i}"] = "Invalid marker."
            continue

        if marker is LabMarker.OTHER and not marker_other_raw:
            errors[f"marker_other_{i}"] = 'Enter a name for this "Other" marker.'
        if marker is not LabMarker.OTHER and marker_other_raw:
            errors[f"marker_other_{i}"] = "Only used for \"Other\" markers."

        value: float | None = None
        if not value_raw:
            errors[f"value_{i}"] = "Value is required."
        else:
            try:
                value = float(value_raw)
            except ValueError:
                errors[f"value_{i}"] = "Enter a number."

        range_low = range_high = None
        if range_low_raw:
            try:
                range_low = float(range_low_raw)
            except ValueError:
                errors[f"range_low_{i}"] = "Enter a number."
        if range_high_raw:
            try:
                range_high = float(range_high_raw)
            except ValueError:
                errors[f"range_high_{i}"] = "Enter a number."
        if range_low is not None and range_high is not None and range_low > range_high:
            errors[f"range_{i}"] = "Low bound must not exceed the high bound."

        parsed_results.append({
            "marker": marker,
            "marker_other": marker_other_raw or None,
            "value": value,
            "unit": unit or None,
            "range_low": range_low,
            "range_high": range_high,
        })

    report_filename = None
    report_file = form.get("report")
    if isinstance(report_file, UploadFile) and report_file.filename:
        try:
            report_filename = await uploads.save_lab_report(report_file)
        except uploads.UploadError as exc:
            errors["report"] = str(exc)

    if errors:
        from app.routers import measurements  # deferred: measurements imports this module at load time
        lab_posted = {
            "drawn_at": drawn_at_raw,
            "notes": _raw("notes"),
            "rows": posted_rows,
            "errors": errors,
        }
        return measurements._render(request, session, uid, tab="labs", errors=errors, status_code=422,
                                    extra={"lab_posted": lab_posted})

    panel = LabPanel(owner_id=uid, drawn_at=drawn_at, notes=_raw("notes") or None,
                     report_filename=report_filename)
    panel.results = [LabResult(**r) for r in parsed_results]
    session.add(panel)
    session.commit()
    return RedirectResponse("/measurements?tab=labs", status_code=303)

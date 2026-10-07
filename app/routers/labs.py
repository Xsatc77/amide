import math
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


def _get_own_panel(session: Session, panel_id: int, uid: int) -> LabPanel:
    """Like `_get_visible_panel`, but never a shared panel -- editing is owner-only, since a panel
    shared with you for viewing was never yours to change."""
    panel = session.scalar(_lab_query(uid).where(LabPanel.id == panel_id))
    if panel is None:
        raise HTTPException(404, "Lab panel not found")
    return panel


# ---------------------------------------------------------------- display helpers

def _result_view(r: LabResult) -> dict:
    marker_label = r.marker_other if r.marker is LabMarker.OTHER else r.marker.value
    out_of_range = None
    if r.range_low is not None and r.range_high is not None and not r.qualifier:       # "<5" has no exact value to compare
        out_of_range = not (r.range_low <= r.value <= r.range_high)
    return {
        "marker_label": marker_label,
        "value": r.value,
        "qualifier": r.qualifier or "",
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


def _panel_edit_data(p: LabPanel) -> dict:
    """Raw, editable field values for one of the viewer's OWN panels -- unlike `_panel_view`
    (display-only: resolved marker label, no raw enum name), this carries exactly what the New/Edit
    dialog's own row template needs to repopulate a <select> and every input for editing."""
    return {
        "id": p.id, "drawn_at": p.drawn_at.isoformat(), "notes": p.notes or "",
        "rows": [
            {"marker": r.marker.name, "marker_other": r.marker_other or "",
             "value": f"{r.qualifier or ''}{r.value:.10g}", "unit": r.unit or "",
             "range_low": r.range_low, "range_high": r.range_high}
            for r in p.results
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
           "lab_charts": {"range": range_key, "series": _labs_charts(own_panels, window_start)},
           "own_panels_for_edit": [_panel_edit_data(p) for p in own_panels]}


def _at(items: list, i: int) -> str:
    return str(items[i]).strip() if i < len(items) else ""


def _parse_lab_rows(form) -> tuple[list[dict], list[dict], dict]:
    """(parsed_results, posted_rows, row_errors) from a submitted bulk-entry sheet's bracket-array
    fields. A row with no value entered is silently skipped, not an error -- the sheet now ships
    with 25 blank lines by default (Review Focus: filling in a handful of a much longer sheet is
    the normal case, not an incomplete submission), so treating every unfilled line as "value
    required" would make the sheet unusable as shipped. A row IS still validated normally, and can
    still error, once it has a non-blank value -- only a fully-blank line is exempt."""
    markers = form.getlist("marker[]")
    values_raw = form.getlist("value[]")
    units = form.getlist("unit[]")
    range_lows = form.getlist("range_low[]")
    range_highs = form.getlist("range_high[]")
    marker_others = form.getlist("marker_other[]")

    errors: dict[str, str] = {}
    parsed_results: list[dict] = []
    posted_rows: list[dict] = []
    any_filled = False
    for i, marker_raw in enumerate(markers):
        marker_raw = str(marker_raw).strip()
        value_raw = _at(values_raw, i)
        unit = _at(units, i)
        range_low_raw = _at(range_lows, i)
        range_high_raw = _at(range_highs, i)
        marker_other_raw = _at(marker_others, i)

        if not value_raw:
            continue  # an unfilled sheet line -- not a posted row at all

        any_filled = True
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
        elif marker is not LabMarker.OTHER and marker_other_raw:
            errors[f"marker_other_{i}"] = "Only used for \"Other\" markers."
        elif marker_other_raw and len(marker_other_raw) > 80:
            errors[f"marker_other_{i}"] = "Must be 80 characters or fewer."

        if unit and len(unit) > 20:
            errors[f"unit_{i}"] = "Must be 20 characters or fewer."

        # A row's value/range bounds must parse to a FINITE float -- `float("nan")` passes the bare
        # try/except below (it's valid float syntax) but would then hit SQLite's NOT NULL `value`
        # column as an effective NULL, raising an unhandled IntegrityError; `float("inf")` (or any
        # magnitude beyond what float64 holds, which Python silently coerces to inf) would insert
        # fine but produce nan/inf chart coordinates downstream. Rejecting both here, before any DB
        # write, keeps a bad row a normal per-row 422 instead of a 500 or silently-broken chart.
        value: float | None = None
        qualifier = value_raw[0] if value_raw[:1] in ("<", ">") else None          # "<5" and ">100" are results too
        number_text = value_raw[1:].strip() if qualifier else value_raw
        try:
            parsed_value = float(number_text)
            if not math.isfinite(parsed_value):
                raise ValueError
            value = parsed_value
        except ValueError:
            errors[f"value_{i}"] = "Enter a number."

        range_low = range_high = None
        if range_low_raw:
            try:
                parsed_low = float(range_low_raw)
                if not math.isfinite(parsed_low):
                    raise ValueError
                range_low = parsed_low
            except ValueError:
                errors[f"range_low_{i}"] = "Enter a number."
        if range_high_raw:
            try:
                parsed_high = float(range_high_raw)
                if not math.isfinite(parsed_high):
                    raise ValueError
                range_high = parsed_high
            except ValueError:
                errors[f"range_high_{i}"] = "Enter a number."
        if range_low is not None and range_high is not None and range_low > range_high:
            errors[f"range_{i}"] = "Low bound must not exceed the high bound."

        parsed_results.append({
            "marker": marker,
            "marker_other": marker_other_raw or None,
            "value": value,
            "qualifier": qualifier,
            "unit": unit or None,
            "range_low": range_low,
            "range_high": range_high,
        })

    if not any_filled:
        errors["rows"] = "Enter at least one result."
    return parsed_results, posted_rows, errors


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


async def _save_panel(request: Request, session: Session, uid: int, *, panel: LabPanel | None):
    """Shared create/edit handling: `panel` is None for a new panel, or an existing owned panel to
    overwrite in place. On success, redirects to the Labs tab; on any error, re-renders the whole
    Measurements page (Labs tab) with the dialog set to reopen pre-filled with what was posted, per
    this codebase's established error-reopen convention (see inventory.js/journal.js)."""
    form = await request.form()

    def _raw(field: str) -> str:
        return str(form.get(field, "")).strip()

    drawn_at_raw = _raw("drawn_at")
    drawn_at: date | None = None
    errors: dict[str, str] = {}
    if not drawn_at_raw:
        errors["drawn_at"] = "Draw date is required."
    else:
        try:
            drawn_at = date.fromisoformat(drawn_at_raw)
        except ValueError:
            errors["drawn_at"] = "Enter a valid date."

    parsed_results, posted_rows, row_errors = _parse_lab_rows(form)
    errors.update(row_errors)

    notes_raw = _raw("notes")
    if len(notes_raw) > 2000:
        errors["notes"] = "Notes must be 2000 characters or fewer."

    # The upload is only written to disk once every other field/row has already passed validation --
    # writing it any earlier would leave an orphaned, unreferenced file on disk whenever some other
    # part of the same submission gets rejected with a 422 (and the browser can't refill a file
    # input, so a user fixing one bad row and resubmitting leaves yet another orphan each time).
    report_filename = panel.report_filename if panel else None
    report_file = form.get("report")
    if not errors and isinstance(report_file, UploadFile) and report_file.filename:
        try:
            report_filename = await uploads.save_lab_report(report_file)
        except uploads.UploadError as exc:
            errors["report"] = str(exc)

    if errors:
        from app.routers import measurements  # deferred: measurements imports this module at load time
        lab_posted = {
            "id": panel.id if panel else None,
            "drawn_at": drawn_at_raw,
            "notes": notes_raw,
            "rows": posted_rows,
            "errors": errors,
        }
        return measurements._render(request, session, uid, tab="labs", errors=errors, status_code=422,
                                    extra={"lab_posted": lab_posted})

    if panel is None:
        panel = LabPanel(owner_id=uid)
        session.add(panel)
    panel.drawn_at = drawn_at
    panel.notes = notes_raw or None
    panel.report_filename = report_filename
    # Clear-and-rebuild, matching save_protocol's own idiom elsewhere in this codebase -- simplest
    # way to reconcile an arbitrary add/remove/reorder of rows on an edit.
    panel.results = [LabResult(**r) for r in parsed_results]
    session.commit()
    return RedirectResponse("/measurements?tab=labs", status_code=303)


@router.post("/labs/panels")
async def create_lab_panel(request: Request, session: Session = Depends(get_session),
                           uid: int = Depends(current_user_id)):
    return await _save_panel(request, session, uid, panel=None)


@router.post("/labs/panels/{panel_id}")
async def edit_lab_panel(panel_id: int, request: Request, session: Session = Depends(get_session),
                         uid: int = Depends(current_user_id)):
    panel = _get_own_panel(session, panel_id, uid)
    return await _save_panel(request, session, uid, panel=panel)

"""The Today view: what's due today, logging a dose (drawing down an Active Vial), skipping one."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calculator.reconstitution import _DOSE_UNITS_TO_MG
from app.calendar.schedule import missed_items, occurrences
from app.db import get_session
from app.dosing.site import eligible_sites, recommend
from app.models import ActiveVial, DoseLog, DoseStatus, DoseUnit, InjectionSite, Protocol, ProtocolItem
from app.protocols.status import current_step, current_week
from app.routers.protocols import get_today
from app.auth.deps import current_user_id
from app.templating import templates

router = APIRouter()

# Repeated float subtraction (e.g. 2.0 - 0.4*5) can leave a tiny non-zero residual (~1e-16) on a
# vial that is really empty -- anything at or below this floor is treated as empty. Typical doses
# run 0.1-2 mL, so this is comfortably below any real dose while still well above float noise.
_EMPTY_EPSILON = 0.0001


def _own_protocol_item(session: Session, protocol_id: int, protocol_item_id: int, uid: int) -> ProtocolItem | None:
    protocol = session.get(Protocol, protocol_id)
    if protocol is None or protocol.owner_id != uid:
        return None
    item = session.get(ProtocolItem, protocol_item_id)
    return item if item is not None and item.protocol_id == protocol.id else None


def _open_vial_for_item(session: Session, inventory_item_id: int) -> ActiveVial | None:
    """The earliest-discard_by open (non-discarded, non-empty) ActiveVial for this inventory item --
    mirrors app.routers.inventory._open_active_vials' ordering for a single item. Empty means at or
    below _EMPTY_EPSILON, not strictly > 0 -- a vial drained by repeated float subtraction can be
    left with a residual like 1.11e-16, which is > 0 but not really any liquid left to draw."""
    return session.scalar(
        select(ActiveVial)
        .where(ActiveVial.inventory_item_id == inventory_item_id, ActiveVial.discarded_at.is_(None),
              ActiveVial.volume_remaining_ml > _EMPTY_EPSILON)
        .order_by(ActiveVial.discard_by, ActiveVial.id)
    )


def _dose_volume_ml(dose_value: float | None, dose_unit: DoseUnit, vial: ActiveVial) -> float | None:
    """None when the dose can't be converted to mL (an IU dose has no universal mg conversion --
    the Calculator's own compute() has the same limitation) or there's no dose value at all."""
    factor = _DOSE_UNITS_TO_MG.get(dose_unit.value)
    if dose_value is None or factor is None:
        return None
    return (dose_value * factor) / vial.concentration_mg_ml


def _last_site_for_peptide(session: Session, uid: int, peptide_id: int) -> InjectionSite | None:
    log = session.scalar(
        select(DoseLog)
        .where(DoseLog.owner_id == uid, DoseLog.peptide_id == peptide_id, DoseLog.injection_site.is_not(None))
        .order_by(DoseLog.logged_at.desc())
    )
    return log.injection_site if log else None


def _effective_dose(item: ProtocolItem, protocol: Protocol, day: date) -> tuple[float | None, int | None]:
    """The titration-adjusted dose for `item` on `day`, computed the same way
    app.calendar.schedule._due_item() computes DueItem.dose for display -- falls back to the
    item's own base dose when titration isn't enabled on its protocol or no step applies for that
    date. Returns (dose, step_number)."""
    dose, step_no = item.dose, None
    if protocol.titration_enabled and item.steps:
        step = current_step(item.steps, current_week(protocol.start_date, day))
        if step is not None:
            dose, step_no = step.dose, item.steps.index(step) + 1
    return dose, step_no


def _existing_log(session: Session, uid: int, protocol_item_id: int, scheduled_date: date) -> DoseLog | None:
    """A DoseLog already on file for this (item, date) -- guards log_dose/skip_dose against
    creating a duplicate on a double-click, back-button resubmit, or a stale catch-up link."""
    return session.scalar(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.protocol_item_id == protocol_item_id,
                              DoseLog.scheduled_date == scheduled_date))


@router.get("/today")
def today_page(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
              uid: int = Depends(current_user_id)):
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    occs = occurrences(protocols, today, today)
    all_due = [(occ, item) for occ in occs for item in occ.items]

    todays_logs = session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date == today)
        .order_by(DoseLog.logged_at)).all()
    logged_ids = {dl.protocol_item_id for dl in todays_logs}
    due = [(occ, item) for occ, item in all_due if item.protocol_item_id not in logged_ids]

    from app.routers.workouts import workouts_due_today
    workout_days_due = workouts_due_today(session, uid, today)

    # Built from all_due (not the logged-filtered `due`) so the recommended-site data for an item
    # already logged today still surfaces on the page -- e.g. right after logging, so the JS/tests
    # reading this blob can see the mirrored recommendation for that peptide's *next* dose, even
    # though the just-logged occurrence itself no longer needs a picker (it dropped out of `due`).
    # DueItem.route holds the human label (e.g. "SubQ"), not the lowercase route value that
    # eligible_sites()/recommend() key off of ("subq") -- .lower() bridges that; every current
    # Route label lowercases to exactly its value.
    site_data = {}
    for occ, item in all_due:
        sites = eligible_sites(item.route.lower())
        if sites:
            last = _last_site_for_peptide(session, uid, item.peptide_id)
            site_data[item.protocol_item_id] = {
                "sites": [{"value": s.value, "label": s.label} for s in sites],
                "last": last.value if last else None,
                "recommended": recommend(last, item.route.lower()).value if recommend(last, item.route.lower()) else None,
            }

    volume_text = {}
    protocol_items_by_id = {
        pi.id: pi for p in protocols for pi in p.items
    }
    for occ, item in due:
        pi = protocol_items_by_id.get(item.protocol_item_id)
        if pi is None or pi.inventory_item_id is None:
            continue
        vial = _open_vial_for_item(session, pi.inventory_item_id)
        if vial is None:
            continue
        # item.dose (the DueItem) is already the titration-adjusted dose for today, computed by
        # _due_item() -- pi.dose is the item's untitrated base dose, which would show a volume
        # that doesn't match the dose displayed right next to it on a titrated protocol.
        vol = _dose_volume_ml(item.dose, pi.dose_unit, vial)
        if vol is None:
            continue
        verb = "Dial the pen to" if vial.dispensing_method.value == "pen" else "Draw to"
        volume_text[item.protocol_item_id] = f"{verb} {vol:.2f} mL"

    # A dose that just emptied its vial (see log_dose's empty-vial redirect) surfaces a small
    # dismissible banner naming the vial, mirroring the existing expiry-prompt convention
    # (app/static/js/inventory.js) but without a JS-driven dialog -- this is a one-time, one-vial
    # notice reached only via that redirect's query param, not a recurring popup.
    empty_vial = None
    raw_empty_vial_id = request.query_params.get("empty_vial", "")
    if raw_empty_vial_id.isdigit():
        v = session.get(ActiveVial, int(raw_empty_vial_id))
        if v is not None and v.owner_id == uid:
            empty_vial = {"id": v.id, "item_name": v.inventory_item.name if v.inventory_item else "This vial"}

    return templates.TemplateResponse(request, "dosing/today.html", {
        "due": due, "today": today, "today_iso": today.isoformat(), "site_data": site_data,
        "volume_text": volume_text, "empty_vial": empty_vial, "workout_days_due": workout_days_due,
        "logged_today": todays_logs,
    })


@router.post("/today/log")
async def log_dose(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    try:
        protocol_id, protocol_item_id = int(form.get("protocol_id", "")), int(form.get("protocol_item_id", ""))
        scheduled_date = date.fromisoformat(str(form.get("scheduled_date", "")))
    except (TypeError, ValueError):
        raise HTTPException(404)
    item = _own_protocol_item(session, protocol_id, protocol_item_id, uid)
    if item is None:
        raise HTTPException(404, "Protocol item not found")
    if scheduled_date > date.today():
        raise HTTPException(422, "Can't log a dose for a future date.")

    # A double-click, browser back-button resubmit, two open tabs, or a stale catch-up link can
    # each race to post this same (item, date) twice -- the second one is a silent no-op, not an
    # error, so it looks like a duplicate click just did nothing rather than double-depleting a
    # vial or duplicating history.
    if _existing_log(session, uid, item.id, scheduled_date) is not None:
        return RedirectResponse("/today", status_code=303)

    # _own_protocol_item already confirmed this protocol exists and is owned by uid.
    protocol = session.get(Protocol, protocol_id)
    effective_dose, _step_no = _effective_dose(item, protocol, scheduled_date)

    vial = None
    volume_ml = None
    if item.inventory_item_id is not None:
        vial = _open_vial_for_item(session, item.inventory_item_id)
        if vial is not None:
            volume_ml = _dose_volume_ml(effective_dose, item.dose_unit, vial)

    # Site parsing/recommendation runs whenever this item's route uses injection sites at all --
    # NOT only when a vial happens to be available -- so a user's explicit site choice is never
    # silently discarded just because the item has no linked inventory item, no open vial, or an
    # IU dose with no computable volume.
    site = None
    sites = eligible_sites(item.route.value)
    if sites:
        raw_site = str(form.get("injection_site") or "").strip()
        try:
            site = InjectionSite(raw_site) if raw_site else None
        except ValueError:
            site = None
        if site is not None and site not in sites:
            # A client posted a site ineligible for this item's route (e.g. a forged glute_l on a
            # SubQ item, bypassing the UI's own filtering) -- treat it the same as absent/invalid.
            site = None
        if site is None:
            site = recommend(_last_site_for_peptide(session, uid, item.peptide_id), item.route.value)

    status = DoseStatus.ON_TIME if scheduled_date == date.today() else DoseStatus.LATE
    session.add(DoseLog(
        owner_id=uid, protocol_id=protocol_id, protocol_item_id=item.id, active_vial_id=vial.id if vial else None,
        peptide_id=item.peptide_id, peptide_name=item.peptide.name, dose_value=effective_dose,
        dose_unit=item.dose_unit, route=item.route.value, scheduled_date=scheduled_date,
        scheduled_time_of_day=item.time_of_day, status=status, logged_at=datetime.now(timezone.utc),
        injection_site=site, volume_ml=volume_ml,
    ))

    empty_vial_id = None
    if vial is not None and volume_ml is not None:
        # Guard against overdrawing: repeated float subtraction (and a dose that plain exceeds
        # what's left) must never leave a negative volume_remaining_ml. The dose still happened in
        # real life even if the vial's tracked volume was already imprecise -- clamp to 0 rather
        # than rejecting the log. Rounding after every write keeps residuals from compounding.
        vial.volume_remaining_ml = round(max(0.0, vial.volume_remaining_ml - volume_ml), 4)
        if vial.volume_remaining_ml <= _EMPTY_EPSILON:
            empty_vial_id = vial.id
    session.commit()
    if empty_vial_id is not None:
        return RedirectResponse(f"/today?empty_vial={empty_vial_id}", status_code=303)
    return RedirectResponse("/today", status_code=303)


@router.post("/today/skip")
async def skip_dose(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    try:
        protocol_id, protocol_item_id = int(form.get("protocol_id", "")), int(form.get("protocol_item_id", ""))
        scheduled_date = date.fromisoformat(str(form.get("scheduled_date", "")))
    except (TypeError, ValueError):
        raise HTTPException(404)
    item = _own_protocol_item(session, protocol_id, protocol_item_id, uid)
    if item is None:
        raise HTTPException(404, "Protocol item not found")

    if _existing_log(session, uid, item.id, scheduled_date) is not None:
        return RedirectResponse("/today", status_code=303)

    # _own_protocol_item already confirmed this protocol exists and is owned by uid.
    protocol = session.get(Protocol, protocol_id)
    effective_dose, _step_no = _effective_dose(item, protocol, scheduled_date)

    session.add(DoseLog(
        owner_id=uid, protocol_id=protocol_id, protocol_item_id=item.id, peptide_id=item.peptide_id,
        peptide_name=item.peptide.name, dose_value=effective_dose, dose_unit=item.dose_unit,
        route=item.route.value, scheduled_date=scheduled_date, scheduled_time_of_day=item.time_of_day,
        status=DoseStatus.SKIPPED, logged_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return RedirectResponse("/today", status_code=303)


@router.post("/today/undo")
async def undo_dose(request: Request, session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    """Removes a logged dose or skip -- only one scheduled for TODAY, never older history, so this
    is strictly an "I just misclicked" correction, not a way to edit the record after the fact. A
    dose that drew down an Active Vial gets that volume restored, so undoing never leaves a vial
    permanently short."""
    form = await request.form()
    try:
        log_id = int(form.get("dose_log_id", ""))
    except (TypeError, ValueError):
        raise HTTPException(404)
    log = session.get(DoseLog, log_id)
    if log is None or log.owner_id != uid:
        raise HTTPException(404, "Dose log not found")
    if log.scheduled_date != date.today():
        raise HTTPException(422, "Only a dose scheduled for today can be undone.")

    if log.active_vial_id is not None and log.volume_ml is not None:
        vial = session.get(ActiveVial, log.active_vial_id)
        if vial is not None:
            vial.volume_remaining_ml = round(vial.volume_remaining_ml + log.volume_ml, 4)

    session.delete(log)
    session.commit()
    return RedirectResponse("/today", status_code=303)

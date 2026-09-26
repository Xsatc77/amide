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
from app.routers.protocols import get_today
from app.auth.deps import current_user_id
from app.templating import templates

router = APIRouter()


def _own_protocol_item(session: Session, protocol_id: int, protocol_item_id: int, uid: int) -> ProtocolItem | None:
    protocol = session.get(Protocol, protocol_id)
    if protocol is None or protocol.owner_id != uid:
        return None
    item = session.get(ProtocolItem, protocol_item_id)
    return item if item is not None and item.protocol_id == protocol.id else None


def _open_vial_for_item(session: Session, inventory_item_id: int) -> ActiveVial | None:
    """The earliest-discard_by open (non-discarded, non-empty) ActiveVial for this inventory item --
    mirrors app.routers.inventory._open_active_vials' ordering for a single item."""
    return session.scalar(
        select(ActiveVial)
        .where(ActiveVial.inventory_item_id == inventory_item_id, ActiveVial.discarded_at.is_(None),
              ActiveVial.volume_remaining_ml > 0)
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

    logged_ids = {
        (dl.protocol_item_id) for dl in session.scalars(
            select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date == today))
    }
    due = [(occ, item) for occ, item in all_due if item.protocol_item_id not in logged_ids]

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

    return templates.TemplateResponse(request, "dosing/today.html", {
        "due": due, "today": today, "today_iso": today.isoformat(), "site_data": site_data,
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

    vial = None
    volume_ml = None
    site = None
    if item.inventory_item_id is not None:
        vial = _open_vial_for_item(session, item.inventory_item_id)
        if vial is not None:
            volume_ml = _dose_volume_ml(item.dose, item.dose_unit, vial)
            raw_site = str(form.get("injection_site") or "").strip()
            try:
                site = InjectionSite(raw_site) if raw_site else None
            except ValueError:
                site = None
            if site is None and eligible_sites(item.route.value):
                site = recommend(_last_site_for_peptide(session, uid, item.peptide_id), item.route.value)

    status = DoseStatus.ON_TIME if scheduled_date == date.today() else DoseStatus.LATE
    session.add(DoseLog(
        owner_id=uid, protocol_id=protocol_id, protocol_item_id=item.id, active_vial_id=vial.id if vial else None,
        peptide_id=item.peptide_id, peptide_name=item.peptide.name, dose_value=item.dose, dose_unit=item.dose_unit,
        route=item.route.value, scheduled_date=scheduled_date, scheduled_time_of_day=item.time_of_day,
        status=status, logged_at=datetime.now(timezone.utc), injection_site=site, volume_ml=volume_ml,
    ))
    if vial is not None and volume_ml is not None:
        vial.volume_remaining_ml -= volume_ml
    session.commit()
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

    session.add(DoseLog(
        owner_id=uid, protocol_id=protocol_id, protocol_item_id=item.id, peptide_id=item.peptide_id,
        peptide_name=item.peptide.name, dose_value=item.dose, dose_unit=item.dose_unit, route=item.route.value,
        scheduled_date=scheduled_date, scheduled_time_of_day=item.time_of_day, status=DoseStatus.SKIPPED,
        logged_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return RedirectResponse("/today", status_code=303)

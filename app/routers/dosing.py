"""The Today view: what's due today, logging a dose (drawing down an Active Vial), skipping one."""

from datetime import date, datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import clock
from app.calculator import units as unit_math
from app.calendar.schedule import missed_items, occurrences
from app.db import get_session
from app.dosing.site import eligible_sites, recommend
from app.inventory import consumption, unit_use
from app.models import TIME_ORDER, ActiveVial, DoseLog, DoseStatus, DoseUnit, InjectionSite, InventoryItem, Protocol, ProtocolItem, User
from app.protocols import repeat
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
    """None when the dose can't be converted to mL (no dose value, or IU against mass with no IU-per-mg on the vial). The dose is
    converted into the vial's own unit and divided by its concentration (per mL, in that unit)."""
    if dose_value is None or not vial.concentration_mg_ml:
        return None
    in_vial_unit = unit_math.convert(dose_value, dose_unit.value, vial.vial_unit or "mg", vial.iu_per_mg)
    return None if in_vial_unit is None else in_vial_unit / vial.concentration_mg_ml


SYRINGE_UNITS_PER_ML = 100       # a U-100 insulin syringe: 100 units to the mL, so a unit is 0.01 mL whatever size the syringe is


def _syringe_units(session: Session, pi: ProtocolItem | None, dose: float | None) -> float | None:
    """What to draw to on the syringe for `dose` of this line's open syringe vial (2 mg at 5 mg/mL is 0.4 mL, 40 units). None when there is no
    open syringe vial or the dose cannot be turned into a volume; pens have no units."""
    if pi is None or pi.inventory_item_id is None or not dose:
        return None
    vial = _open_vial_for_item(session, pi.inventory_item_id)
    if vial is None or vial.dispensing_method.value != "syringe":
        return None
    volume = _dose_volume_ml(dose, pi.dose_unit, vial)
    return None if volume is None else round(volume * SYRINGE_UNITS_PER_ML, 1)


def _last_site_for_peptide(session: Session, uid: int, peptide_id: int) -> InjectionSite | None:
    log = session.scalar(
        select(DoseLog)
        .where(DoseLog.owner_id == uid, DoseLog.peptide_id == peptide_id, DoseLog.injection_site.is_not(None))
        .order_by(DoseLog.logged_at.desc())
    )
    return log.injection_site if log else None


def _last_dose_for_peptide(session: Session, uid: int, peptide_id: int) -> str | None:
    """The newest dose actually taken of this peptide, as "250 mcg on 10/28/2026" (None when none was ever logged with an amount)."""
    log = session.scalar(
        select(DoseLog)
        .where(DoseLog.owner_id == uid, DoseLog.peptide_id == peptide_id, DoseLog.status.in_((DoseStatus.ON_TIME, DoseStatus.LATE)),
               DoseLog.dose_value.is_not(None))
        .order_by(DoseLog.scheduled_date.desc(), DoseLog.id.desc())
    )
    return f"{log.dose_value:g} {log.dose_unit.value} on {log.scheduled_date.strftime('%m/%d/%Y')}" if log else None


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


def _back(form) -> str:
    """Where to go after a dose is logged, skipped or undone: the Journal when it asked (a path of this site only), else Today."""
    raw = str(form.get("next") or "")
    return raw if raw.startswith("/measurements?tab=journal") and "//" not in raw and "\\" not in raw else "/today"


def _log_item(session: Session, uid: int, protocol_id: int, item: ProtocolItem, scheduled_date: date, form) -> int | None:
    """Log one due item as taken on `scheduled_date` (drawing down its open vial, supplies and units as a Today log does). Returns the id of a
    vial that this dose emptied, else None. Does not commit."""
    # _own_protocol_item already confirmed this protocol exists and is owned by uid.
    protocol = session.get(Protocol, protocol_id)
    effective_dose, _step_no = _effective_dose(item, protocol, scheduled_date)
    def _typed(field: str) -> float | None:
        raw = str(form.get(field) or "").strip()
        if not raw:
            return None
        try:
            amount = float(raw)
        except ValueError:
            amount = 0.0
        if not 0 < amount < 1_000_000:                                  # also rejects nan and inf
            raise HTTPException(422, "Enter how many units as a number greater than 0.")
        return amount

    typed_dose, typed_units = _typed("dose"), _typed("syringe_units")  # the amount really taken, pre-filled from the protocol: in the dose's unit, or units on the syringe
    if typed_dose is not None:
        effective_dose = typed_dose

    vial = None
    volume_ml = None
    if item.inventory_item_id is not None:
        vial = _open_vial_for_item(session, item.inventory_item_id)
        if vial is not None:
            if typed_units is not None and vial.dispensing_method.value == "syringe" and vial.concentration_mg_ml:
                volume_ml = typed_units / SYRINGE_UNITS_PER_ML          # what was drawn decides the dose: units -> mL -> the vial's unit -> the protocol's unit
                taken = unit_math.convert(volume_ml * vial.concentration_mg_ml, vial.vial_unit or "mg", item.dose_unit.value, vial.iu_per_mg)
                if taken is not None:
                    effective_dose = round(taken, 6)
            else:
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

    status = DoseStatus.ON_TIME if scheduled_date == clock.today() else DoseStatus.LATE
    units_taken = None
    stock = session.get(InventoryItem, item.inventory_item_id) if item.inventory_item_id is not None else None
    if vial is None and unit_use.tracks_units(stock):
        share = unit_use.units_for_dose(stock, effective_dose, item.dose_unit)
        if share is not None:
            units_taken = unit_use.take(stock, share)
    supplies = consumption.use_for_shot(session, uid, vial.dispensing_method) if vial is not None else {}
    session.add(DoseLog(
        owner_id=uid, protocol_id=protocol_id, protocol_item_id=item.id, active_vial_id=vial.id if vial else None,
        peptide_id=item.peptide_id, peptide_name=item.peptide.name, dose_value=effective_dose,
        dose_unit=item.dose_unit, route=item.route.value, scheduled_date=scheduled_date,
        scheduled_time_of_day=item.time_of_day, status=status, logged_at=datetime.now(timezone.utc),
        injection_site=site, volume_ml=volume_ml, inventory_units=units_taken, stock_item_id=stock.id if units_taken else None, supplies_used=supplies or None,
        fiber_g=stock.fiber_g_per_dose if stock is not None and vial is None and stock.fiber_g_per_dose else None,
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
    return empty_vial_id


@router.get("/today")
def today_page(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
              uid: int = Depends(current_user_id)):
    repeat.roll_forward(session, uid, today)
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    occs = occurrences(protocols, today, today)
    all_due = [(occ, item) for occ in occs for item in occ.items]

    todays_logs = session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date == today)
        .order_by(DoseLog.logged_at)).all()
    logged_ids = {dl.protocol_item_id for dl in todays_logs}
    due = [(occ, item) for occ, item in all_due if item.protocol_item_id not in logged_ids]
    due.sort(key=lambda pair: TIME_ORDER[pair[1].time_of_day])          # Fasting first through Bedtime, then Any

    from app.routers.workouts import workouts_due_today
    workout_days_due = workouts_due_today(session, uid, today)
    from app.workouts import moves as workout_moves
    workout_move_dates = {day.id: workout_moves.free_dates(session, uid, today, workout_moves.origin_date(session, uid, day.id, today))
                          for day in workout_days_due}

    # Same traced body outline the Body page uses (not a bespoke shape here) -- the owner asked
    # for the injection-site picker to stop drawing its own crude blob and match the real figure.
    from app.routers.measurements import _silhouette_shape
    me_user = session.get(User, uid)
    silhouette_shape = _silhouette_shape(me_user.sex.value if me_user and me_user.sex else None)

    # Built from all_due (not the logged-filtered `due`) so the recommended-site data for an item
    # already logged today still surfaces on the page -- e.g. right after logging, so the JS/tests
    # reading this blob can see the mirrored recommendation for that peptide's *next* dose, even
    # though the just-logged occurrence itself no longer needs a picker (it dropped out of `due`).
    # DueItem.route holds the human label (e.g. "SubQ"), not the lowercase route value that
    # eligible_sites()/recommend() key off of ("subq") -- .lower() bridges that; every current
    # Route label lowercases to exactly its value.
    site_data = {}
    stocked = {pi.id: pi.inventory_item_id for p in protocols for pi in p.items}
    protocol_items_by_id = {pi.id: pi for p in protocols for pi in p.items}
    syringe_units = {}                                    # protocol line -> units to draw to, for every line with an open syringe vial
    for occ, item in all_due:
        units = _syringe_units(session, protocol_items_by_id.get(item.protocol_item_id), item.dose)
        if units is not None:
            syringe_units[item.protocol_item_id] = f"{units:g}"
    for occ, item in all_due:
        sites = eligible_sites(item.route.lower())
        if sites:
            last = _last_site_for_peptide(session, uid, item.peptide_id)
            calc = {"dose_value": f"{item.dose:g}" if item.dose else "", "dose_unit": item.unit, "inventory_item_id": stocked.get(item.protocol_item_id) or ""}
            site_data[item.protocol_item_id] = {
                "peptide": item.peptide,
                "dose": (f"{item.dose:g} {item.unit}" + (f" ({syringe_units[item.protocol_item_id]} units)" if item.protocol_item_id in syringe_units else "")) if item.dose else None,
                "dose_amount": syringe_units.get(item.protocol_item_id) or (f"{item.dose:g}" if item.dose else ""),
                "dose_unit": "units" if item.protocol_item_id in syringe_units else item.unit,
                "on_syringe": item.protocol_item_id in syringe_units,
                "last_dose": _last_dose_for_peptide(session, uid, item.peptide_id),
                "calc_url": "/calculator?" + urlencode({k: v for k, v in calc.items() if v != ""}),
                "sites": [{"value": s.value, "label": s.label} for s in sites],
                "last": last.value if last else None,
                "recommended": recommend(last, item.route.lower()).value if recommend(last, item.route.lower()) else None,
            }

    volume_text = {}
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
        volume_text[item.protocol_item_id] = f"{verb} {vol:.2f} mL" + (f" ({syringe_units[item.protocol_item_id]} units)" if item.protocol_item_id in syringe_units else "")

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
        "volume_text": volume_text, "syringe_units": syringe_units, "empty_vial": empty_vial, "workout_days_due": workout_days_due, "workout_move_dates": workout_move_dates, "workouts_coming_up": workout_moves.coming_up(session, uid, today),
        "logged_today": todays_logs, "silhouette_shape": silhouette_shape,
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
    if scheduled_date > clock.today():
        raise HTTPException(422, "Can't log a dose for a future date.")

    # A double-click, browser back-button resubmit, two open tabs, or a stale catch-up link can
    # each race to post this same (item, date) twice -- the second one is a silent no-op, not an
    # error, so it looks like a duplicate click just did nothing rather than double-depleting a
    # vial or duplicating history.
    if _existing_log(session, uid, item.id, scheduled_date) is not None:
        return RedirectResponse(_back(form), status_code=303)

    empty_vial_id = _log_item(session, uid, protocol_id, item, scheduled_date, form)
    session.commit()
    if empty_vial_id is not None and not form.get("next"):
        return RedirectResponse(f"/today?empty_vial={empty_vial_id}", status_code=303)
    return RedirectResponse(_back(form), status_code=303)


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
        return RedirectResponse(_back(form), status_code=303)

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
    return RedirectResponse(_back(form), status_code=303)


@router.post("/today/undo")
async def undo_dose(request: Request, session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    """Removes a logged dose or skip, from today or any earlier day (the Journal edits past days), so a wrong entry can be put right
    and logged again. A dose that drew down an Active Vial gets that volume restored, so undoing never leaves a vial permanently short."""
    form = await request.form()
    try:
        log_id = int(form.get("dose_log_id", ""))
    except (TypeError, ValueError):
        raise HTTPException(404)
    log = session.get(DoseLog, log_id)
    if log is None or log.owner_id != uid:
        raise HTTPException(404, "Dose log not found")

    if log.active_vial_id is not None and log.volume_ml is not None:
        vial = session.get(ActiveVial, log.active_vial_id)
        if vial is not None:
            vial.volume_remaining_ml = round(vial.volume_remaining_ml + log.volume_ml, 4)

    consumption.give_back(session, uid, log.supplies_used)
    if log.inventory_units:
        stock_id = log.stock_item_id
        if stock_id is None and log.protocol_item_id is not None:           # a dose logged before the item was recorded: the line's item is the best guess
            pitem = session.get(ProtocolItem, log.protocol_item_id)
            stock_id = pitem.inventory_item_id if pitem is not None else None
        stock = session.get(InventoryItem, stock_id) if stock_id is not None else None
        if stock is not None:
            unit_use.give_back(stock, log.inventory_units)

    session.delete(log)
    session.commit()
    return RedirectResponse(_back(form), status_code=303)


@router.post("/journal/doses/mark-all")
async def mark_day_taken(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Log every dose that was due on a day and has no log yet as taken (for the day you fell asleep before logging). Doses already logged or skipped stay."""
    form = await request.form()
    try:
        day = date.fromisoformat(str(form.get("date", "")))
    except ValueError:
        raise HTTPException(422, "A valid date is required.")
    if day > clock.today():
        raise HTTPException(422, "Can't log a dose for a future date.")
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide), selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs), selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item))).all()
    for occ in occurrences(protocols, day, day):
        for due in occ.items:
            item = _own_protocol_item(session, occ.protocol_id, due.protocol_item_id, uid)
            if item is not None and _existing_log(session, uid, item.id, day) is None:
                _log_item(session, uid, occ.protocol_id, item, day, {})
                session.flush()
    session.commit()
    return RedirectResponse(_back(form), status_code=303)

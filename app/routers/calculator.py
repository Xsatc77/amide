"""Reconstitution calculator: vial + BAC water + dose -> concentration, draw volume, syringe units.

The math lives in app.calculator.reconstitution; this router only reads request params, calls it, and
serves the page and its JSON endpoints. The `/calculator/reconstitute` route is the one place here that
writes to the database: it creates an ActiveVial and decrements the source InventoryItem's count.
"""

from dataclasses import asdict
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.calculator.reconstitution import SYRINGE_CAPACITIES_UNITS, compute, water_for_target_units
from app.db import get_session
from app.models import ActiveVial, DoseUnit, InventoryItem, Medium, Protocol, ProtocolItem
from app.templating import templates

router = APIRouter()

DEFAULTS = {"vial_mg": 10.0, "water_ml": 2.0, "dose_value": 250.0, "dose_unit": "mcg", "syringe_ml": 1.0}


def _num(raw, default=None) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _syringe_ml(raw) -> float:
    value = _num(raw)
    return value if value in SYRINGE_CAPACITIES_UNITS else DEFAULTS["syringe_ml"]


@router.get("/api/calculator/compute")
def api_compute(vial_mg: str = "", water_ml: str = "", dose_value: str = "", dose_unit: str = "mg",
                syringe_ml: str = "1.0"):
    result = compute(_num(vial_mg), _num(water_ml), _num(dose_value), dose_unit, _syringe_ml(syringe_ml))
    return asdict(result)


@router.get("/api/calculator/target-water")
def api_target_water(vial_mg: str = "", dose_mg: str = "", target_units: str = ""):
    return {"water_ml": water_for_target_units(_num(vial_mg), _num(dose_mg), _num(target_units))}


@router.get("/calculator")
def calculator_page(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    q = request.query_params
    state = {k: q.get(k, str(default)) for k, default in DEFAULTS.items()}
    selected_item_id = q.get("inventory_item_id")

    inventory = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.owner_id == uid, InventoryItem.medium == Medium.LYOPHILIZED,
              InventoryItem.vial_size_mg.is_not(None), InventoryItem.vial_size_unit == DoseUnit.MG)
        .order_by(InventoryItem.name.collate("NOCASE"))
    ).all()

    if selected_item_id and any(str(i.id) == selected_item_id for i in inventory):
        # The selected item's own vial size always wins over any ?vial_mg= query override --
        # otherwise the confirmation modal (built from this field) could save a concentration
        # that doesn't match the item's real amount.
        state["vial_mg"] = "%g" % next(i.vial_size_mg for i in inventory if str(i.id) == selected_item_id)

    syringe_ml = _syringe_ml(state["syringe_ml"])
    result = compute(_num(state["vial_mg"]), _num(state["water_ml"]), _num(state["dose_value"]),
                     state["dose_unit"], syringe_ml)

    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid)
        .options(selectinload(Protocol.items).selectinload(ProtocolItem.peptide))
        .order_by(Protocol.name)
    ).all()

    data = {
        # A list of [mL, units] pairs, not a dict: JSON object keys are always strings, and JS's
        # Number(1.0) stringifies to "1" (not "1.0"), which would silently miss the 1.0 mL entry on
        # lookup. The page builds a JS Map from this list instead, where numeric keys work correctly.
        "syringe_capacities": list(SYRINGE_CAPACITIES_UNITS.items()),
        "inventory": [{"id": i.id, "name": i.name, "vial_mg": i.vial_size_mg, "count": i.count} for i in inventory],
        "protocol_doses": [
            {"protocol": p.name, "peptide": it.peptide.name, "dose": it.dose, "unit": it.dose_unit.value}
            for p in protocols for it in p.items if it.dose is not None
        ],
        "default_discard_days": request.state.user.default_discard_days or 28,
    }
    return templates.TemplateResponse(request, "calculator/calculator.html", {
        "state": state, "result": result, "data": data, "capacities": SYRINGE_CAPACITIES_UNITS,
        "selected_item_id": int(selected_item_id) if selected_item_id and selected_item_id.isdigit() else None,
        "reconstitute_error": q.get("reconstitute_error"),
    })


@router.post("/calculator/reconstitute")
async def reconstitute(request: Request, session: Session = Depends(get_session),
                       uid: int = Depends(current_user_id)):
    form = await request.form()
    try:
        item_id = int(form.get("inventory_item_id", ""))
    except (TypeError, ValueError):
        raise HTTPException(status_code=404)
    item = session.get(InventoryItem, item_id)
    if item is None or item.owner_id != uid or item.vial_size_unit != DoseUnit.MG:
        raise HTTPException(status_code=404)

    water_ml = _num(form.get("water_ml"))
    dose_value = _num(form.get("dose_value"))
    dose_unit = str(form.get("dose_unit", "mg"))
    discard_by_raw = str(form.get("discard_by", ""))

    errors = []
    if item.count <= 0:
        errors.append("This item has none left in stock to reconstitute.")
    if water_ml is None or water_ml <= 0:
        errors.append("Enter the water added.")
    if dose_value is None or dose_value <= 0:
        errors.append("Enter a dose amount.")
    try:
        discard_by = date.fromisoformat(discard_by_raw)
    except ValueError:
        discard_by = None
        errors.append("Enter a valid discard-by date.")

    def error_redirect(message: str) -> RedirectResponse:
        return RedirectResponse(
            f"/calculator?inventory_item_id={item.id}&reconstitute_error={quote(message)}", status_code=303)

    if errors:
        return error_redirect(" ".join(errors))

    result = compute(item.vial_size_mg, water_ml, dose_value, dose_unit, 1.0)
    if result.concentration_mg_ml is None:
        return error_redirect("Could not compute a concentration from these values.")

    session.add(ActiveVial(
        owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=result.concentration_mg_ml,
        water_ml=water_ml, dose_value=dose_value, dose_unit=DoseUnit(dose_unit),
        doses_total=result.doses_per_vial, date_mixed=date.today(), discard_by=discard_by,
    ))
    item.count -= 1
    session.commit()
    return RedirectResponse("/inventory#active-vials", status_code=303)

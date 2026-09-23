"""Reconstitution calculator: vial + BAC water + dose -> concentration, draw volume, syringe units.

The math lives in app.calculator.reconstitution; this router only reads request params, calls it, and
serves the page and its JSON endpoints. Everything here is read-only.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.calculator.reconstitution import SYRINGE_CAPACITIES_UNITS, compute, water_for_target_units
from app.db import get_session
from app.models import InventoryItem, Medium, Protocol, ProtocolItem
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
    syringe_ml = _syringe_ml(state["syringe_ml"])
    result = compute(_num(state["vial_mg"]), _num(state["water_ml"]), _num(state["dose_value"]),
                     state["dose_unit"], syringe_ml)

    inventory = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.owner_id == uid, InventoryItem.medium == Medium.LYOPHILIZED,
              InventoryItem.vial_size_mg.is_not(None))
        .order_by(InventoryItem.name.collate("NOCASE"))
    ).all()

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
        "inventory": [{"id": i.id, "name": i.name, "vial_mg": i.vial_size_mg} for i in inventory],
        "protocol_doses": [
            {"protocol": p.name, "peptide": it.peptide.name, "dose": it.dose, "unit": it.dose_unit.value}
            for p in protocols for it in p.items if it.dose is not None
        ],
    }
    return templates.TemplateResponse(request, "calculator/calculator.html", {
        "state": state, "result": result, "data": data, "capacities": SYRINGE_CAPACITIES_UNITS,
    })

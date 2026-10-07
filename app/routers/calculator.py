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
from app.calculator import units as unit_math
from app.calculator.reconstitution import SYRINGE_CAPACITIES_UNITS, compute, water_for_target_units
from app.calculator.teaching import explain
from app.db import get_session
from app.inventory.consumption import apply_plan, plan_reconstitution
from app.library.matching import match_name
from app.models import (
    ActiveVial, Category, DispensingMethod, DoseUnit, InventoryItem, Medium, Peptide, PeptideDosingTier, Protocol, ProtocolItem,
)
from app.templating import templates

router = APIRouter()

DEFAULTS = {"vial_mg": 10.0, "water_ml": 2.0, "dose_value": 250.0, "dose_unit": "mcg", "syringe_ml": 1.0}


def _library_match(name: str, peptides: list[Peptide]) -> int | None:
    """The library peptide an inventory item's name points at (an exact, normalized or related match), or None."""
    found = match_name(name, peptides)
    return found.cards[0].id if found and found.how in ("exact", "normalized", "related") and found.cards else None


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
                syringe_ml: str = "1.0", vial_unit: str = "mg", iu_per_mg: str = ""):
    """`vial_mg` is the vial amount in `vial_unit` (mg, mcg or IU; the name is historic). The answer carries a `teach` block: the
    steps with these numbers, what makes the draw unusable, and the water amounts that would work."""
    syringe = _syringe_ml(syringe_ml)
    result = compute(_num(vial_mg), _num(water_ml), _num(dose_value), dose_unit, syringe, vial_unit=vial_unit, iu_per_mg=_num(iu_per_mg))
    out = asdict(result)
    out["teach"] = explain(vial=_num(vial_mg), vial_unit=vial_unit, water_ml=_num(water_ml), dose_value=_num(dose_value), dose_unit=dose_unit,
                           syringe_ml=syringe, iu_per_mg=_num(iu_per_mg))
    return out


@router.get("/api/calculator/target-water")
def api_target_water(vial_mg: str = "", dose_mg: str = "", target_units: str = "", dose_value: str = "", dose_unit: str = "mg",
                     vial_unit: str = "mg", iu_per_mg: str = ""):
    if dose_value:                                    # the dose in its own unit, converted into the vial's unit
        dose = unit_math.convert(_num(dose_value) or 0, dose_unit, vial_unit, _num(iu_per_mg))
        return {"water_ml": water_for_target_units(_num(vial_mg), dose, _num(target_units))}
    return {"water_ml": water_for_target_units(_num(vial_mg), _num(dose_mg), _num(target_units))}


_LEVELS = ("Beginner", "Intermediate", "Advanced")


def _library_data(session: Session) -> tuple[list[Peptide], list[dict]]:
    """Every peptide in the master list with its Beginner / Intermediate / Advanced dose where the library gives one as a plain amount
    (like "500mcg" or "3 IU"); weight-based or other doses come through as text only."""
    peptides = session.scalars(select(Peptide).order_by(Peptide.name.collate("NOCASE"))).all()
    tiers: dict[int, dict] = {}
    for t in session.scalars(select(PeptideDosingTier)):
        parsed = unit_math.parse_dose_text(t.dose_text)
        tiers.setdefault(t.peptide_id, {})[t.level.value] = {
            "amount": parsed[0] if parsed else None, "unit": parsed[1] if parsed else None, "text": t.dose_text, "frequency": t.frequency_text}
    return peptides, [{"id": p.id, "name": p.name, "iu_per_mg": unit_math.default_iu_per_mg(p.name),
                       "tiers": {k: tiers.get(p.id, {}).get(k) for k in _LEVELS if tiers.get(p.id, {}).get(k)}} for p in peptides]


def _bac_data(session: Session, uid: int, today: date) -> list[dict]:
    items = session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid, InventoryItem.category == Category.BAC_WATER)
                            .order_by(InventoryItem.bac_priority.is_(None), InventoryItem.bac_priority, InventoryItem.name.collate("NOCASE"))).all()
    out = []
    for item in items:
        vial = session.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == item.id, ActiveVial.owner_id == uid, ActiveVial.discarded_at.is_(None),
                                                         ActiveVial.discard_by >= today, ActiveVial.volume_remaining_ml > 0.001)
                              .order_by(ActiveVial.discard_by, ActiveVial.id))
        out.append({"id": item.id, "name": item.name, "priority": item.bac_priority, "in_stock": item.available_count,
                    "bottle_ml": item.volume_ml,
                    "open": {"ml_left": round(vial.volume_remaining_ml, 2), "discard_by": vial.discard_by.isoformat()} if vial else None})
    return out


@router.get("/calculator")
def calculator_page(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    q = request.query_params
    state = {k: q.get(k, str(default)) for k, default in DEFAULTS.items()}
    state["vial_unit"] = q.get("vial_unit") if q.get("vial_unit") in unit_math.UNITS else "mg"
    state["iu_per_mg"] = q.get("iu_per_mg", "")
    selected_item_id = q.get("inventory_item_id")

    inventory = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.owner_id == uid, InventoryItem.category == Category.MEDICINE,
              InventoryItem.medium == Medium.LYOPHILIZED, InventoryItem.vial_size_mg.is_not(None))
        .order_by(InventoryItem.name.collate("NOCASE"))
    ).all()
    inventory = [i for i in inventory if i.available_count > 0]

    if selected_item_id and any(str(i.id) == selected_item_id for i in inventory):
        # The selected item's own vial size always wins over any ?vial_mg= query override --
        # otherwise the confirmation modal (built from this field) could save a concentration
        # that doesn't match the item's real amount.
        chosen = next(i for i in inventory if str(i.id) == selected_item_id)
        state["vial_mg"] = "%g" % chosen.vial_size_mg
        state["vial_unit"] = chosen.vial_size_unit.value
        if chosen.iu_per_mg:
            state["iu_per_mg"] = "%g" % chosen.iu_per_mg

    peptides, library = _library_data(session)
    syringe_ml = _syringe_ml(state["syringe_ml"])
    result = compute(_num(state["vial_mg"]), _num(state["water_ml"]), _num(state["dose_value"]),
                     state["dose_unit"], syringe_ml, vial_unit=state["vial_unit"], iu_per_mg=_num(state["iu_per_mg"]))

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
        "inventory": [{"id": i.id, "name": i.name, "vial_mg": i.vial_size_mg, "unit": i.vial_size_unit.value, "count": i.available_count,
                       "iu_per_mg": i.iu_per_mg or unit_math.default_iu_per_mg(i.name), "peptide_id": _library_match(i.name, peptides)} for i in inventory],
        "library": library,
        "bac": _bac_data(session, uid, date.today()),
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
    if item is None or item.owner_id != uid or item.category != Category.MEDICINE or item.vial_size_mg is None:
        raise HTTPException(status_code=404)

    water_ml = _num(form.get("water_ml"))
    dose_value = _num(form.get("dose_value"))
    dose_unit = str(form.get("dose_unit", "mg"))
    discard_by_raw = str(form.get("discard_by", ""))
    iu_per_mg = _num(form.get("iu_per_mg")) or item.iu_per_mg
    bac_raw = str(form.get("bac_item_id") or "").strip()

    errors = []
    if item.available_count <= 0:
        errors.append("This item has none left in stock to reconstitute.")
    if dose_unit not in unit_math.UNITS:
        errors.append("Choose mg, mcg or IU for the dose.")
    if bac_raw and not bac_raw.isdigit():
        errors.append("That is not one of your BAC Water items.")
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

    vial_unit = item.vial_size_unit.value
    result = compute(item.vial_size_mg, water_ml, dose_value, dose_unit, 1.0, vial_unit=vial_unit, iu_per_mg=iu_per_mg)
    if "iu_per_mg" in result.problems:
        return error_redirect(f"This vial is in {vial_unit} but the dose is in {dose_unit}: enter the IU per mg for this product (about 3 for HGH).")
    if result.concentration is None:
        return error_redirect("Could not compute a concentration from these values.")

    load_into_pen = bool(str(form.get("load_into_pen", "")).strip())
    plan = plan_reconstitution(session, uid, water_ml=water_ml, pen=load_into_pen,                    # BAC water and supplies: all there, or nothing happens
                               bac_item_id=int(bac_raw) if bac_raw else None)
    if plan.errors:
        return error_redirect(". ".join(plan.errors))
    apply_plan(session, uid, plan, water_ml=water_ml, discard_days=request.state.user.default_discard_days or 28)
    session.add(ActiveVial(
        owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=result.concentration, vial_unit=vial_unit,
        iu_per_mg=iu_per_mg if ("IU" in (vial_unit, dose_unit)) else None,
        water_ml=water_ml, dose_value=dose_value, dose_unit=DoseUnit(dose_unit),
        doses_total=result.doses_per_vial, date_mixed=date.today(), discard_by=discard_by,
        volume_remaining_ml=water_ml,
        dispensing_method=DispensingMethod.PEN if load_into_pen else DispensingMethod.SYRINGE,
    ))
    item.reconstituted_count += 1
    if iu_per_mg and "IU" in (vial_unit, dose_unit):
        item.iu_per_mg = iu_per_mg                                         # the item remembers its factor for next time and for dose totals
    session.commit()
    return RedirectResponse("/inventory#active-vials", status_code=303)

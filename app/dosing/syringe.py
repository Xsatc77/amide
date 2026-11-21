"""The syringe a shot is drawn with: how many units of it a dose is, and which one of the person's syringes the shot uses up.

A syringe in Inventory (a Supply of the Dosing Syringe type) says how many units make a millilitre: 100 on a U-100 insulin syringe, which is the
default, 40 on a U-40. The same dose is the same volume on any of them, so the units to draw to depend on the syringe. Picking a syringe when a
shot is logged is what takes that one from the stock."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calculator import units as unit_math
from app.models import Category, DoseUnit, InventoryItem, Medium, SupplyType

DEFAULT_UNITS_PER_ML = 100.0                    # U-100


@dataclass(frozen=True)
class Concentration:
    per_ml: float                               # the amount in 1 mL, in `unit`
    unit: str
    iu_per_mg: float | None = None


def units_per_ml(item: InventoryItem | None) -> float:
    return (item.syringe_units_per_ml if item is not None else None) or DEFAULT_UNITS_PER_ML


def label(per_ml: float) -> str:
    return f"U-{per_ml:g}"


def concentration(vial, stock: InventoryItem | None) -> Concentration | None:
    """What is in a mL of the medicine being drawn: the open vial's strength, else a liquid item's (its amount over its volume). None when unknown."""
    if vial is not None:
        return Concentration(vial.concentration_mg_ml, vial.vial_unit or "mg", vial.iu_per_mg) if vial.concentration_mg_ml else None
    if stock is not None and stock.category == Category.MEDICINE and stock.medium == Medium.LIQUID and stock.vial_size_mg and stock.volume_ml:
        return Concentration(stock.vial_size_mg / stock.volume_ml, stock.vial_size_unit.value, stock.iu_per_mg or unit_math.default_iu_per_mg(stock.name))
    return None


def volume_for(dose: float | None, dose_unit: DoseUnit, conc: Concentration | None) -> float | None:
    """The mL that hold `dose`, or None when it cannot be worked out (no dose or strength, or a unit that does not convert)."""
    if not dose or conc is None or not conc.per_ml:
        return None
    amount = unit_math.convert(dose, dose_unit.value, conc.unit, conc.iu_per_mg)
    return None if amount is None else amount / conc.per_ml


def stocked(session: Session, uid: int) -> list[InventoryItem]:
    """The person's dosing syringes that are in stock, in the order a shot would use them."""
    return list(session.scalars(select(InventoryItem).where(
        InventoryItem.owner_id == uid, InventoryItem.category == Category.SUPPLY, InventoryItem.supply_type == SupplyType.DOSING_SYRINGE,
        InventoryItem.count > 0).order_by(InventoryItem.id)))


def options(session: Session, uid: int) -> list[dict]:
    """The dropdown's choices. With no syringe in stock there is one plain U-100 choice that takes nothing from Inventory."""
    out = [{"id": i.id, "label": f"{i.name} ({label(units_per_ml(i))}) · {i.count} left", "units_per_ml": units_per_ml(i), "capacity_ml": i.volume_ml}
           for i in stocked(session, uid)]
    return out or [{"id": "", "label": "U-100 (no syringe in Inventory)", "units_per_ml": DEFAULT_UNITS_PER_ML, "capacity_ml": None}]


def chosen(session: Session, uid: int, raw) -> InventoryItem | None:
    """The syringe the person picked, if it is one of their own in stock; else None."""
    try:
        item = session.get(InventoryItem, int(str(raw or "").strip()))
    except ValueError:
        return None
    ok = (item is not None and item.owner_id == uid and item.category == Category.SUPPLY and item.supply_type == SupplyType.DOSING_SYRINGE and item.count > 0)
    return item if ok else None


def units_drawn(session: Session, log) -> float | None:
    """The units this logged shot was drawn to on its syringe: the mL it took times the units to the mL of the syringe it used (a U-100 when
    that one is not recorded or has since been deleted). None when no volume was worked out for it, or it came from a pen, which has no units."""
    if not log.volume_ml:
        return None
    if log.active_vial is not None and log.active_vial.dispensing_method.value != "syringe":
        return None
    per_ml = DEFAULT_UNITS_PER_ML
    for key in (log.supplies_used or {}):
        try:
            item = session.get(InventoryItem, int(key))
        except (TypeError, ValueError):
            continue
        if item is not None and item.supply_type == SupplyType.DOSING_SYRINGE:
            per_ml = units_per_ml(item)
            break
    return round(log.volume_ml * per_ml, 1)

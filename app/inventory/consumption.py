"""What reconstituting a vial, and loading it into a pen, uses up: BAC water and supplies.

Everything needed is checked first. If anything is missing nothing is used and the reason is returned, so a half-finished
reconstitution can never eat some of your stock."""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.sessions import now_utc
from app.models import ActiveVial, Category, DispensingMethod, InventoryItem, SupplyType

RECON_SUPPLIES = {SupplyType.ALCOHOL_PAD: 4, SupplyType.RECON_SYRINGE: 1}
PEN_SUPPLIES = {SupplyType.ALCOHOL_PAD: 2, SupplyType.PEN_VIAL: 1, SupplyType.DOSING_SYRINGE: 1, SupplyType.PEN_NEEDLE: 1}
DEFAULT_BOTTLE_ML = 30.0
EMPTY_EPSILON = 0.001


def _plural(label: str) -> str:
    return label + "s"


@dataclass
class Plan:
    errors: list[str] = field(default_factory=list)
    supplies: list[tuple[InventoryItem, int]] = field(default_factory=list)       # (item, how many to take from it)
    open_vial: ActiveVial | None = None
    new_bottle: InventoryItem | None = None
    bottle_ml: float = 0.0


def _needs(*, recon: bool, pen: bool) -> dict[SupplyType, int]:
    needs: dict[SupplyType, int] = {}
    for wanted, on in ((RECON_SUPPLIES, recon), (PEN_SUPPLIES, pen)):
        if on:
            for kind, qty in wanted.items():
                needs[kind] = needs.get(kind, 0) + qty
    return needs


def _plan_supplies(session: Session, uid: int, needs: dict[SupplyType, int], plan: Plan) -> None:
    for kind, need in needs.items():
        items = session.scalars(select(InventoryItem).where(
            InventoryItem.owner_id == uid, InventoryItem.category == Category.SUPPLY, InventoryItem.supply_type == kind,
            InventoryItem.count > 0).order_by(InventoryItem.id)).all()
        have = sum(i.count for i in items)
        if have == 0:
            plan.errors.append(f"No {_plural(kind.label)} Available")
        elif have < need:
            plan.errors.append(f"Not enough {_plural(kind.label)}: {have} available, {need} needed")
        else:
            left = need
            for item in items:
                take = min(item.count, left)
                plan.supplies.append((item, take))
                left -= take
                if not left:
                    break


def _plan_bac(session: Session, uid: int, water_ml: float, today: date, plan: Plan) -> None:
    """An open BAC vial with enough water left first; otherwise the best-ranked bottle in stock (rank 1 first, unranked last)."""
    open_vials = session.scalars(
        select(ActiveVial).join(InventoryItem, ActiveVial.inventory_item_id == InventoryItem.id)
        .where(ActiveVial.owner_id == uid, InventoryItem.category == Category.BAC_WATER, ActiveVial.discarded_at.is_(None),
               ActiveVial.discard_by >= today, ActiveVial.volume_remaining_ml >= water_ml - EMPTY_EPSILON)
        .order_by(ActiveVial.discard_by, ActiveVial.id)).all()
    if open_vials:
        plan.open_vial = open_vials[0]
        return
    stock = [i for i in session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid, InventoryItem.category == Category.BAC_WATER)
                                        .order_by(InventoryItem.id)) if i.available_count > 0]
    if not stock:
        plan.errors.append("No BAC Water Available")
        return
    stock.sort(key=lambda i: (i.bac_priority is None, i.bac_priority or 0, i.id))
    bottle = stock[0]
    size = bottle.volume_ml or DEFAULT_BOTTLE_ML
    if size < water_ml - EMPTY_EPSILON:
        plan.errors.append(f"Not enough BAC Water: the next bottle ({bottle.name}) holds {size:g} mL and {water_ml:g} mL is needed")
        return
    plan.new_bottle, plan.bottle_ml = bottle, size


def plan_reconstitution(session: Session, uid: int, *, water_ml: float, pen: bool, today: date | None = None) -> Plan:
    today = today or date.today()
    plan = Plan()
    _plan_bac(session, uid, water_ml, today, plan)
    _plan_supplies(session, uid, _needs(recon=True, pen=pen), plan)
    return plan


def plan_pen_conversion(session: Session, uid: int) -> Plan:
    plan = Plan()
    _plan_supplies(session, uid, _needs(recon=False, pen=True), plan)
    return plan


def apply_plan(session: Session, uid: int, plan: Plan, *, water_ml: float = 0.0, discard_days: int = 28, today: date | None = None) -> None:
    """Use up what the plan chose. Call only when `plan.errors` is empty; the caller commits."""
    from datetime import timedelta
    today = today or date.today()
    for item, take in plan.supplies:
        item.count -= take
    if plan.open_vial is not None:
        vial = plan.open_vial
        vial.volume_remaining_ml = max(vial.volume_remaining_ml - water_ml, 0.0)
        if vial.volume_remaining_ml <= EMPTY_EPSILON:
            vial.discarded_at = now_utc()                                  # used up: it leaves the Active Vials list
    elif plan.new_bottle is not None:
        bottle = plan.new_bottle
        bottle.reconstituted_count += 1                                    # one bottle off the shelf
        remaining = max(plan.bottle_ml - water_ml, 0.0)
        session.add(ActiveVial(
            owner_id=uid, inventory_item_id=bottle.id, water_ml=plan.bottle_ml, volume_remaining_ml=remaining, date_mixed=today,
            discard_by=today + timedelta(days=discard_days), dispensing_method=DispensingMethod.SYRINGE,
            discarded_at=now_utc() if remaining <= EMPTY_EPSILON else None))

"""Stock for reconstitution tests: supply items of each type and BAC water items with a bottle size, priority and arrivals."""

from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Category, InventoryItem, Order, OrderItem, SupplyType, User

ALL_TYPES = (SupplyType.RECON_SYRINGE, SupplyType.DOSING_SYRINGE, SupplyType.ALCOHOL_PAD, SupplyType.PEN_VIAL, SupplyType.PEN_NEEDLE)


def make_supply(uid: int, name: str, supply_type: SupplyType | None, count: int) -> int:
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=Category.SUPPLY, owner_id=uid, count=count, supply_type=supply_type)
        s.add(item)
        s.commit()
        return item.id


def make_bac(uid: int, name: str, *, priority: int | None = None, bottles: int = 1, volume: float | None = 30.0, arrived: date = date(2026, 8, 10)) -> int:
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=Category.BAC_WATER, owner_id=uid, count=0, bac_priority=priority, volume_ml=volume)
        s.add(item)
        s.flush()
        if bottles > 0:
            order = Order(order_date=arrived, arrival_date=arrived)
            s.add(order)
            s.flush()
            order.items.append(OrderItem(inventory_item_id=item.id, quantity=bottles, received_quantity=bottles))
        s.commit()
        return item.id


def stock_everything(uid: int, *, pads=20, recon_syringes=10, dosing_syringes=10, pen_vials=10, pen_needles=10, bac_bottles=3) -> dict:
    """Every supply a reconstitution (with or without a pen) can need, plus BAC water with a bottle size. Returns the item ids."""
    return {
        "pads": make_supply(uid, "Test alcohol pads", SupplyType.ALCOHOL_PAD, pads),
        "recon": make_supply(uid, "Test recon syringes", SupplyType.RECON_SYRINGE, recon_syringes),
        "dosing": make_supply(uid, "Test dosing syringes", SupplyType.DOSING_SYRINGE, dosing_syringes),
        "pen_vial": make_supply(uid, "Test pen cartridges", SupplyType.PEN_VIAL, pen_vials),
        "pen_needle": make_supply(uid, "Test pen needles", SupplyType.PEN_NEEDLE, pen_needles),
        "bac": make_bac(uid, "Test BAC water", priority=1, bottles=bac_bottles),
    }


def counts(ids: dict) -> dict:
    with SessionLocal() as s:
        out = {}
        for key, item_id in ids.items():
            item = s.get(InventoryItem, item_id)
            out[key] = item.available_count if item.category != Category.SUPPLY else item.count
        return out


def first_user() -> int:
    with SessionLocal() as s:
        return s.scalar(select(User.id))

"""When will the stock of a peptide run out? Sealed vials plus what is left in open ones, against what the active protocols use."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calculator import units as unit_math
from app.models import ActiveVial, Frequency, InventoryItem, Protocol, ProtocolItem
from app.protocols.status import Status, current_step, current_week, protocol_status

ALERT_DAYS = 14                      # a dashboard alert when the stock runs out within this many days


@dataclass(frozen=True)
class RunOut:
    days: int
    date: date


def _doses_per_day(item: ProtocolItem) -> float | None:
    f = item.frequency
    if f is Frequency.DAILY:
        return 1.0
    if f is Frequency.EOD:
        return 0.5
    if f is Frequency.EVERY_N_DAYS and item.every_n_days:
        return 1.0 / item.every_n_days
    if f is Frequency.WEEKDAYS and item.weekdays:
        return len(set(item.weekdays)) / 7
    if f is Frequency.WEEKLY:
        return 1.0 / 7
    return None                      # as needed: no schedule to project


def _dose_now(item: ProtocolItem, protocol: Protocol, today: date) -> float | None:
    if protocol.titration_enabled and item.steps:
        step = current_step(item.steps, current_week(protocol.start_date, today))
        if step is not None:
            return step.dose
    return item.dose


def runs_out(session: Session, uid: int, today: date) -> dict[int, RunOut]:
    """inventory item id -> when its stock runs out, for the user's items that an active, scheduled protocol uses.
    Items nothing active uses, as-needed doses and items with no vial size get no estimate."""
    protocols = session.scalars(select(Protocol).where(Protocol.owner_id == uid)).all()
    use: dict[int, float] = {}                      # inventory item id -> amount per day, in that item's own vial unit
    items = {i.id: i for i in session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid))}
    for p in protocols:
        if protocol_status(p, today) is not Status.ACTIVE:
            continue
        for it in p.items:
            inv = items.get(it.inventory_item_id) if it.inventory_item_id else None
            per_day, dose = _doses_per_day(it), _dose_now(it, p, today)
            if inv is None or inv.vial_size_mg is None or per_day is None or dose is None:
                continue
            factor = inv.iu_per_mg or unit_math.default_iu_per_mg(inv.name)
            amount = unit_math.convert(dose, it.dose_unit.value, inv.vial_size_unit.value, factor)
            if amount is None:
                continue
            use[inv.id] = use.get(inv.id, 0.0) + amount * per_day
    open_left: dict[int, float] = {}
    for v in session.scalars(select(ActiveVial).where(ActiveVial.owner_id == uid, ActiveVial.discarded_at.is_(None), ActiveVial.inventory_item_id.in_(list(use) or [0]))):
        open_left[v.inventory_item_id] = open_left.get(v.inventory_item_id, 0.0) + (v.volume_remaining_ml or 0.0) * v.concentration_mg_ml
    out: dict[int, RunOut] = {}
    for item_id, per_day in use.items():
        if per_day <= 0:
            continue
        inv = items[item_id]
        stock = max(inv.available_count, 0) * inv.vial_size_mg + open_left.get(item_id, 0.0)
        days = int(stock / per_day + 1e-9)
        out[item_id] = RunOut(days=days, date=today + timedelta(days=days))
    return out

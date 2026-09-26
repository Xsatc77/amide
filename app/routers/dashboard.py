"""The Dashboard: the app's homepage -- today's schedule, alerts, cost/adherence snapshots, and
placeholders for not-yet-built widgets. Read-only; every widget reuses an existing query shape."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.alerts import expiration_alerts, low_stock_alerts, shipment_alerts
from app.auth.deps import current_user_id
from app.calendar.schedule import occurrences
from app.db import get_session
from app.models import (
    ActiveVial, Category, DoseLog, DoseStatus, InventoryItem, Order, OrderItem, Protocol,
    ProtocolItem, User,
)
from app.protocols.status import Status, protocol_status
from app.routers.protocols import get_today
from app.templating import templates

router = APIRouter()


def _todays_schedule(session: Session, uid: int, today: date) -> list[dict]:
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    occs = occurrences(protocols, today, today)
    due = [(occ, item) for occ in occs for item in occ.items]
    logs = {dl.protocol_item_id: dl for dl in session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date == today))}
    rows = []
    for occ, item in due:
        log = logs.get(item.protocol_item_id)
        status = "Due"
        if log is not None:
            status = "Logged" if log.status in (DoseStatus.ON_TIME, DoseStatus.LATE) else "Skipped"
        rows.append({"peptide": item.peptide, "dose": item.dose, "unit": item.unit,
                    "time_of_day": item.time_of_day, "status": status})
    return rows


def _adherence_pct(session: Session, uid: int, today: date) -> int | None:
    since = today - timedelta(days=30)
    logs = session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date >= since,
                              DoseLog.scheduled_date <= today)).all()
    if not logs:
        return None
    on_time_or_late = sum(1 for l in logs if l.status in (DoseStatus.ON_TIME, DoseStatus.LATE))
    return round(100 * on_time_or_late / len(logs))


def _cost_snapshot(session: Session, uid: int) -> list[dict]:
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    active_items = [
        it for p in protocols if protocol_status(p, date.today()) is Status.ACTIVE
        for it in p.items if it.inventory_item_id is not None
    ]
    rows = []
    seen_item_ids = set()
    for it in active_items:
        if it.inventory_item_id in seen_item_ids:
            continue
        seen_item_ids.add(it.inventory_item_id)
        line = session.scalar(
            select(OrderItem).join(Order, OrderItem.order_id == Order.id)
            .where(OrderItem.inventory_item_id == it.inventory_item_id, Order.arrival_date.is_not(None))
            .order_by(Order.arrival_date.desc()))
        if line is None or line.total_cost is None or not line.received_quantity:
            continue
        cost_per_vial = line.total_cost / line.received_quantity
        vial = session.scalar(
            select(ActiveVial).where(ActiveVial.inventory_item_id == it.inventory_item_id,
                                     ActiveVial.discarded_at.is_(None))
            .order_by(ActiveVial.id.desc()))
        cost_per_dose = cost_per_vial / vial.doses_total if vial and vial.doses_total else None
        rows.append({"peptide": it.peptide.name, "cost_per_vial": cost_per_vial,
                    "cost_per_dose": cost_per_dose})
    return rows


@router.get("/dashboard")
def dashboard(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
             uid: int = Depends(current_user_id)):
    threshold_items = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid,
                                    InventoryItem.category.in_([Category.MEDICINE, Category.BAC_WATER]))).all()
    viewer = session.get(User, uid)
    default_threshold = viewer.low_stock_default if viewer.low_stock_default is not None else 5
    delay_days = viewer.shipment_delay_days if viewer.shipment_delay_days is not None else 21

    vials = session.scalars(
        select(ActiveVial).where(ActiveVial.owner_id == uid, ActiveVial.discarded_at.is_(None))).all()
    for v in vials:
        v.item_name = v.inventory_item.name  # convenience attr expected by app.alerts

    # Orders have no owner_id of their own -- scope via their line items' linked InventoryItem.
    order_ids = {li.order_id for li in session.scalars(
        select(OrderItem).join(InventoryItem, OrderItem.inventory_item_id == InventoryItem.id)
        .where(InventoryItem.owner_id == uid))}
    orders = session.scalars(select(Order).where(Order.id.in_(order_ids))).all() if order_ids else []

    alerts = {
        "low_stock": low_stock_alerts(threshold_items, default_threshold),
        "expiration": expiration_alerts(vials=vials, items=threshold_items, today=today),
        "shipment": shipment_alerts(orders, today=today, threshold_days=delay_days),
    }

    return templates.TemplateResponse(request, "dashboard/index.html", {
        "schedule": _todays_schedule(session, uid, today),
        "alerts": alerts,
        "cost_snapshot": _cost_snapshot(session, uid),
        "adherence_pct": _adherence_pct(session, uid, today),
        "today": today,
    })

"""The Dashboard: the app's homepage -- today's schedule, alerts, cost/adherence snapshots, and
placeholders for not-yet-built widgets. Read-only; every widget reuses an existing query shape."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.alerts import expiration_alerts, low_stock_alerts, shipment_alerts
from app.auth.deps import current_user_id
from app.calendar.schedule import occurrences
from app.db import get_session
from app.models import (
    ActiveVial, Category, DoseLog, DoseStatus, InventoryItem, Order, OrderItem, Protocol,
    ProtocolItem, Share, ShareCategory, User,
)
from app.protocols.status import Status, protocol_status
from app.routers.protocols import get_today
from app.templating import templates

router = APIRouter()


def _resolve_viewer(session: Session, uid: int, viewer_id: int | None) -> tuple[int, set[ShareCategory]]:
    """Returns (effective_viewer_id, categories_shared_by_that_viewer_with_uid). Falls back to
    (uid, {both categories}) when viewer_id is None or the share no longer exists -- a revoked
    share silently reverts to self rather than erroring, since the dropdown itself won't offer a
    stale option on the next render anyway."""
    if viewer_id is None or viewer_id == uid:
        return uid, {ShareCategory.INVENTORY, ShareCategory.PERSONAL_DATA}
    categories = set(session.scalars(
        select(Share.category).where(Share.owner_id == viewer_id, Share.grantee_id == uid)))
    if not categories:
        return uid, {ShareCategory.INVENTORY, ShareCategory.PERSONAL_DATA}
    return viewer_id, categories


def _shared_with_me(session: Session, uid: int) -> list[dict]:
    rows = session.execute(
        select(Share.owner_id, User.username).join(User, User.id == Share.owner_id)
        .where(Share.grantee_id == uid).distinct()).all()
    return [{"id": owner_id, "username": username} for owner_id, username in rows]


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


def _cost_snapshot(session: Session, uid: int, today: date) -> list[dict]:
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    active_items = [
        it for p in protocols if protocol_status(p, today) is Status.ACTIVE
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
             uid: int = Depends(current_user_id), viewer_id: str | None = Query(None)):
    # A plain HTML <select> submits its empty "You" option as `viewer_id=` (empty string), which
    # `int | None` query typing would reject with a 422 -- parse manually so both "absent" and
    # "present but empty" mean "no override, view yourself." A garbled, non-numeric value (never
    # produced by our own dropdown, but possible from a hand-typed URL) is treated the same way
    # rather than raising -- consistent with _resolve_viewer's own silent-fallback-to-self
    # philosophy for any other invalid/stale viewer_id.
    try:
        viewer_id_int = int(viewer_id) if viewer_id else None
    except ValueError:
        viewer_id_int = None
    effective_uid, categories = _resolve_viewer(session, uid, viewer_id_int)

    schedule = None
    adherence_pct = None
    if ShareCategory.PERSONAL_DATA in categories:
        schedule = _todays_schedule(session, effective_uid, today)
        adherence_pct = _adherence_pct(session, effective_uid, today)

    alerts = None
    cost_snapshot = None
    if ShareCategory.INVENTORY in categories:
        threshold_items = session.scalars(
            select(InventoryItem).where(InventoryItem.owner_id == effective_uid,
                                        InventoryItem.category.in_([Category.MEDICINE, Category.BAC_WATER]))).all()
        viewer = session.get(User, effective_uid)
        default_threshold = viewer.low_stock_default if viewer.low_stock_default is not None else 5
        delay_days = viewer.shipment_delay_days if viewer.shipment_delay_days is not None else 21

        vials = session.scalars(
            select(ActiveVial).where(ActiveVial.owner_id == effective_uid, ActiveVial.discarded_at.is_(None))).all()
        for v in vials:
            v.item_name = v.inventory_item.name  # convenience attr expected by app.alerts

        # Orders have no owner_id of their own -- scope via their line items' linked InventoryItem.
        order_ids = {li.order_id for li in session.scalars(
            select(OrderItem).join(InventoryItem, OrderItem.inventory_item_id == InventoryItem.id)
            .where(InventoryItem.owner_id == effective_uid))}
        orders = session.scalars(select(Order).where(Order.id.in_(order_ids))).all() if order_ids else []

        alerts = {
            "low_stock": low_stock_alerts(threshold_items, default_threshold),
            "expiration": expiration_alerts(vials=vials, items=threshold_items, today=today),
            "shipment": shipment_alerts(orders, today=today, threshold_days=delay_days),
        }
        cost_snapshot = _cost_snapshot(session, effective_uid, today)

    return templates.TemplateResponse(request, "dashboard/index.html", {
        "schedule": schedule,
        "alerts": alerts,
        "cost_snapshot": cost_snapshot,
        "adherence_pct": adherence_pct,
        "today": today,
        "viewer_id": effective_uid,
        "shared_with_me": _shared_with_me(session, uid),
        # Separate flags rather than reusing `schedule`/`adherence_pct is None` to mean "not
        # shared" -- `_adherence_pct` already returns None for "no doses logged in the window,"
        # which is a real, shared-and-empty state distinct from "this category isn't shared at
        # all." Conflating the two would hide the widget for someone who *did* share but has no
        # doses logged yet.
        "show_personal_data": ShareCategory.PERSONAL_DATA in categories,
        "show_inventory": ShareCategory.INVENTORY in categories,
    })

"""The Orders tab on Inventory: every order with an shipping-tracker-style timeline, tracking links and quick actions."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_user_id
from app.db import get_session
from app.inventory.tracking import DEFAULT_DELAY_DAYS, STATUS_LABELS, build_timeline, tracking_link
from app.models import InventoryItem, Order, OrderItem, User
from app.templating import templates

router = APIRouter()

TABS = (("", "Active"), ("waiting", "Waiting to ship"), ("in_transit", "In transit"), ("delivered", "Needs check-in"),
        ("checked_in", "Checked in"), ("all", "All"))


def _my_orders(session: Session, uid: int) -> list[dict]:
    """The caller's own orders (an order can span people's items: only their lines are ever shown), newest first."""
    rows = session.execute(
        select(Order, OrderItem, InventoryItem)
        .join(OrderItem, OrderItem.order_id == Order.id)
        .join(InventoryItem, OrderItem.inventory_item_id == InventoryItem.id)
        .where(InventoryItem.owner_id == uid)
        .order_by(Order.order_date.desc(), Order.id.desc(), OrderItem.id)).all()
    grouped: dict[int, dict] = {}
    for order, line, item in rows:
        grouped.setdefault(order.id, {"order": order, "lines": []})["lines"].append((item, line))
    return list(grouped.values())


def _cards(session: Session, uid: int, status: str) -> list[dict]:
    me = session.get(User, uid)
    delay = me.shipment_delay_days if me is not None and me.shipment_delay_days is not None else DEFAULT_DELAY_DAYS
    today = date.today()
    cards = []
    for entry in _my_orders(session, uid):
        order = entry["order"]
        timeline = build_timeline(order, today, delay)
        if status == "all" or (status == "" and timeline["status"] != "checked_in") or status == timeline["status"]:
            cards.append({**entry, "timeline": timeline, "link": tracking_link(order.tracking_site, order.tracking_number)})
    return cards


def _page(request: Request, session: Session, uid: int, status: str, *, errors: dict | None = None, code: int = 200):
    status = status if status in {key for key, _ in TABS} else ""
    return templates.TemplateResponse(request, "inventory/orders.html", {
        "cards": _cards(session, uid, status), "tabs": TABS, "status": status, "errors": errors or {}, "labels": STATUS_LABELS, "today_iso": date.today().isoformat(),
    }, status_code=code)


def _own_order(session: Session, order_id: int, uid: int) -> Order:
    order = session.get(Order, order_id)
    mine = order is not None and session.scalar(
        select(OrderItem.id).join(InventoryItem, OrderItem.inventory_item_id == InventoryItem.id)
        .where(OrderItem.order_id == order_id, InventoryItem.owner_id == uid).limit(1)) is not None
    if not mine:
        raise HTTPException(404, "Order not found")
    return order


def _date_field(raw, key: str, errors: dict, order: Order, earliest: date | None) -> date | None:
    value = str(raw or "").strip()
    try:
        found = date.fromisoformat(value)
    except ValueError:
        errors[key] = "Enter a valid date."
        return None
    if found > date.today():
        errors[key] = "That date can't be in the future."
    elif found < order.order_date or (earliest is not None and found < earliest):
        errors[key] = "That date can't be before an earlier step."
    return found


@router.get("/inventory/orders")
def orders_page(request: Request, status: str = "", session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return _page(request, session, uid, status)


@router.post("/inventory/orders/{order_id}/tracking")
async def set_tracking(order_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    order = _own_order(session, order_id, uid)
    form = await request.form()
    number = str(form.get("tracking_number") or "").strip()
    site = str(form.get("tracking_site") or "").strip()
    errors: dict[str, str] = {}
    if len(number) > 100:
        errors["tracking_number"] = "Tracking number is too long."
    if site and (len(site) > 500 or not site.lower().startswith(("http://", "https://"))):
        errors["tracking_site"] = "Tracking site must be a valid http(s) address."
    if errors:
        return _page(request, session, uid, "", errors={order_id: errors}, code=422)
    order.tracking_number = number or None
    order.tracking_site = site or None
    session.commit()
    return RedirectResponse("/inventory/orders", status_code=303)


@router.post("/inventory/orders/{order_id}/ship")
async def mark_shipped(order_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    order = _own_order(session, order_id, uid)
    form = await request.form()
    errors: dict[str, str] = {}
    if order.arrival_date is not None:
        errors["shipped_date"] = "This order is already checked in."
    shipped = _date_field(form.get("shipped_date"), "shipped_date", errors, order, None) if not errors else None
    if errors:
        return _page(request, session, uid, "", errors={order_id: errors}, code=422)
    order.shipped_date = shipped
    session.commit()
    return RedirectResponse("/inventory/orders", status_code=303)


@router.post("/inventory/orders/{order_id}/deliver")
async def mark_delivered(order_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    order = _own_order(session, order_id, uid)
    form = await request.form()
    errors: dict[str, str] = {}
    if order.arrival_date is not None:
        errors["delivered_date"] = "This order is already checked in."
    delivered = _date_field(form.get("delivered_date"), "delivered_date", errors, order, order.shipped_date) if not errors else None
    if errors:
        return _page(request, session, uid, "", errors={order_id: errors}, code=422)
    order.delivered_date = delivered
    session.commit()
    return RedirectResponse("/inventory/orders", status_code=303)

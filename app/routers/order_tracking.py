"""The Orders tab on Inventory: every order with an shipping-tracker-style timeline, tracking links and quick actions."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.auth.deps import current_user_id
from app.db import get_session
from app.inventory.tracking import DEFAULT_DELAY_DAYS, STATUS_LABELS, build_timeline, tracking_link
from app import uploads
from app.models import Category, InventoryItem, Order, OrderItem, User
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
    today = clock.today()
    cards = []
    for entry in _my_orders(session, uid):
        order = entry["order"]
        timeline = build_timeline(order, today, delay)
        if status == "all" or (status == "" and timeline["status"] != "checked_in") or status == timeline["status"]:
            cards.append({**entry, "timeline": timeline, "link": tracking_link(order.tracking_site, order.tracking_number), "can_remove": can_remove_lines(order)})
    return cards


def _my_items(session: Session, uid: int) -> list[InventoryItem]:
    return list(session.scalars(select(InventoryItem).where(InventoryItem.owner_id == uid).order_by(InventoryItem.name.collate("NOCASE"))))


def _page(request: Request, session: Session, uid: int, status: str, *, errors: dict | None = None, code: int = 200):
    status = status if status in {key for key, _ in TABS} else ""
    return templates.TemplateResponse(request, "inventory/orders.html", {
        "cards": _cards(session, uid, status), "my_items": _my_items(session, uid), "tabs": TABS, "status": status, "errors": errors or {}, "labels": STATUS_LABELS, "today_iso": clock.today().isoformat(),
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
    if found > clock.today():
        errors[key] = "That date can't be in the future."
    elif found < order.order_date or (earliest is not None and found < earliest):
        errors[key] = "That date can't be before an earlier step."
    return found


@router.get("/inventory/orders")
def orders_page(request: Request, status: str = "", session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    return _page(request, session, uid, status)


def can_remove_lines(order: Order) -> bool:
    """Only before it ships, and only while another item stays in it."""
    return order.shipped_date is None and order.delivered_date is None and order.arrival_date is None and len(order.items) > 1


@router.post("/inventory/orders/{order_id}/lines/{line_id}/remove")
async def remove_line(order_id: int, line_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Take one item out of an order that has not shipped (a vendor cannot send it). The rest stay; the order's shipping and tax are set from the form, so the
    shipping is shared between fewer items and the tax drops by the removed item's share."""
    from app.routers.inventory import _parse_money        # deferred: the inventory router imports this package's helpers at load time
    order = _own_order(session, order_id, uid)
    line = next((li for li in order.items if li.id == line_id), None)
    if line is None:
        raise HTTPException(404, "Order item not found")
    form = await request.form()
    errors: dict[str, str] = {}
    if not can_remove_lines(order):
        errors["order"] = ("An item can only be removed from an order that has not shipped yet." if len(order.items) > 1
                           else "This is the only item in the order, so there is nothing to keep. Delete the item instead.")
    tax = _parse_money(str(form.get("tax") or "").strip(), "tax", "Tax", errors)
    shipping = _parse_money(str(form.get("shipping") or "").strip(), "shipping", "Shipping", errors)
    if errors:
        return _page(request, session, uid, "", errors={order_id: errors}, code=422)
    uploads.delete_coa(line.coa_filename)
    order.items.remove(line)
    order.tax_cents, order.shipping_cents = tax, shipping
    session.commit()
    return RedirectResponse(f"/inventory/orders#order-{order_id}", status_code=303)


@router.post("/inventory/orders/{order_id}/lines")
async def add_line(order_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Put another of the caller's items on an order that was submitted without it, before or after the order is checked in. On a checked-in order the new line is
    counted as received straight away (the amount can be corrected with Edit), since there is no second package to check in."""
    from app.inventory import supply_stock
    from app.routers.inventory import _parse_order_line_fields    # deferred: the inventory router imports this package's helpers at load time
    order = _own_order(session, order_id, uid)
    form = await request.form()
    raw = {key: str(form.get(key) or "").strip() for key in ("quantity", "cost", "lot_number", "expiration_date", "coa_vial_size_mg", "coa_purity_pct")}
    values, errors = _parse_order_line_fields(raw)
    item = None
    try:
        item = session.get(InventoryItem, int(str(form.get("inventory_item_id") or "")))
    except ValueError:
        pass
    if item is None or item.owner_id != uid:
        errors["inventory_item_id"] = "Choose one of your items."
    elif any(li.inventory_item_id == item.id for li in order.items):
        errors["inventory_item_id"] = f"{item.name} is already in this order. Use its Edit button to change the quantity."
    received = None
    if order.arrival_date is not None and "quantity" not in errors:
        text = str(form.get("received_quantity") or "").strip()
        try:
            received = int(text) if text else values["quantity"]
        except ValueError:
            errors["received_quantity"] = "Received must be a whole number."
        else:
            if not 0 <= received <= values["quantity"]:
                errors["received_quantity"] = f"Received must be between 0 and the {values['quantity']} ordered."
    if errors:
        return _page(request, session, uid, "", errors={order_id: errors}, code=422)
    line = OrderItem(**values, inventory_item_id=item.id, received_quantity=received)
    order.items.append(line)
    session.flush()
    supply_stock.sync(line)
    session.commit()
    return RedirectResponse(f"/inventory/orders#order-{order_id}", status_code=303)


@router.post("/inventory/orders/{order_id}/delete")
def delete_order(order_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Delete the order and every one of the caller's lines in it (and their COA files). Supplies already added to a count by check-in come back off it.
    Lines that belong to someone else's items are theirs: if any remain, the order stays for them."""
    order = _own_order(session, order_id, uid)
    for line in [li for li in order.items if li.inventory_item.owner_id == uid]:
        item = line.inventory_item
        if item.category == Category.SUPPLY and line.stock_applied:
            item.count = max((item.count or 0) - line.stock_applied, 0)
        uploads.delete_coa(line.coa_filename)
        order.items.remove(line)
    if not order.items:
        session.delete(order)
    session.commit()
    return RedirectResponse("/inventory/orders", status_code=303)


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

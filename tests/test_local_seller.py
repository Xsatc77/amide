"""Local Seller (picked up in person: no shipping wait) and the vendor's average time to arrive."""

import html
from datetime import date, timedelta
from types import SimpleNamespace

from app.alerts import shipment_alerts
from app.db import SessionLocal
from app.models import Category, InventoryItem, Medium, Order, OrderItem, Vendor
from app.vendors.shipping import average_delivery

TODAY = date.today()


def line(local):
    return SimpleNamespace(inventory_item=SimpleNamespace(local_seller=local))


def order(days_ago_ordered, arrived_after=None, lines=(), shipped=None):
    return SimpleNamespace(id=1, vendor="V", order_date=TODAY - timedelta(days=days_ago_ordered), shipped_date=shipped,
                           arrival_date=None if arrived_after is None else TODAY - timedelta(days=days_ago_ordered) + timedelta(days=arrived_after),
                           items=list(lines))


# ---------------------------------------------------------------- the checkbox

def test_the_local_seller_checkbox_is_saved_and_cleared(client, db, me):
    r = client.post("/inventory", data={"name": "Corner Store Item", "category": "Supply", "local_seller": "1"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.query(InventoryItem).filter_by(name="Corner Store Item", owner_id=me).one()
        assert item.local_seller is True
        item_id = item.id
    client.post(f"/inventory/{item_id}", data={"name": "Corner Store Item", "category": "Supply"}, follow_redirects=False)
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.local_seller is False
        s.delete(item)
        s.commit()


def test_the_checkbox_is_on_the_add_dialog_and_the_edit_page(client, db, me):
    assert 'name="local_seller"' in client.get("/inventory").text
    with SessionLocal() as s:
        item = InventoryItem(name="Edit Me Local", category=Category.SUPPLY, owner_id=me, count=1, local_seller=True)
        s.add(item)
        s.commit()
        item_id = item.id
    try:
        page = client.get(f"/inventory/{item_id}").text
        assert 'name="local_seller"' in page and "checked" in page.split('name="local_seller"')[1].split(">")[0]
    finally:
        with SessionLocal() as s:
            s.delete(s.get(InventoryItem, item_id))
            s.commit()


# ---------------------------------------------------------------- shipment alerts

def test_an_order_of_only_local_seller_items_never_raises_a_shipment_alert():
    late = order(40, lines=[line(True), line(True)])
    assert shipment_alerts([late], today=TODAY, threshold_days=21) == []


def test_an_order_with_any_shipped_item_still_alerts_and_orders_without_lines_do_too():
    assert len(shipment_alerts([order(40, lines=[line(True), line(False)])], today=TODAY, threshold_days=21)) == 1
    assert len(shipment_alerts([order(40)], today=TODAY, threshold_days=21)) == 1


# ---------------------------------------------------------------- average delivery

def test_average_delivery_is_days_from_order_to_arrival_over_arrived_orders():
    assert average_delivery([order(30, 10), order(20, 14), order(10, None)]) == (12.0, 2)


def test_local_seller_and_unarrived_orders_do_not_count_and_none_means_no_data():
    assert average_delivery([order(30, 10, lines=[line(True)]), order(5, None)]) is None
    assert average_delivery([]) is None
    assert average_delivery([order(30, 10, lines=[line(True), line(False)])]) == (10.0, 1)


def test_the_vendor_page_shows_the_average_and_says_nothing_with_no_arrived_orders(client, db, me):
    with SessionLocal() as s:
        vendor = Vendor(name="Speedy Test Vendor")
        s.add(vendor)
        s.flush()
        item = InventoryItem(name="Speedy Item", category=Category.MEDICINE, owner_id=me, count=0, medium=Medium.LYOPHILIZED)
        s.add(item)
        s.flush()
        for ordered_days_ago, took in ((40, 6), (30, 8)):
            o = Order(order_date=TODAY - timedelta(days=ordered_days_ago), arrival_date=TODAY - timedelta(days=ordered_days_ago - took), vendor_id=vendor.id, vendor=vendor.name)
            s.add(o)
            s.flush()
            o.items.append(OrderItem(inventory_item_id=item.id, quantity=1, received_quantity=1))
        s.commit()
        vendor_id, item_id = vendor.id, item.id
    try:
        page = html.unescape(client.get(f"/vendors/{vendor_id}").text)
        assert "Average time to arrive" in page and "7 days" in page and "2 orders" in page
        with SessionLocal() as s:
            s.query(Order).delete()
            s.commit()
        assert "Average time to arrive" not in client.get(f"/vendors/{vendor_id}").text
    finally:
        with SessionLocal() as s:
            s.query(Order).delete()
            s.query(InventoryItem).filter_by(id=item_id).delete()
            s.query(Vendor).filter_by(id=vendor_id).delete()
            s.commit()

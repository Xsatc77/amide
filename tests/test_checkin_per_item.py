"""Check-in goes through every line of the order, each with its own lot, expiration, medium,
received and damaged counts."""
import html
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import InventoryItem, Medium, Order, OrderItem


def _two_line_order(db):
    with SessionLocal() as s:
        a = InventoryItem(name="Alpha Test", medium=Medium.LYOPHILIZED, vial_size_mg=5.0, owner_id=1)
        b = InventoryItem(name="Beta Test", medium=Medium.LYOPHILIZED, vial_size_mg=10.0, owner_id=1)
        o = Order(order_date=date(2026, 8, 1))
        s.add_all([a, b, o])
        s.flush()
        la = OrderItem(order_id=o.id, inventory_item_id=a.id, quantity=10)
        lb = OrderItem(order_id=o.id, inventory_item_id=b.id, quantity=5)
        s.add_all([la, lb])
        s.commit()
        return a.id, b.id, o.id, la.id, lb.id


def _uid(client):
    from app.models import User
    from tests.conftest import TEST_USER
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == TEST_USER.lower()))


def test_dialog_has_a_row_for_every_line_with_the_new_fields(client, db):
    a, b, o, la, lb = _two_line_order(db)
    with SessionLocal() as s:
        for i in (s.get(InventoryItem, a), s.get(InventoryItem, b)):
            i.owner_id = _uid(client)
        s.commit()
    t = html.unescape(client.get(f"/inventory/{a}").text)
    assert "Beta Test" in t and "checkin-data" in t
    js = client.get("/static/js/inventory.js").text
    for name in ("\"received_quantity\"", "\"damaged_quantity\"", "\"lot_number\"", "\"expiration_date\"", "\"medium\""):
        assert name in js


def test_check_in_saves_each_lines_own_details(client, db):
    a, b, o, la, lb = _two_line_order(db)
    uid = _uid(client)
    with SessionLocal() as s:
        for i in (s.get(InventoryItem, a), s.get(InventoryItem, b)):
            i.owner_id = uid
        s.commit()
    r = client.post(f"/inventory/{a}/orders/{o}/check-in", data={
        "arrival_date": "2026-08-10",
        f"received_quantity_{la}": "10", f"damaged_quantity_{la}": "2",
        f"lot_number_{la}": "LOT-A1", f"expiration_date_{la}": "2028-01-31",
        f"received_quantity_{lb}": "5", f"damaged_quantity_{lb}": "",
        f"lot_number_{lb}": "LOT-B9", f"expiration_date_{lb}": "2028-06-30",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        x, y = s.get(OrderItem, la), s.get(OrderItem, lb)
        assert (x.received_quantity, x.damaged_quantity) == (8, 2)        # stock counts only the usable
        assert (x.lot_number, x.expiration_date) == ("LOT-A1", date(2028, 1, 31))
        assert (y.received_quantity, y.damaged_quantity) == (5, 0)
        assert (y.lot_number, y.expiration_date) == ("LOT-B9", date(2028, 6, 30))
        assert s.get(InventoryItem, a).available_count == 8


def test_more_damaged_than_arrived_is_refused(client, db):
    a, b, o, la, lb = _two_line_order(db)
    uid = _uid(client)
    with SessionLocal() as s:
        for i in (s.get(InventoryItem, a), s.get(InventoryItem, b)):
            i.owner_id = uid
        s.commit()
    r = client.post(f"/inventory/{a}/orders/{o}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{la}": "3", f"damaged_quantity_{la}": "4"})
    assert r.status_code == 422
    assert "More damaged than arrived" in html.unescape(r.text)
    with SessionLocal() as s:
        assert s.get(Order, o).arrival_date is None


def _mine(client, a, b):
    uid = _uid(client)
    with SessionLocal() as s:
        for i in (s.get(InventoryItem, a), s.get(InventoryItem, b)):
            i.owner_id = uid
        s.commit()


def test_a_different_medium_at_check_in_moves_only_that_line_to_its_own_item(client, db):
    a, b, o, la, lb = _two_line_order(db)
    _mine(client, a, b)
    r = client.post(f"/inventory/{a}/orders/{o}/check-in", data={
        "arrival_date": "2026-08-10", f"medium_{la}": "Liquid"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        moved = s.get(OrderItem, la).inventory_item
        assert moved.id != a and moved.medium == Medium.LIQUID and moved.name == "Alpha Test"
        assert s.get(InventoryItem, a).medium == Medium.LYOPHILIZED       # the original item is untouched
        assert s.get(OrderItem, lb).inventory_item_id == b


def test_editing_an_order_can_split_it_off_by_medium_and_reuses_the_split_item(client, db):
    a, b, o, la, lb = _two_line_order(db)
    _mine(client, a, b)
    base = {"quantity": "10", "order_date": "2026-08-01", "line_medium": "Liquid"}
    r = client.post(f"/inventory/{a}/orders/{la}", data=base, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        first = s.get(OrderItem, la).inventory_item_id
        assert first != a and r.headers["location"] == f"/inventory/{first}"
        o2 = Order(order_date=date(2026, 8, 2))
        s.add(o2); s.flush()
        l2 = OrderItem(order_id=o2.id, inventory_item_id=a, quantity=3)
        s.add(l2); s.commit()
        l2_id = l2.id
    client.post(f"/inventory/{a}/orders/{l2_id}", data={**base, "quantity": "3", "order_date": "2026-08-02"})
    with SessionLocal() as s:
        assert s.get(OrderItem, l2_id).inventory_item_id == first          # same medium, same item

"""Deleting an order entirely, and the Edit link on the Orders tab."""

from datetime import timedelta

import pytest

from app import clock
from app.db import SessionLocal
from app.models import Category, InventoryItem, Medium, Order, OrderItem
from photo_helpers import other_client

TODAY = clock.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(Order).filter(Order.vendor == "Delete Test Co").delete(synchronize_session=False)
        s.query(InventoryItem).filter(InventoryItem.name.like("Delorder %")).delete(synchronize_session=False)
        s.commit()


def make_order(uid, arrived=False, supply=False):
    with SessionLocal() as s:
        order = Order(order_date=TODAY - timedelta(days=5), vendor="Delete Test Co", arrival_date=TODAY if arrived else None)
        s.add(order)
        s.flush()
        ids = []
        for n in range(2):
            if supply:
                item = InventoryItem(name=f"Delorder {n}", category=Category.SUPPLY, owner_id=uid, count=10 if arrived else 0)
            else:
                item = InventoryItem(name=f"Delorder {n}", category=Category.MEDICINE, owner_id=uid, count=0, medium=Medium.LYOPHILIZED, vial_size_mg=10)
            s.add(item)
            s.flush()
            s.add(OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=5, received_quantity=5 if arrived else None,
                            stock_applied=5 if (arrived and supply) else 0))
            ids.append(item.id)
        s.commit()
        return order.id, ids


def test_deleting_an_order_removes_it_and_its_lines_but_keeps_the_items(client, me):
    order_id, item_ids = make_order(me)
    r = client.post(f"/inventory/orders/{order_id}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inventory/orders"
    with SessionLocal() as s:
        assert s.get(Order, order_id) is None
        assert s.query(OrderItem).filter(OrderItem.order_id == order_id).count() == 0
        assert s.query(InventoryItem).filter(InventoryItem.id.in_(item_ids)).count() == 2


def test_deleting_a_checked_in_supply_order_takes_its_stock_back_off_the_count(client, me):
    order_id, item_ids = make_order(me, arrived=True, supply=True)
    assert client.post(f"/inventory/orders/{order_id}/delete").status_code in (200, 303)
    with SessionLocal() as s:
        assert [s.get(InventoryItem, i).count for i in item_ids] == [5, 5]


def test_someone_elses_order_cannot_be_deleted(client, me):
    order_id, _ = make_order(me)
    with other_client("delorderother") as member:
        assert member.post(f"/inventory/orders/{order_id}/delete").status_code == 404
    with SessionLocal() as s:
        assert s.get(Order, order_id) is not None


def test_the_orders_tab_offers_edit_and_delete(client, me):
    order_id, item_ids = make_order(me)
    page = client.get("/inventory/orders", params={"status": "all"}).text
    line = next(i for i in item_ids)
    assert f"/inventory/{line}?edit_order=" in page and f"/inventory/orders/{order_id}/delete" in page and "Delete order" in page


def add_line(client, order_id, item_id, **form):
    return client.post(f"/inventory/orders/{order_id}/lines", data={"inventory_item_id": str(item_id), "quantity": "3", **form}, follow_redirects=False)


def spare(uid, category=Category.MEDICINE):
    with SessionLocal() as s:
        item = InventoryItem(name="Delorder spare", category=category, owner_id=uid, count=0, medium=Medium.LYOPHILIZED if category == Category.MEDICINE else None,
                             vial_size_mg=10 if category == Category.MEDICINE else None)
        s.add(item)
        s.commit()
        return item.id


def test_an_item_left_off_can_be_added_to_an_order_that_has_not_arrived(client, me):
    order_id, _ = make_order(me)
    extra = spare(me)
    r = add_line(client, order_id, extra, cost="12.50", lot_number="L9")
    assert r.status_code == 303
    with SessionLocal() as s:
        line = s.query(OrderItem).filter_by(order_id=order_id, inventory_item_id=extra).one()
        assert (line.quantity, line.cost_cents, line.lot_number, line.received_quantity) == (3, 1250, "L9", None)


def test_an_item_added_to_a_checked_in_order_counts_as_received_now(client, me):
    order_id, _ = make_order(me, arrived=True)
    extra = spare(me)
    assert add_line(client, order_id, extra).status_code == 303
    with SessionLocal() as s:
        assert s.query(OrderItem).filter_by(order_id=order_id, inventory_item_id=extra).one().received_quantity == 3
    short = spare(me)
    with SessionLocal() as s:
        s.get(InventoryItem, short).name = "Delorder short"
        s.commit()
    add_line(client, order_id, short, received_quantity="2")
    with SessionLocal() as s:
        assert s.query(OrderItem).filter_by(order_id=order_id, inventory_item_id=short).one().received_quantity == 2


def test_a_supply_added_to_a_checked_in_order_adds_to_its_count(client, me):
    order_id, _ = make_order(me, arrived=True)
    supply = spare(me, Category.SUPPLY)
    add_line(client, order_id, supply)
    with SessionLocal() as s:
        assert s.get(InventoryItem, supply).count == 3


def test_adding_a_line_refuses_a_repeat_a_bad_quantity_and_someone_elses_item(client, me):
    order_id, (first, _) = make_order(me)
    assert add_line(client, order_id, first).status_code == 422                     # already in the order
    assert add_line(client, order_id, spare(me), quantity="0").status_code == 422
    with other_client("delorderlines") as member:
        assert add_line(client, order_id, 987654321).status_code == 422
        assert add_line(member, order_id, first).status_code == 404                 # not their order
    with SessionLocal() as s:
        assert s.query(OrderItem).filter_by(order_id=order_id).count() == 2

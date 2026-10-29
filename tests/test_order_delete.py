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

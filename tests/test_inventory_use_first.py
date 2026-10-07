"""Peptides / Medicines are listed in the order to use them: nearest expiration first, then the oldest arrival (FIFO)."""

import html
import re
from datetime import date, timedelta

import pytest

from app.db import SessionLocal
from app.models import Category, InventoryItem, Medium, Order, OrderItem

TODAY = date.today()


def stock(uid, name, *, expires=None, arrived=None, received=2, category=Category.MEDICINE):
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=category, owner_id=uid, count=0, medium=Medium.LYOPHILIZED if category == Category.MEDICINE else None)
        s.add(item)
        s.flush()
        if arrived is not None:
            order = Order(order_date=arrived - timedelta(days=5), arrival_date=arrived)
            s.add(order)
            s.flush()
            order.items.append(OrderItem(inventory_item_id=item.id, quantity=received, received_quantity=received, expiration_date=expires))
        s.commit()
        return item.id


@pytest.fixture
def items(me):
    ids = [stock(me, "Zorvex Late", expires=TODAY + timedelta(days=400), arrived=TODAY - timedelta(days=30)),
           stock(me, "Alpha Soon", expires=TODAY + timedelta(days=60), arrived=TODAY - timedelta(days=10)),
           stock(me, "Quill Oldest", expires=TODAY + timedelta(days=400), arrived=TODAY - timedelta(days=90)),
           stock(me, "Mid Undated", expires=None, arrived=TODAY - timedelta(days=200)),
           stock(me, "Empty Shelf")]
    yield ids
    with SessionLocal() as s:
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.id.in_(ids)).delete()
        s.commit()


def medicine_names(client, **params):
    page = html.unescape(client.get("/inventory", params=params).text)
    section = page.split('id="medicines"')[1].split('id="bac-water"')[0]
    return re.findall(r'<a href="/inventory/\d+">([^<]+)</a>', section)


def test_the_default_order_is_nearest_expiration_then_oldest_arrival_with_undated_and_empty_last(client, db, me, items):
    assert medicine_names(client) == ["Alpha Soon", "Quill Oldest", "Zorvex Late", "Mid Undated", "Empty Shelf"]


def test_sort_by_name_is_available(client, db, me, items):
    assert medicine_names(client, sort="name") == ["Alpha Soon", "Empty Shelf", "Mid Undated", "Quill Oldest", "Zorvex Late"]


def test_the_table_shows_when_each_item_expires_and_arrived(client, db, me, items):
    page = html.unescape(client.get("/inventory").text)
    assert (TODAY + timedelta(days=60)).strftime("%m/%d/%Y") in page and (TODAY - timedelta(days=90)).strftime("%m/%d/%Y") in page
    assert '<th>Expires</th>' in page and '<th>Arrived</th>' in page


def test_an_item_only_counts_lines_that_were_checked_in(me):
    with SessionLocal() as s:
        item = InventoryItem(name="Pending Only", category=Category.MEDICINE, owner_id=me, count=0, medium=Medium.LYOPHILIZED)
        s.add(item)
        s.flush()
        order = Order(order_date=TODAY)                                    # not arrived
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item.id, quantity=3, expiration_date=TODAY + timedelta(days=5)))
        s.commit()
        s.refresh(item)
        assert item.next_expiration is None and item.first_arrival is None
        s.query(Order).delete()
        s.delete(item)
        s.commit()


def test_the_order_control_reflects_the_choice(client, db, me, items):
    assert '<option value="use_first" selected>' in client.get("/inventory").text
    assert '<option value="name" selected>' in client.get("/inventory", params={"sort": "name"}).text
    assert '<option value="use_first" selected>' in client.get("/inventory", params={"sort": "junk"}).text

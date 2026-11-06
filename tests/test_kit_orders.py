"""An order of kits of 10 vials is stored, checked in and counted in vials: 1 kit ordered is 10 vials to check in."""

import html

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import InventoryItem


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(InventoryItem).filter(InventoryItem.name.like("Kitorder%")).delete(synchronize_session=False)
        s.commit()


def line(**over):
    base = {"order_date": "2026-09-01", "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Kitorder A",
            "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "20", "lines-0-quantity": "1", "lines-0-cost": "100.00"}
    base.update(over)
    return base


def stored(name):
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == name))
        return item.purchasing_unit.value, [li.quantity for li in item.order_items], item.id


def all_quantities(name):
    """Each order line is its own item, so a restock adds an item: the quantities across every item of that name."""
    with SessionLocal() as s:
        return sorted(li.quantity for i in s.scalars(select(InventoryItem).where(InventoryItem.name == name)) for li in i.order_items)


def test_one_kit_on_the_new_order_form_is_ten_vials(client):
    r = client.post("/inventory/orders", data=line(**{"lines-0-purchasing_unit": "kit_of_10"}), follow_redirects=False)
    assert r.status_code == 303
    assert stored("Kitorder A")[:2] == ("kit_of_10", [10])


def test_a_single_vial_order_is_unchanged(client):
    client.post("/inventory/orders", data=line(**{"lines-0-name": "Kitorder B", "lines-0-quantity": "3"}), follow_redirects=False)
    assert stored("Kitorder B")[:2] == ("individual", [3])


def test_the_kit_arrives_as_ten_vials_at_check_in(client):
    client.post("/inventory/orders", data=line(**{"lines-0-name": "Kitorder C", "lines-0-purchasing_unit": "kit_of_10", "lines-0-quantity": "2"}),
                follow_redirects=False)
    _, _, item_id = stored("Kitorder C")
    with SessionLocal() as s:
        order_id = s.get(InventoryItem, item_id).order_items[0].order_id
    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={"arrival_date": "2026-09-05"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).available_count == 20                       # two kits, ten vials each


def test_restocking_a_kit_item_counts_kits_too(client):
    client.post("/inventory/orders", data=line(**{"lines-0-name": "Kitorder D", "lines-0-purchasing_unit": "kit_of_10"}), follow_redirects=False)
    _, _, item_id = stored("Kitorder D")
    again = {"order_date": "2026-09-10", "lines-0-mode": "existing", "lines-0-item_id": str(item_id), "lines-0-quantity": "1", "lines-0-cost": "100.00"}
    assert client.post("/inventory/orders", data=again, follow_redirects=False).status_code == 303
    assert all_quantities("Kitorder D") == [10, 10]


def test_the_item_page_order_form_counts_kits_as_well(client):
    client.post("/inventory/orders", data=line(**{"lines-0-name": "Kitorder E", "lines-0-purchasing_unit": "kit_of_10"}), follow_redirects=False)
    _, _, item_id = stored("Kitorder E")
    client.post(f"/inventory/{item_id}/orders", data={"quantity": "1", "order_date": "2026-09-11", "cost": "100.00"}, follow_redirects=False)
    assert all_quantities("Kitorder E") == [10, 10]


def test_the_new_order_form_offers_sold_as(client):
    page = html.unescape(client.get("/inventory").text)
    assert "Sold as" in page and "Kit of 10 vials" in page

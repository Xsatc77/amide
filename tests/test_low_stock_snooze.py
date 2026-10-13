"""Low-stock alerts on the dashboard: snooze one for 7 or 10 days, or set that item's own alert level."""

import html
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Category, DashboardDismissal, DoseUnit, InventoryItem, Medium, Order, OrderItem
from photo_helpers import other_client


@pytest.fixture
def item(me):
    with SessionLocal() as s:
        it = InventoryItem(name="Lowstock Peptide", category=Category.MEDICINE, owner_id=me, count=0, medium=Medium.LYOPHILIZED,
                           vial_size_mg=5, vial_size_unit=DoseUnit.MG)
        s.add(it)
        s.flush()
        order = Order(order_date=date.today() - timedelta(days=9), arrival_date=date.today() - timedelta(days=3))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=it.id, quantity=2, received_quantity=2))
        s.commit()
        iid = it.id
    yield iid
    with SessionLocal() as s:
        s.query(InventoryItem).filter(InventoryItem.id == iid).delete(synchronize_session=False)
        s.query(Order).filter(~Order.items.any()).delete(synchronize_session=False)
        s.query(DashboardDismissal).delete()
        s.commit()


def alerts_text(client):
    return html.unescape(client.get("/dashboard").text)


def test_a_low_stock_item_shows_with_snooze_buttons_and_an_alert_level_field(client, item):
    text = alerts_text(client)
    assert "Lowstock Peptide" in text and "low stock" in text
    assert f'value="lowstock:{item}"' in text and 'name="days"' in text and f'action="/inventory/{item}/low-stock"' in text


@pytest.mark.parametrize("days", [7, 10])
def test_snoozing_hides_the_alert_until_the_snooze_ends(client, item, days):
    r = client.post("/dashboard/alerts/snooze", data={"key": f"lowstock:{item}", "days": str(days)}, follow_redirects=False)
    assert r.status_code == 303
    assert "low stock" not in alerts_text(client)
    with SessionLocal() as s:
        row = s.scalar(select(DashboardDismissal).where(DashboardDismissal.alert_key == f"lowstock:{item}"))
        assert row.snoozed_until == clock.today() + timedelta(days=days)
        row.snoozed_until = clock.today() - timedelta(days=1)                  # the snooze has run out
        s.commit()
    assert "Lowstock Peptide" in alerts_text(client) and "low stock" in alerts_text(client)


def test_a_snooze_can_be_taken_again_and_only_for_7_or_10_days_on_your_own_items(client, item):
    assert client.post("/dashboard/alerts/snooze", data={"key": f"lowstock:{item}", "days": "3"}).status_code == 422
    assert client.post("/dashboard/alerts/snooze", data={"key": "lowstock:999999", "days": "7"}).status_code == 422
    assert client.post("/dashboard/alerts/snooze", data={"key": f"lowstock:{item}", "days": "7"}, follow_redirects=False).status_code == 303
    client.post("/dashboard/alerts/snooze", data={"key": f"lowstock:{item}", "days": "10"})
    with SessionLocal() as s:
        rows = s.scalars(select(DashboardDismissal).where(DashboardDismissal.alert_key == f"lowstock:{item}")).all()
        assert len(rows) == 1 and rows[0].snoozed_until == clock.today() + timedelta(days=10)
    with other_client("snoozeother") as stranger:
        assert stranger.post("/dashboard/alerts/snooze", data={"key": f"lowstock:{item}", "days": "7"}).status_code == 422


def test_an_items_own_alert_level_replaces_the_default_and_blank_goes_back_to_it(client, item):
    assert client.post(f"/inventory/{item}/low-stock", data={"threshold": "1"}, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item).low_stock_threshold == 1
    assert "low stock" not in alerts_text(client)                    # 2 left is above an alert level of 1
    client.post(f"/inventory/{item}/low-stock", data={"threshold": ""})
    with SessionLocal() as s:
        assert s.get(InventoryItem, item).low_stock_threshold is None
    assert "Lowstock Peptide" in alerts_text(client)
    assert client.post(f"/inventory/{item}/low-stock", data={"threshold": "-1"}).status_code == 422
    assert client.post(f"/inventory/{item}/low-stock", data={"threshold": "abc"}).status_code == 422
    with other_client("snoozeother2") as stranger:
        assert stranger.post(f"/inventory/{item}/low-stock", data={"threshold": "9"}).status_code == 404

"""Vial labels: printed automatically when an order is checked in, and a "put these dates on your label" popup after reconstituting."""

import html
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActiveVial, Category, DoseUnit, InventoryItem, Medium, Order, OrderItem, User
from photo_helpers import other_client

TODAY = date.today()


@pytest.fixture(autouse=True)
def _reset_label_settings(client, me):
    def wipe():
        with SessionLocal() as s:
            user = s.get(User, me)
            user.auto_print_labels, user.label_size = True, "5160"
            s.commit()
    wipe()
    yield
    wipe()


def text(r) -> str:
    return html.unescape(r.text)


@pytest.fixture
def world(me):
    """An order of 3 vials of a peptide (lot LOT42), 2 BAC bottles and a supply, all checked in."""
    with SessionLocal() as s:
        pep = InventoryItem(name="Zorvex Label Test", category=Category.MEDICINE, owner_id=me, count=0, medium=Medium.LYOPHILIZED, vial_size_mg=10, vial_size_unit=DoseUnit.MG)
        bac = InventoryItem(name="Label Test BAC", category=Category.BAC_WATER, owner_id=me, count=0)
        s.add_all([pep, bac])
        s.flush()
        order = Order(order_date=TODAY - timedelta(days=9), arrival_date=TODAY - timedelta(days=2))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=pep.id, quantity=3, received_quantity=3, lot_number="LOT42"))
        order.items.append(OrderItem(inventory_item_id=bac.id, quantity=2, received_quantity=2))
        waiting = Order(order_date=TODAY)
        s.add(waiting)
        s.flush()
        waiting.items.append(OrderItem(inventory_item_id=pep.id, quantity=1))
        s.commit()
        ids = {"pep": pep.id, "bac": bac.id, "order": order.id, "waiting": waiting.id}
    yield ids
    with SessionLocal() as s:
        s.query(ActiveVial).filter(ActiveVial.inventory_item_id == ids["pep"]).delete()
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.id.in_([ids["pep"], ids["bac"]])).delete()
        s.commit()


# ---------------------------------------------------------------- the label sheet

def test_one_label_per_received_peptide_vial_with_blank_boxes_for_the_dates(client, db, world):
    page = text(client.get(f"/inventory/orders/{world['order']}/labels"))
    assert page.count('class="vial-label"') == 3                                  # the BAC bottles are not peptide vials
    for expected in ("Zorvex Label Test", "10 mg", "LOT42", "Research Use Only", "Recon", "Exp", (TODAY - timedelta(days=2)).strftime("%m/%d/%Y")):
        assert expected in page, expected
    assert "Label Test BAC" not in page


def test_an_order_not_yet_checked_in_or_someone_elses_has_no_labels(client, db, world):
    assert client.get(f"/inventory/orders/{world['waiting']}/labels").status_code == 404
    assert client.get("/inventory/orders/99999999/labels").status_code == 404
    with other_client("labelother") as member:
        assert member.get(f"/inventory/orders/{world['order']}/labels").status_code == 404


def test_the_label_size_comes_from_the_users_setting(client, db, me, world):
    assert "label-5160" in client.get(f"/inventory/orders/{world['order']}/labels").text
    with SessionLocal() as s:
        s.get(User, me).label_size = "4x2"
        s.commit()
    page = client.get(f"/inventory/orders/{world['order']}/labels").text
    assert "label-4x2" in page and "size: 4in 2in" in page


def test_auto_print_only_when_asked_and_only_to_a_local_next_page(client, db, world):
    plain = client.get(f"/inventory/orders/{world['order']}/labels").text
    assert "data-auto-print" not in plain
    auto = client.get(f"/inventory/orders/{world['order']}/labels", params={"auto": "1", "next": f"/inventory/{world['pep']}"}).text
    assert "data-auto-print" in auto and f'data-next="/inventory/{world["pep"]}"' in auto
    evil = client.get(f"/inventory/orders/{world['order']}/labels", params={"auto": "1", "next": "https://example.com/x"}).text
    assert "example.com" not in evil and 'data-next="/inventory"' in evil


# ---------------------------------------------------------------- check-in prints automatically

def test_checking_in_an_order_opens_the_labels_when_auto_print_is_on(client, db, world):
    r = client.post(f"/inventory/{world['pep']}/orders/{world['waiting']}/check-in", data={"arrival_date": TODAY.isoformat()}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/inventory/orders/{world['waiting']}/labels?auto=1") and f"/inventory/{world['pep']}" in r.headers["location"]


def test_checking_in_goes_straight_back_when_auto_print_is_off(client, db, me, world):
    with SessionLocal() as s:
        s.get(User, me).auto_print_labels = False
        s.commit()
    r = client.post(f"/inventory/{world['pep']}/orders/{world['waiting']}/check-in", data={"arrival_date": TODAY.isoformat()}, follow_redirects=False)
    assert r.headers["location"] == f"/inventory/{world['pep']}"


def test_checking_in_an_order_with_no_peptide_vials_prints_nothing(client, db, world):
    with SessionLocal() as s:
        order = Order(order_date=TODAY)
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=world["bac"], quantity=1))
        s.commit()
        order_id = order.id
    r = client.post(f"/inventory/{world['bac']}/orders/{order_id}/check-in", data={"arrival_date": TODAY.isoformat()}, follow_redirects=False)
    assert r.headers["location"] == f"/inventory/{world['bac']}"


# ---------------------------------------------------------------- after reconstituting

def reconstitute(client, world):
    from supply_helpers import stock_everything
    stock_everything(client_user_id())
    return client.post("/calculator/reconstitute", data={"inventory_item_id": str(world["pep"]), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
                                                           "discard_by": (TODAY + timedelta(days=28)).isoformat()}, follow_redirects=False)


def client_user_id():
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


def test_reconstituting_shows_the_dates_to_put_on_the_label(client, db, world):
    r = reconstitute(client, world)
    with SessionLocal() as s:
        vial = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == world["pep"]))
        vial_id = vial.id
    assert r.status_code == 303 and r.headers["location"] == f"/inventory?labels={vial_id}#active-vials"
    page = text(client.get(f"/inventory?labels={vial_id}"))
    assert "Put these dates on your label" in page
    assert TODAY.strftime("%m/%d/%Y") in page.split("Put these dates on your label")[1] and (TODAY + timedelta(days=28)).strftime("%m/%d/%Y") in page.split("Put these dates on your label")[1]
    assert f"/active-vials/{vial_id}/label" in page
    assert "Put these dates on your label" not in client.get("/inventory").text


def test_the_dialog_ignores_a_vial_that_is_not_yours(client, db, world):
    with other_client("labelother2") as member:
        assert "Put these dates on your label" not in text(member.get("/inventory?labels=1"))


def test_a_single_vial_label_has_the_dates_and_concentration_filled_in(client, db, world):
    reconstitute(client, world)
    with SessionLocal() as s:
        vial_id = s.scalar(select(ActiveVial.id).where(ActiveVial.inventory_item_id == world["pep"]))
    page = text(client.get(f"/active-vials/{vial_id}/label"))
    assert page.count('class="vial-label"') == 1
    for expected in ("Zorvex Label Test", "5 mg/mL", TODAY.strftime("%m/%d/%Y"), (TODAY + timedelta(days=28)).strftime("%m/%d/%Y"), "Research Use Only"):
        assert expected in page, expected
    with other_client("labelother3") as member:
        assert member.get(f"/active-vials/{vial_id}/label").status_code == 404


# ---------------------------------------------------------------- settings

def test_settings_has_a_labels_section_that_saves_and_validates(client, db, me):
    page = client.get("/settings").text
    assert 'name="auto_print_labels"' in page and 'name="label_size"' in page
    r = client.post("/settings/labels", data={"label_size": "2x1"}, follow_redirects=False)           # the box unticked: auto print off
    assert r.status_code == 303
    with SessionLocal() as s:
        user = s.get(User, me)
        assert (user.auto_print_labels, user.label_size) == (False, "2x1")
    client.post("/settings/labels", data={"auto_print_labels": "1", "label_size": "5160"})
    with SessionLocal() as s:
        assert s.get(User, me).auto_print_labels is True
    assert client.post("/settings/labels", data={"label_size": "nope"}).status_code == 422

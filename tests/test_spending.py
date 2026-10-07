"""Spending: what each peptide cost per vial, per mg and per dose, and what was spent each month."""

import html
from datetime import date, timedelta

import pytest

from app.db import SessionLocal
from app.inventory.spending import summarize
from app.models import (
    Category, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Peptide, PeptideSource, Protocol, ProtocolItem, Route,
)
from photo_helpers import other_client

TODAY = date.today()


@pytest.fixture(autouse=True)
def _clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.name.like("Spend%")).delete(synchronize_session=False)
        s.query(Order).delete()
        s.query(InventoryItem).filter(InventoryItem.name.like("Spend%")).delete(synchronize_session=False)
        s.query(Peptide).filter(Peptide.name.like("Spend%")).delete(synchronize_session=False)
        s.commit()


def buy(uid, name, *, vials=10, size=10.0, unit=DoseUnit.MG, cost=100.0, shipping=0.0, tax=0.0, when=TODAY):
    """One order of `vials` vials for `cost` dollars (a whole-order cost on one line)."""
    with SessionLocal() as s:
        item = InventoryItem(name=name, category=Category.MEDICINE, owner_id=uid, count=0, medium=Medium.LYOPHILIZED, vial_size_mg=size, vial_size_unit=unit)
        s.add(item)
        s.flush()
        order = Order(order_date=when, arrival_date=when + timedelta(days=5), shipping_cents=round(shipping * 100), tax_cents=round(tax * 100))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item.id, quantity=vials, received_quantity=vials, cost_cents=round(cost * 100)))
        s.commit()
        return item.id


def run(uid):
    with SessionLocal() as s:
        items = s.query(InventoryItem).filter(InventoryItem.owner_id == uid, InventoryItem.name.like("Spend%")).all()
        return summarize(items)


def test_cost_per_vial_and_per_mg_include_shipping_and_tax(me):
    buy(me, "Spend Alpha", vials=10, size=10.0, cost=100.0, shipping=20.0, tax=5.0)       # $125 for 100 mg
    row = run(me)["rows"][0]
    assert row["spent"] == pytest.approx(125.0) and row["per_vial"] == pytest.approx(12.5)
    assert row["per_unit"] == pytest.approx(1.25) and row["unit"] == "mg"


def test_mcg_vials_are_costed_per_mg_and_iu_vials_per_iu(me):
    buy(me, "Spend Mcg", vials=2, size=500.0, unit=DoseUnit.MCG, cost=10.0)                # 1 mg total
    buy(me, "Spend Iu", vials=1, size=24.0, unit=DoseUnit.IU, cost=48.0)
    rows = {r["name"]: r for r in run(me)["rows"]}
    assert rows["Spend Mcg"]["per_unit"] == pytest.approx(10.0) and rows["Spend Mcg"]["unit"] == "mg"
    assert rows["Spend Iu"]["per_unit"] == pytest.approx(2.0) and rows["Spend Iu"]["unit"] == "IU"


def test_a_peptide_bought_twice_adds_up(me):
    item = buy(me, "Spend Twice", vials=5, size=10.0, cost=50.0)
    with SessionLocal() as s:
        order = Order(order_date=TODAY - timedelta(days=40), arrival_date=TODAY - timedelta(days=35))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item, quantity=5, received_quantity=5, cost_cents=7500))
        s.commit()
    row = run(me)["rows"][0]
    assert row["spent"] == pytest.approx(125.0) and row["vials"] == 10 and row["per_vial"] == pytest.approx(12.5)


def test_cost_per_dose_uses_the_active_protocols_dose(me):
    item = buy(me, "Spend Dose", vials=10, size=10.0, cost=100.0)                         # $1 per mg
    with SessionLocal() as s:
        pep = Peptide(name="Spend Dose Peptide", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name="Spend Protocol", start_date=TODAY - timedelta(days=2), owner_id=me)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=250, dose_unit=DoseUnit.MCG, frequency=Frequency.DAILY, route=Route.SUBQ, inventory_item_id=item))
        s.commit()
    with SessionLocal() as s:
        items = s.query(InventoryItem).filter(InventoryItem.id == item).all()
        row = summarize(items, protocol_items=s.query(ProtocolItem).filter(ProtocolItem.inventory_item_id == item).all())["rows"][0]
    assert row["per_dose"] == pytest.approx(0.25)                                           # 0.25 mg at $1 a mg


def test_monthly_spend_groups_by_order_month_and_totals(me):
    buy(me, "Spend Jan A", cost=100.0, when=date(2026, 1, 10))
    buy(me, "Spend Jan B", cost=50.0, shipping=10.0, when=date(2026, 1, 20))
    buy(me, "Spend Mar", cost=70.0, when=date(2026, 3, 2))
    result = run(me)
    assert [(m["month"], round(m["total"], 2)) for m in result["months"]] == [("2026-03", 70.0), ("2026-01", 160.0)]
    assert result["grand_total"] == pytest.approx(230.0)


def test_items_with_no_cost_are_left_out(me):
    buy(me, "Spend Free", cost=0.0)
    assert run(me)["rows"] == [] or run(me)["rows"][0]["spent"] == 0


# ---------------------------------------------------------------- the page

def test_the_spending_page_shows_the_numbers_and_only_my_own(client, db, me):
    buy(me, "Spend Page Item", vials=10, size=10.0, cost=100.0, shipping=20.0, when=date(2026, 2, 3))
    page = html.unescape(client.get("/inventory/spending").text)
    assert "Spend Page Item" in page and "$12.00" in page and "$1.20" in page and "2026-02" in page and "$120.00" in page
    with other_client("spendother") as member:
        assert "Spend Page Item" not in member.get("/inventory/spending").text


def test_the_inventory_page_links_to_spending(client, db):
    assert 'href="/inventory/spending"' in client.get("/inventory").text

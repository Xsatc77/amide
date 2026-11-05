"""Dashboard Cost Snapshot: drops, pills and powders that are not mixed from powder get a cost per dose from their unit size and the protocol's dose."""

from datetime import timedelta

import pytest

from app import clock
from app.db import SessionLocal
from app.models import Category, DoseUnit, Frequency, InventoryItem, Medium, Order, OrderItem, Peptide, PeptideSource, Protocol, ProtocolItem, Route
from app.routers.dashboard import _cost_snapshot

TODAY = clock.today()


def stock(uid, name, *, medium, size, unit, dose, dose_unit, cost, units=2, per_package=None):
    with SessionLocal() as s:
        pep = Peptide(name=name, source=PeptideSource.CUSTOM)
        item = InventoryItem(name=name, category=Category.MEDICINE, owner_id=uid, count=units, medium=medium, vial_size_mg=size, vial_size_unit=unit, units_per_package=per_package)
        s.add_all([pep, item])
        s.flush()
        order = Order(order_date=TODAY - timedelta(days=10), arrival_date=TODAY - timedelta(days=5))
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=item.id, quantity=units, received_quantity=units, cost_cents=round(cost * 100)))
        p = Protocol(name=f"{name} course", start_date=TODAY - timedelta(days=2), owner_id=uid)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=dose, dose_unit=dose_unit, frequency=Frequency.DAILY, route=Route.ORAL, inventory_item_id=item.id))
        s.commit()


def rows(uid):
    with SessionLocal() as s:
        return {r["peptide"]: r for r in _cost_snapshot(s, uid, TODAY)}


def test_drops_get_a_cost_per_dose_from_the_unit_size_and_the_dose(me):
    stock(me, "Snapdrops", medium=Medium.DROPS, size=5000, unit=DoseUnit.MCG, dose=5000, dose_unit=DoseUnit.MCG, cost=13.0)       # $6.50 a bottle, one dose a bottle
    row = rows(me)["Snapdrops"]
    assert row["cost_per_vial"] == pytest.approx(6.5) and row["cost_per_dose"] == pytest.approx(6.5)


def test_a_bottle_that_holds_many_doses_divides_the_bottle_cost_by_them(me):
    stock(me, "Snapb12", medium=Medium.DROPS, size=5000, unit=DoseUnit.MCG, dose=5000, dose_unit=DoseUnit.MCG, cost=12.96, per_package=70)   # $6.48 a bottle of 70 doses
    row = rows(me)["Snapb12"]
    assert row["cost_per_vial"] == pytest.approx(6.48) and row["cost_per_dose"] == pytest.approx(6.48 / 70)


def test_a_dose_that_is_two_of_the_per_dose_amount_costs_two_doses(me):
    stock(me, "Snapb12 double", medium=Medium.DROPS, size=5000, unit=DoseUnit.MCG, dose=10000, dose_unit=DoseUnit.MCG, cost=14.0, per_package=70)
    assert rows(me)["Snapb12 double"]["cost_per_dose"] == pytest.approx(7.0 * 2 / 70)


def test_a_logged_dose_uses_one_seventieth_of_a_bottle_when_the_bottle_holds_seventy(me):
    from app.inventory import unit_use
    stock(me, "Snapuse", medium=Medium.DROPS, size=5000, unit=DoseUnit.MCG, dose=5000, dose_unit=DoseUnit.MCG, cost=10.0, per_package=70)
    with SessionLocal() as s:
        item = s.query(InventoryItem).filter(InventoryItem.name == "Snapuse").one()
        assert unit_use.units_for_dose(item, 5000, DoseUnit.MCG) == pytest.approx(1 / 70)


def test_drops_offer_doses_per_bottle_as_an_optional_field_and_nothing_else_does():
    from app.inventory.rules import optional_fields_for, required_fields_for
    assert optional_fields_for(Medium.DROPS) == {"units_per_package"} and "units_per_package" not in required_fields_for(Medium.DROPS)
    assert optional_fields_for(Medium.LYOPHILIZED) == set() and optional_fields_for(None) == set()


def test_a_dose_smaller_than_the_unit_costs_its_share_and_units_convert(me):
    stock(me, "Snappills", medium=Medium.PILL, size=5200, unit=DoseUnit.MG, dose=2600, dose_unit=DoseUnit.MG, cost=20.0)           # $10 a pack of 5200 mg; a 2600 mg dose is half
    assert rows(me)["Snappills"]["cost_per_dose"] == pytest.approx(5.0)


def test_a_powder_vial_without_an_open_vial_still_has_no_cost_per_dose(me):
    stock(me, "Snappowder", medium=Medium.LYOPHILIZED, size=10, unit=DoseUnit.MG, dose=250, dose_unit=DoseUnit.MCG, cost=100.0)
    assert rows(me)["Snappowder"]["cost_per_dose"] is None


def test_the_water_fill_has_waves_and_bubbles_only_once_there_is_water(client, db, me):
    page = client.get("/dashboard").text
    fill = page[page.index('class="water-fill"'):page.index('class="water-panel-content"')]
    assert ("water-wave" in fill and fill.count("<i></i>") == 6) == ("--water-pct: 0%" not in page)

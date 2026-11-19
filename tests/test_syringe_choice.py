"""The syringe picked with the injection site: the units to draw on it, and which of the person's syringes the shot uses up."""

import json
from datetime import date, timedelta

import pytest

from app.db import SessionLocal
from app.dosing import syringe
from app.models import (ActiveVial, Category, DoseLog, DoseUnit, Frequency, InventoryItem, Medium, Order, Peptide, PeptideSource, Protocol, ProtocolItem,
                        Route, SupplyType)
from supply_helpers import make_supply

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(DoseLog).delete()
        s.query(ActiveVial).delete()
        s.query(Protocol).filter(Protocol.name.like("Syr%")).delete(synchronize_session=False)
        s.query(Order).delete()
        s.query(InventoryItem).delete()
        s.query(Peptide).filter(Peptide.name.like("Syr%")).delete(synchronize_session=False)
        s.commit()


def syringe_item(uid, name, count, per_ml=None, barrel=None):
    item_id = make_supply(uid, name, SupplyType.DOSING_SYRINGE, count)
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        item.syringe_units_per_ml, item.volume_ml = per_ml, barrel
        s.commit()
    return item_id


def line(uid, *, liquid=False, dose=2.0, unit=DoseUnit.MG):
    """A protocol line on a 5 mg/mL open powder vial, or on a 250 mg/mL oil with no vial to open."""
    with SessionLocal() as s:
        if liquid:
            stock = InventoryItem(name="Syr Oil", category=Category.MEDICINE, owner_id=uid, count=0, medium=Medium.LIQUID, vial_size_mg=2500, vial_size_unit=DoseUnit.MG, volume_ml=10)
        else:
            stock = InventoryItem(name="Syr Powder", category=Category.MEDICINE, owner_id=uid, count=0, medium=Medium.LYOPHILIZED, vial_size_mg=10)
        s.add(stock)
        s.flush()
        if not liquid:
            s.add(ActiveVial(owner_id=uid, inventory_item_id=stock.id, concentration_mg_ml=5.0, water_ml=2.0, volume_remaining_ml=2.0, date_mixed=TODAY,
                             discard_by=TODAY + timedelta(days=28), doses_total=10, confirmed=True))
        pep = Peptide(name="Syr Peptide", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name="Syr Protocol", start_date=TODAY - timedelta(days=5), owner_id=uid)
        s.add(p)
        s.flush()
        pi = ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=dose, dose_unit=unit, frequency=Frequency.DAILY, route=Route.SUBQ, inventory_item_id=stock.id)
        s.add(pi)
        s.commit()
        return p.id, pi.id


def log(client, pid, piid, **extra):
    return client.post("/today/log", data={"protocol_id": str(pid), "protocol_item_id": str(piid), "scheduled_date": TODAY.isoformat(), **extra}, follow_redirects=False)


def count(item_id):
    with SessionLocal() as s:
        return s.get(InventoryItem, item_id).count


def site_blob(client, piid):
    page = client.get("/today").text
    return json.loads(page.split('id="site-data">')[1].split("</script>")[0])[str(piid)]


def test_the_dropdown_lists_every_syringe_in_stock_and_falls_back_to_a_plain_u100(client, me):
    assert syringe.options(SessionLocal(), me) == [{"id": "", "label": "U-100 (no syringe in Inventory)", "units_per_ml": 100.0, "capacity_ml": None}]
    a, b = syringe_item(me, "Syr BD 1 mL", 5), syringe_item(me, "Syr Slim", 3, per_ml=40, barrel=0.5)
    syringe_item(me, "Syr Empty", 0)
    with SessionLocal() as s:
        got = syringe.options(s, me)
    assert [o["id"] for o in got] == [a, b] and got[0]["label"] == "Syr BD 1 mL (U-100) · 5 left" and got[1]["label"].startswith("Syr Slim (U-40)") and got[1]["capacity_ml"] == 0.5
    line(me)
    page = client.get("/today").text
    assert "data-site-syringe" in page and f'value="{b}" data-per-ml="40.0"' in page and "Syr Empty" not in page


def test_the_current_and_last_dose_carry_their_volume_so_any_syringe_gives_its_units(client, me):
    pid, piid = line(me, dose=2.0)                                              # 2 mg at 5 mg/mL is 0.4 mL: 40 units on a U-100, 16 on a U-40
    assert site_blob(client, piid)["dose_ml"] == pytest.approx(0.4) and site_blob(client, piid)["last_ml"] is None
    log(client, pid, piid, syringe_units="50")                                  # really drew 50 units, 0.5 mL
    with SessionLocal() as s:
        done = s.query(DoseLog).one()
        assert done.volume_ml == pytest.approx(0.5) and done.dose_value == pytest.approx(2.5)
    assert site_blob(client, piid)["last_ml"] == pytest.approx(0.5)             # the last dose is what was really drawn


def test_the_syringe_picked_is_the_one_taken_from_inventory_and_its_units_decide_the_dose(client, me):
    first, slim = syringe_item(me, "Syr BD 1 mL", 5), syringe_item(me, "Syr Slim", 3, per_ml=40, barrel=1)
    pads = make_supply(me, "Syr pads", SupplyType.ALCOHOL_PAD, 9)
    pid, piid = line(me)
    assert log(client, pid, piid, syringe_units="16", syringe_item_id=str(slim)).status_code == 303          # 16 units on a U-40 is 0.4 mL: 2 mg
    assert (count(first), count(slim), count(pads)) == (5, 2, 8)
    with SessionLocal() as s:
        done = s.query(DoseLog).one()
        assert done.volume_ml == pytest.approx(0.4) and done.dose_value == pytest.approx(2.0) and done.supplies_used == {str(pads): 1, str(slim): 1}
    client.post("/today/undo", data={"dose_log_id": str(done.id)})
    assert (count(first), count(slim), count(pads)) == (5, 3, 9)


def test_without_a_pick_the_first_syringe_in_stock_is_used_as_before_and_a_stranger_or_empty_one_is_ignored(client, me):
    first, empty = syringe_item(me, "Syr BD 1 mL", 5), syringe_item(me, "Syr Empty", 0)
    pid, piid = line(me)
    log(client, pid, piid)
    assert count(first) == 4
    with SessionLocal() as s:
        s.query(DoseLog).delete()
        s.commit()
    log(client, pid, piid, syringe_item_id=str(empty))                          # nothing left in it: falls back to the first in stock
    log(client, pid, piid, syringe_item_id="999999")
    assert count(first) == 3 and count(empty) == 0


def test_a_liquid_with_no_open_vial_is_drawn_from_its_own_strength_and_takes_the_picked_syringe(client, me):
    slim = syringe_item(me, "Syr Slim", 3, per_ml=40)
    pid, piid = line(me, liquid=True, dose=200.0)                               # 200 mg of a 250 mg/mL oil is 0.8 mL: 80 units on a U-100, 32 on a U-40
    blob = site_blob(client, piid)
    assert blob["dose_ml"] == pytest.approx(0.8) and blob["on_syringe"] is True
    assert "Draw to 0.80 mL (80 units)" in client.get("/today").text
    log(client, pid, piid, syringe_units="32", syringe_item_id=str(slim))
    assert count(slim) == 2
    with SessionLocal() as s:
        done = s.query(DoseLog).one()
        assert done.volume_ml == pytest.approx(0.8) and done.dose_value == pytest.approx(200.0)


def test_the_picker_shows_the_units_statements_and_posts_the_syringe(client, me):
    js = client.get("/static/js/dosing.js").text
    assert "units in U-" in js and "[data-syringe-input]" in js and "syringeSelect.value" in js
    line(me)
    page = client.get("/today").text
    assert "data-site-dose-units" in page and "data-site-last-units" in page and 'name="syringe_item_id"' in page


def test_a_syringe_item_keeps_its_units_per_ml_and_barrel_size(client, me):
    item_id = syringe_item(me, "Syr Slim", 3)
    form = {"name": "Syr Slim", "count": "3", "supply_type": "dosing_syringe", "syringe_units_per_ml": "40", "syringe_barrel_ml": "0.5", "category": "Supply"}
    assert client.post(f"/inventory/{item_id}", data=form, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert (item.syringe_units_per_ml, item.volume_ml) == (40.0, 0.5)
    bad = client.post(f"/inventory/{item_id}", data={**form, "syringe_units_per_ml": "5000"})
    assert bad.status_code == 422 and "at most 1000" in bad.text

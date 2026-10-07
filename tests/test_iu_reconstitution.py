"""IU (and mcg) vials: reconstituting them, picking the BAC water, drawing doses from them, and course totals."""

import html
from datetime import date
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActiveVial, Category, DoseLog, DoseUnit, Frequency, InventoryItem, Medium, Peptide, Protocol, ProtocolItem, Route, SupplyType, User
from app.protocols.course_totals import compute_course_totals
from supply_helpers import counts, make_bac, make_supply, stock_everything
from test_active_vials import _create_item


def recon(client, item_id, **extra):
    data = {"inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "500", "dose_unit": "mcg", "discard_by": "2026-12-31", **extra}
    return client.post("/calculator/reconstitute", data=data, follow_redirects=False)


def error_text(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query).get("reconstitute_error", [""])[0]


@pytest.fixture
def hgh(db, me):
    stock_everything(me)
    return _create_item("Test HGH191AA", category="Medicine", medium="Lyophilized", vial_size_mg=10, vial_size_unit="IU", quantity=2,
                        arrival_date=date(2026, 8, 10), uid=me)


def vial_for(item_id):
    with SessionLocal() as s:
        return s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == item_id))


# ---------------------------------------------------------------- reconstituting an IU vial

def test_an_iu_vial_with_a_mass_dose_is_reconstituted_through_the_conversion(client, hgh):
    r = recon(client, hgh, iu_per_mg="3")
    assert r.status_code == 303 and r.headers["location"].startswith("/inventory?labels=") and r.headers["location"].endswith("#active-vials")
    v = vial_for(hgh)
    assert (v.vial_unit, v.concentration_mg_ml, v.iu_per_mg, v.doses_total) == ("IU", pytest.approx(5.0), 3.0, 6)       # 10 IU / 2 mL; 500 mcg = 1.5 IU
    with SessionLocal() as s:
        item = s.get(InventoryItem, hgh)
        assert item.reconstituted_count == 1 and item.iu_per_mg == 3.0                                                 # the item remembers its factor


def test_an_iu_dose_needs_no_factor(client, hgh):
    assert recon(client, hgh, dose_value="2", dose_unit="IU").status_code == 303
    v = vial_for(hgh)
    assert (v.vial_unit, v.doses_total, v.iu_per_mg) == ("IU", 5, None)


def test_a_mass_dose_on_an_iu_vial_without_a_factor_is_refused_and_nothing_changes(client, hgh, me):
    r = recon(client, hgh)
    assert "IU per mg" in error_text(r) and vial_for(hgh) is None
    with SessionLocal() as s:
        assert s.get(InventoryItem, hgh).reconstituted_count == 0


def test_the_item_factor_is_used_when_the_form_sends_none(client, hgh):
    recon(client, hgh, iu_per_mg="3")
    recon(client, hgh)                                                                                                   # the second time the item already knows
    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=hgh).count() == 2


def test_an_mcg_vial_is_stored_per_mcg(client, db, me):
    stock_everything(me)
    item = _create_item("Test mcg peptide", category="Medicine", medium="Lyophilized", vial_size_mg=500, vial_size_unit="mcg", quantity=2,
                        arrival_date=date(2026, 8, 10), uid=me)
    assert recon(client, item, dose_value="250", dose_unit="mcg").status_code == 303
    v = vial_for(item)
    assert (v.vial_unit, v.concentration_mg_ml, v.doses_total) == ("mcg", pytest.approx(250.0), 2)


def test_the_page_lists_iu_and_mcg_items_in_the_inventory_picker(client, hgh):
    page = html.unescape(client.get("/calculator").text)
    assert "Test HGH191AA" in page and "10 IU" in page


# ---------------------------------------------------------------- choosing the BAC water

def test_a_chosen_bac_item_is_used_instead_of_the_priority_order(client, db, me):
    stock_everything(me, bac_bottles=0)
    first = make_bac(me, "First-ranked BAC", priority=1)
    chosen = make_bac(me, "Chosen BAC", priority=3)
    peptide = _create_item("Zorvex Test", category="Medicine", medium="Lyophilized", vial_size_mg=10, quantity=2, arrival_date=date(2026, 8, 10), uid=me)
    assert recon(client, peptide, dose_value="250", bac_item_id=str(chosen)).status_code == 303
    assert counts({"first": first, "chosen": chosen}) == {"first": 1, "chosen": 0}


def test_a_chosen_bac_item_with_none_left_is_refused_even_if_others_have_stock(client, db, me):
    stock_everything(me, bac_bottles=0)
    make_bac(me, "Stocked BAC", priority=1)
    empty = make_bac(me, "Empty BAC", priority=2, bottles=0)
    peptide = _create_item("Zorvex Test", category="Medicine", medium="Lyophilized", vial_size_mg=10, quantity=2, arrival_date=date(2026, 8, 10), uid=me)
    r = recon(client, peptide, dose_value="250", bac_item_id=str(empty))
    assert "No BAC Water Available" in error_text(r) and "Empty BAC" in error_text(r)


def test_the_open_vial_of_the_chosen_item_is_used_first(client, db, me):
    stock_everything(me, bac_bottles=0)
    chosen = make_bac(me, "Chosen BAC", priority=2, bottles=2)
    peptide = _create_item("Zorvex Test", category="Medicine", medium="Lyophilized", vial_size_mg=10, quantity=3, arrival_date=date(2026, 8, 10), uid=me)
    recon(client, peptide, dose_value="250", bac_item_id=str(chosen))
    recon(client, peptide, dose_value="250", bac_item_id=str(chosen))
    assert counts({"chosen": chosen}) == {"chosen": 1}                                                                  # one bottle, shared by both


def test_a_bac_item_that_is_not_bac_water_or_not_yours_is_refused(client, db, me):
    ids = stock_everything(me)
    peptide = _create_item("Zorvex Test", category="Medicine", medium="Lyophilized", vial_size_mg=10, quantity=2, arrival_date=date(2026, 8, 10), uid=me)
    assert "BAC" in error_text(recon(client, peptide, dose_value="250", bac_item_id=str(ids["pads"])))
    assert "BAC" in error_text(recon(client, peptide, dose_value="250", bac_item_id="99999"))


def test_the_page_offers_the_bac_water_items_with_what_is_left(client, db, me):
    stock_everything(me, bac_bottles=0)
    make_bac(me, "Top-priority BAC", priority=1, bottles=2, volume=30.0)
    page = html.unescape(client.get("/calculator").text)
    assert "Top-priority BAC" in page and "Automatic" in page and "2 in stock" in page


# ---------------------------------------------------------------- drawing doses from an IU vial

def setup_iu_protocol(db, me, *, dose, dose_unit, iu_per_mg=3.0, vial_iu_per_mg=None):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide")) or Peptide(name="Retatrutide")
        s.add(peptide)
        s.flush()
        item = InventoryItem(owner_id=uid, name="Test HGH191AA", category=Category.MEDICINE, medium=Medium.LYOPHILIZED, vial_size_mg=10, vial_size_unit=DoseUnit.IU)
        s.add(item)
        s.flush()
        vial = ActiveVial(owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0, dose_value=1.5, dose_unit=DoseUnit.IU,
                          doses_total=6, date_mixed=date.today(), discard_by=date(2099, 1, 1), volume_remaining_ml=2.0, vial_unit="IU",
                          iu_per_mg=vial_iu_per_mg)
        s.add(vial)
        protocol = Protocol(name="Growth", start_date=date.today(), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=dose, dose_unit=DoseUnit(dose_unit), frequency=Frequency.DAILY,
                             route=Route.SUBQ, inventory_item_id=item.id)
        s.add(pitem)
        s.commit()
        return protocol.id, pitem.id, vial.id


def log(client, protocol_id, pitem_id):
    return client.post("/today/log", data={"protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
                                           "scheduled_date": date.today().isoformat()}, follow_redirects=False)


def test_an_iu_dose_from_an_iu_vial_draws_its_volume(client, db, me):
    p, i, v = setup_iu_protocol(db, me, dose=2.0, dose_unit="IU")
    assert log(client, p, i).status_code == 303
    with SessionLocal() as s:
        assert s.get(ActiveVial, v).volume_remaining_ml == pytest.approx(1.6)                      # 2 IU / 5 IU per mL = 0.4 mL


def test_a_mass_dose_from_an_iu_vial_uses_the_vials_factor(client, db, me):
    p, i, v = setup_iu_protocol(db, me, dose=500, dose_unit="mcg", vial_iu_per_mg=3.0)
    log(client, p, i)
    with SessionLocal() as s:
        assert s.get(ActiveVial, v).volume_remaining_ml == pytest.approx(1.7)                      # 500 mcg = 1.5 IU = 0.3 mL


def test_a_mass_dose_from_an_iu_vial_with_no_factor_logs_without_a_volume(client, db, me):
    p, i, v = setup_iu_protocol(db, me, dose=500, dose_unit="mcg", vial_iu_per_mg=None)
    assert log(client, p, i).status_code == 303
    with SessionLocal() as s:
        assert s.get(ActiveVial, v).volume_remaining_ml == pytest.approx(2.0)
        assert s.scalar(select(DoseLog.id)) is not None


def test_the_today_page_shows_the_draw_for_an_iu_dose(client, db, me):
    setup_iu_protocol(db, me, dose=2.0, dose_unit="IU")
    assert "Draw to 0.40 mL" in html.unescape(client.get("/today").text)


def test_an_iu_dose_from_a_mass_vial_with_a_factor_also_works(client, db, me):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide")) or Peptide(name="Retatrutide")
        s.add(peptide)
        s.flush()
        item = InventoryItem(owner_id=uid, name="Mass vial", category=Category.MEDICINE, medium=Medium.LYOPHILIZED, vial_size_mg=10)
        s.add(item)
        s.flush()
        vial = ActiveVial(owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0, dose_value=1, dose_unit=DoseUnit.MG,
                          doses_total=10, date_mixed=date.today(), discard_by=date(2099, 1, 1), volume_remaining_ml=2.0, vial_unit="mg", iu_per_mg=3.0)
        s.add(vial)
        protocol = Protocol(name="P", start_date=date.today(), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=3.0, dose_unit=DoseUnit.IU, frequency=Frequency.DAILY, route=Route.SUBQ,
                             inventory_item_id=item.id)
        s.add(pitem)
        s.commit()
        ids = (protocol.id, pitem.id, vial.id)
    log(client, ids[0], ids[1])
    with SessionLocal() as s:
        assert s.get(ActiveVial, ids[2]).volume_remaining_ml == pytest.approx(1.8)                 # 3 IU = 1 mg = 0.2 mL


# ---------------------------------------------------------------- course totals

def item(dose, unit, inventory_item_id=1):
    return NS(id=1, peptide=NS(name="HGH", library_specifications=None), dose=dose, dose_unit=unit, frequency=Frequency.DAILY, every_n_days=None,
              weekdays=None, steps=[], cycle_offs=[], inventory_item_id=inventory_item_id)


def proto(it):
    return NS(start_date=date(2026, 1, 1), end_date=date(2026, 1, 10), titration_enabled=False, items=[it])


def iu_vial(iu_per_mg=None, amount=10.0):
    return {1: NS(id=1, vial_size_mg=amount, vial_size_unit=DoseUnit.IU, purchasing_unit=None, medium=Medium.LYOPHILIZED, iu_per_mg=iu_per_mg)}


def test_iu_doses_from_an_iu_vial_give_a_vial_count():
    [t] = compute_course_totals(proto(item(2.0, DoseUnit.IU)), iu_vial())
    assert t.total_amount == 20.0 and t.vials_estimate == 2                                        # 10 days x 2 IU into 10 IU vials


def test_mass_doses_from_an_iu_vial_convert_with_the_items_factor():
    [t] = compute_course_totals(proto(item(500, DoseUnit.MCG)), iu_vial(iu_per_mg=3.0))
    assert t.vials_estimate == 2                                                                   # 5000 mcg = 15 IU = 1.5 vials of 10 IU


def test_mass_doses_from_an_iu_vial_without_a_factor_say_what_is_needed():
    [t] = compute_course_totals(proto(item(500, DoseUnit.MCG)), iu_vial())
    assert t.vials_estimate is None and "IU per mg" in t.note

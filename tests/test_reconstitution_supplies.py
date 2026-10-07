"""Reconstituting uses BAC water (an open vial first, else the best-ranked bottle in stock) and supplies; a pen needs its own set."""

import html
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActiveVial, Category, InventoryItem, SupplyType, User
from supply_helpers import ALL_TYPES, counts, make_bac, make_supply, stock_everything
from test_active_vials import _create_item

RECON = {"water_ml": "2", "dose_value": "250", "dose_unit": "mcg", "discard_by": "2026-12-31"}


@pytest.fixture
def peptide(db, me):
    return _create_item("Zorvex Test", category="Medicine", medium="Lyophilized", vial_size_mg=10, quantity=5, arrival_date=date(2026, 8, 10), uid=me)


def recon(client, item_id, **extra):
    return client.post("/calculator/reconstitute", data={"inventory_item_id": str(item_id), **RECON, **extra}, follow_redirects=False)


def error_text(response) -> str:
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(response.headers["location"]).query).get("reconstitute_error", [""])[0]


def bac_vials(uid=None):
    with SessionLocal() as s:
        return s.scalars(select(ActiveVial).join(InventoryItem, ActiveVial.inventory_item_id == InventoryItem.id)
                         .where(InventoryItem.category == Category.BAC_WATER).order_by(ActiveVial.id)).all()


# ---------------------------------------------------------------- BAC water

def test_the_best_ranked_bac_is_opened_whatever_the_age_of_the_others(client, db, me, peptide):
    stock_everything(me, bac_bottles=0)                                     # supplies only
    third = make_bac(me, "Third-ranked BAC", priority=3, arrived=date(2026, 1, 1))     # oldest
    first = make_bac(me, "First-ranked BAC", priority=1, arrived=date(2026, 9, 30))   # newest
    second = make_bac(me, "Second-ranked BAC", priority=2, arrived=date(2026, 5, 1))
    assert recon(client, peptide).status_code == 303
    assert counts({"first": first, "second": second, "third": third}) == {"first": 0, "second": 1, "third": 1}
    (vial,) = bac_vials()
    assert vial.inventory_item_id == first and vial.discard_by == date.today() + timedelta(days=28)
    assert vial.date_mixed == date.today() and vial.volume_remaining_ml == pytest.approx(28.0)       # a 30 mL bottle minus the 2 mL used
    assert vial.concentration_mg_ml is None and vial.doses_total is None


def test_unranked_bac_comes_after_every_ranked_one(client, db, me, peptide):
    stock_everything(me, bac_bottles=0)
    unranked = make_bac(me, "Plain BAC", priority=None)
    ranked = make_bac(me, "Ranked BAC", priority=4)
    recon(client, peptide)
    assert counts({"unranked": unranked, "ranked": ranked}) == {"unranked": 1, "ranked": 0}


def test_an_open_bac_vial_is_used_before_opening_another_and_loses_the_water_used(client, db, me, peptide):
    ids = stock_everything(me, bac_bottles=2)
    recon(client, peptide)
    recon(client, peptide)
    assert counts({"bac": ids["bac"]}) == {"bac": 1}                         # only one bottle was opened
    (vial,) = bac_vials()
    assert vial.volume_remaining_ml == pytest.approx(26.0)


def test_when_the_open_vial_cannot_cover_the_water_a_new_bottle_is_opened_and_the_old_one_stays(client, db, me, peptide):
    ids = stock_everything(me, bac_bottles=0)
    small = make_bac(me, "Small BAC", priority=1, volume=3.0, bottles=1)
    big = make_bac(me, "Big BAC", priority=2, volume=30.0, bottles=1)
    recon(client, peptide)                                                    # opens the 3 mL bottle, 1 mL left
    recon(client, peptide)                                                    # 2 mL needed: the open one cannot cover it
    first, second = bac_vials()
    assert (first.inventory_item_id, round(first.volume_remaining_ml, 2)) == (small, 1.0)
    assert (second.inventory_item_id, round(second.volume_remaining_ml, 2)) == (big, 28.0)
    assert first.discarded_at is None


def test_an_expired_open_bac_vial_is_not_used(client, db, me, peptide):
    ids = stock_everything(me, bac_bottles=2)
    recon(client, peptide)
    with SessionLocal() as s:
        s.scalar(select(ActiveVial)).discard_by = date.today() - timedelta(days=1)
        s.commit()
    recon(client, peptide)
    assert counts({"bac": ids["bac"]}) == {"bac": 0} and len(bac_vials()) == 2


def test_a_bottle_that_is_used_up_leaves_the_active_list(client, db, me, peptide):
    stock_everything(me, bac_bottles=0)
    make_bac(me, "Tiny BAC", priority=1, volume=2.0)
    recon(client, peptide)
    (vial,) = bac_vials()
    assert vial.volume_remaining_ml == pytest.approx(0.0) and vial.discarded_at is not None


def test_no_bac_water_at_all_blocks_the_reconstitution_with_the_reason(client, db, me, peptide):
    ids = stock_everything(me, bac_bottles=0)
    before = counts(ids)
    r = recon(client, peptide)
    assert "No BAC Water Available" in error_text(r)
    assert counts(ids) == before and bac_vials() == []
    with SessionLocal() as s:
        assert s.get(InventoryItem, peptide).reconstituted_count == 0


def test_a_new_bottle_too_small_for_the_water_blocks(client, db, me, peptide):
    stock_everything(me, bac_bottles=0)
    make_bac(me, "Tiny BAC", priority=1, volume=1.0)
    assert "Not enough BAC" in error_text(recon(client, peptide))


# ---------------------------------------------------------------- supplies

def test_a_reconstitution_uses_four_pads_and_one_syringe(client, db, me, peptide):
    ids = stock_everything(me)
    recon(client, peptide)
    after = counts(ids)
    assert (after["pads"], after["recon"]) == (16, 9)
    assert (after["dosing"], after["pen_vial"], after["pen_needle"]) == (10, 10, 10)                  # no pen: those stay


def test_supplies_come_from_whichever_items_of_the_type_have_stock(client, db, me, peptide):
    ids = stock_everything(me, pads=0)
    a = make_supply(me, "Pads brand A", SupplyType.ALCOHOL_PAD, 3)
    b = make_supply(me, "Pads brand B", SupplyType.ALCOHOL_PAD, 10)
    recon(client, peptide)
    assert counts({"a": a, "b": b}) == {"a": 0, "b": 9}                                                # 3 from A then 1 from B


def test_an_item_with_no_supply_type_is_never_used(client, db, me, peptide):
    ids = stock_everything(me, pads=0)
    untyped = make_supply(me, "Pads without a type", None, 50)
    r = recon(client, peptide)
    assert "No Alcohol Prep Pads Available" in error_text(r) and counts({"u": untyped}) == {"u": 50}


@pytest.mark.parametrize("key,message", [("recon", "No Reconstitution Syringes Available"), ("pads", "No Alcohol Prep Pads Available")])
def test_a_missing_supply_blocks_everything_with_the_reason(client, db, me, peptide, key, message):
    ids = stock_everything(me, **{"recon_syringes": 0} if key == "recon" else {"pads": 0})
    before = counts(ids)
    r = recon(client, peptide)
    assert r.status_code == 303 and message in error_text(r)
    assert counts(ids) == before and bac_vials() == []
    with SessionLocal() as s:
        assert s.get(InventoryItem, peptide).reconstituted_count == 0 and s.query(ActiveVial).count() == 0


def test_too_few_pads_says_how_many_are_needed(client, db, me, peptide):
    stock_everything(me, pads=3)
    text = error_text(recon(client, peptide))
    assert "Not enough Alcohol Prep Pads" in text and "3 available" in text and "4 needed" in text


def test_the_shortage_message_reaches_the_calculator_page(client, db, me, peptide):
    stock_everything(me, recon_syringes=0)
    r = recon(client, peptide)
    assert "No Reconstitution Syringes Available" in html.unescape(client.get(r.headers["location"]).text)


# ---------------------------------------------------------------- the pen

def test_loading_into_a_pen_at_reconstitution_also_uses_the_pen_supplies(client, db, me, peptide):
    ids = stock_everything(me)
    recon(client, peptide, load_into_pen="1")
    after = counts(ids)
    assert (after["pads"], after["recon"], after["pen_vial"], after["dosing"], after["pen_needle"]) == (14, 9, 9, 9, 9)
    with SessionLocal() as s:
        assert s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == peptide)).dispensing_method.value == "pen"


def test_a_missing_pen_item_blocks_the_whole_reconstitution_and_uses_nothing(client, db, me, peptide):
    ids = stock_everything(me, pen_needles=0)
    before = counts(ids)
    r = recon(client, peptide, load_into_pen="1")
    assert "No Peptide Pen Needles Available" in error_text(r)
    assert counts(ids) == before and bac_vials() == []
    with SessionLocal() as s:
        assert s.get(InventoryItem, peptide).reconstituted_count == 0


def vial_of(item_id):
    with SessionLocal() as s:
        return s.scalar(select(ActiveVial.id).where(ActiveVial.inventory_item_id == item_id))


def test_converting_a_vial_to_a_pen_later_uses_the_pen_supplies(client, db, me, peptide):
    ids = stock_everything(me)
    recon(client, peptide)
    before = counts(ids)
    r = client.post(f"/active-vials/{vial_of(peptide)}/convert-to-pen", follow_redirects=False)
    assert r.status_code == 303
    after = counts(ids)
    assert (after["pads"], after["pen_vial"], after["dosing"], after["pen_needle"]) == (before["pads"] - 2, 9, 9, 9)
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_of(peptide)).dispensing_method.value == "pen"


def test_converting_without_a_pen_item_is_refused_with_the_reason_and_changes_nothing(client, db, me, peptide):
    ids = stock_everything(me, pen_needles=0)
    recon(client, peptide)
    before = counts(ids)
    r = client.post(f"/active-vials/{vial_of(peptide)}/convert-to-pen", follow_redirects=False)
    assert r.status_code == 303 and "pen_error=" in r.headers["location"]
    assert "No Peptide Pen Needles Available" in html.unescape(client.get(r.headers["location"]).text)
    assert counts(ids) == before
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_of(peptide)).dispensing_method.value == "syringe"


def test_a_bac_vial_cannot_be_converted_to_a_pen(client, db, me, peptide):
    stock_everything(me)
    recon(client, peptide)
    (vial,) = bac_vials()
    assert client.post(f"/active-vials/{vial.id}/convert-to-pen", follow_redirects=False).status_code == 404


# ---------------------------------------------------------------- what the page shows

def test_an_open_bac_vial_shows_room_temperature_the_water_left_and_the_use_by_date_without_peptide_buttons(client, db, me, peptide):
    stock_everything(me)
    recon(client, peptide)
    page = html.unescape(client.get("/inventory").text)
    assert "Test BAC water" in page and "Room temperature" in page and "do not refrigerate" in page.lower()
    assert "28 mL left" in page and "Use by" in page
    start = page.index('id="active-vials"')
    section = page[start:page.index("</section>", start)]
    assert section.count("Convert to peptide pen") == 1                    # only the peptide vial offers a pen; the BAC vial never does
    assert "vial-card-label" not in section                                # the old text-on-the-picture layout is gone


def test_the_dashboard_still_loads_with_an_open_bac_vial(client, db, me, peptide):
    stock_everything(me)
    recon(client, peptide)
    assert client.get("/dashboard").status_code == 200


# ---------------------------------------------------------------- the item form

def make_item(client, **fields):
    return client.post("/inventory", data=fields, follow_redirects=False)


def test_a_supply_item_can_be_given_a_supply_type(client, db, me):
    r = make_item(client, category="Supply", name="Alcohol pads", count="100", supply_type="alcohol_prep_pad")
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.scalar(select(InventoryItem).where(InventoryItem.name == "Alcohol pads")).supply_type == SupplyType.ALCOHOL_PAD


def test_an_unknown_supply_type_is_refused(client, db, me):
    assert make_item(client, category="Supply", name="X", count="1", supply_type="mystery").status_code == 422


def test_bac_water_takes_a_priority_a_bottle_size_and_defaults_to_room_temperature(client, db, me):
    r = make_item(client, category="BAC Water", name="Some BAC", bac_priority="2", bac_volume="30", quantity="2", order_date="2026-08-01")
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Some BAC"))
        assert (item.bac_priority, item.volume_ml, item.storage.value) == (2, 30.0, "room_temp")


@pytest.mark.parametrize("fields", [{"bac_priority": "9"}, {"bac_priority": "x"}, {"bac_volume": "-1"}, {"bac_volume": "abc"}])
def test_bad_bac_priority_or_volume_is_refused(client, db, me, fields):
    r = make_item(client, category="BAC Water", name="Bad BAC", quantity="1", order_date="2026-08-01", **fields)
    assert r.status_code == 422


def test_a_peptide_ignores_supply_type_and_bac_fields(client, db, me):
    make_item(client, category="Medicine", name="Pepx", medium="Lyophilized", vial_size_mg="10", quantity="1", order_date="2026-08-01",
              supply_type="alcohol_prep_pad", bac_priority="1")
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Pepx"))
        assert (item.supply_type, item.bac_priority) == (None, None)


def test_the_form_offers_the_supply_types_and_priorities(client, db, me):
    page = html.unescape(client.get("/inventory").text)
    for label in ("Reconstitution Syringe", "Dosing Syringe", "Alcohol Prep Pad", "Peptide Pen Vial", "Peptide Pen Needle", "10mL Sterile Vial", "Peptide Filter",
                  "BAC priority", "1st choice"):
        assert label in page


def test_editing_shows_the_saved_supply_type(client, db, me):
    item_id = make_supply(me, "Pads", SupplyType.ALCOHOL_PAD, 5)
    page = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert "alcohol_prep_pad" in page

"""Dual chamber cartridges: dry drug and diluent in one cartridge. Their prices and sizes are kept apart from plain vials. Invented names only."""

from datetime import date

import pytest

from app.inventory.rules import field_label, required_fields_for
from app.library.price_lists.analysis import best_prices, price_range
from app.library.price_lists.reader import rows_from_lines, rows_from_table
from app.library.price_lists.rows import Spec, parse_spec
from app.library.price_lists.specs import format_spec, merge_specs
from app.library.price_lists.vendor_view import build_price_compare
from app.models import Medium
from price_helpers import item, make_card, make_list, make_vendor

DC = "dual_chamber"


@pytest.mark.parametrize("text", ["10mg Dual Chamber Cartridge", "10 mg dual-chamber cartridge", "10mg DUAL CHAMBER", "10mg Dual-Chamber Cartridge*1"])
def test_a_spec_that_says_dual_chamber_is_marked_as_one(text):
    spec = parse_spec(text)
    assert (spec.amount, spec.unit, spec.form) == (10, "mg", DC)


def test_a_plain_spec_has_no_form_and_equals_the_old_spec():
    assert parse_spec("10mg*10vials") == Spec(10, "mg", 10)
    assert parse_spec("10mg*10vials").form is None


def test_a_table_row_picks_up_the_form_from_the_spec_cell_or_the_name_cell():
    table = [["Product name", "Code", "Specification", "Price"],
             ["Zorvex", "ZX10", "10mg Dual Chamber Cartridge", "$40"],
             ["Zorvex Dual Chamber Cartridge", "ZX20", "20mg", "$70"],
             ["Zorvex", "ZX30", "30mg*10vials", "$90"]]
    rows = rows_from_table(table)
    assert [r.spec.form for r in rows] == [DC, DC, None]


def test_a_line_row_picks_up_the_form():
    (row,) = rows_from_lines(["Zorvex ZX10 10mg Dual Chamber Cartridge $40"])
    assert row.spec.form == DC


def test_the_size_label_says_the_form_and_sorts_with_the_other_sizes():
    assert format_spec(10, "mg", DC) == "10mg Dual Chamber Cartridge"
    assert format_spec(10, "mg") == "10mg"
    assert merge_specs("5mg, 10mg", "10mg Dual Chamber Cartridge, 10mg") == "5mg, 10mg, 10mg Dual Chamber Cartridge"


def cartridge(name, amount, price, card, form=DC):
    it = item(name, amount, price, card=card, pack=1)
    it.form = form
    return it


def test_cartridge_prices_do_not_mix_into_the_vial_price_range_or_the_best_price_lists(db):
    card = make_card(db, "Zorvex")
    vendor = make_vendor(db, "Acme")
    make_list(db, vendor, date(2026, 9, 1), item("Zorvex", 10, 80, card=card, pack=10), cartridge("Zorvex", 10, 40, card))
    rng = price_range(db, card.id)
    assert (rng.amount, rng.unit, rng.low, rng.high) == (10, "mg", 8.0, 8.0)          # the vial price only; the $40 cartridge is not an "$8 to $40" range
    options = best_prices(db, card.id)
    assert [(o.amount, o.unit, o.form) for o in options] == [(10, "mg", None), (10, "mg", DC)]
    assert options[1].vendors[0].per_vial == 40.0


def test_the_price_popup_labels_a_cartridge_size(db):
    card = make_card(db, "Zorvex")
    vendor = make_vendor(db, "Acme")
    make_list(db, vendor, date(2026, 9, 1), item("Zorvex", 10, 80, card=card, pack=10), cartridge("Zorvex", 10, 40, card))
    popup = build_price_compare(db, card.id, price_range(db, card.id))
    assert [s["label"] for s in popup["sizes"]] == ["10mg", "10mg dual chamber cartridge"]


def test_a_dual_chamber_cartridge_is_a_medium_that_needs_an_amount_per_cartridge():
    assert Medium.DUAL_CHAMBER.value == "Dual Chamber Cartridge"
    assert required_fields_for(Medium.DUAL_CHAMBER) == {"vial_size_mg"}
    assert field_label("vial_size_mg", Medium.DUAL_CHAMBER) == "Amount per cartridge"


def test_an_inventory_item_can_be_a_dual_chamber_cartridge_and_a_dose_uses_a_share_of_one(client, me):
    from datetime import timedelta
    from sqlalchemy import select
    from app import clock
    from app.db import SessionLocal
    from app.inventory import unit_use
    from app.models import DoseUnit, InventoryItem
    data = {"name": "Dualtest Zorvex", "category": "Medicine", "medium": "Dual Chamber Cartridge", "vial_size_mg": "10", "vial_size_unit": "mg", "quantity": "4",
            "order_date": (clock.today() - timedelta(days=3)).isoformat(), "is_active": "1"}
    try:
        r = client.post("/inventory", data=data, follow_redirects=False)
        assert r.status_code == 303, r.text[:300]
        with SessionLocal() as s:
            item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Dualtest Zorvex"))
            assert item.medium is Medium.DUAL_CHAMBER and item.is_active
            assert unit_use.tracks_units(item) and unit_use.units_for_dose(item, 2.5, DoseUnit.MG) == pytest.approx(0.25)
    finally:
        with SessionLocal() as s:
            from app.models import Order
            s.query(InventoryItem).filter(InventoryItem.name == "Dualtest Zorvex").delete(synchronize_session=False)
            s.query(Order).filter(~Order.items.any()).delete(synchronize_session=False)
            s.commit()

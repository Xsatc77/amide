from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Peptide, PeptideSource, PriceAlertIgnore, PriceList, PriceListItem, Vendor, Warehouse, WarehouseSource,
)

# Invented vendors (Acme, Zephyr, Borealis) and peptides (Zorvex, Quillamine) only.


def make_vendor(db, name):
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    return vendor


def make_card(db, name, aliases=None):
    card = Peptide(name=name, aliases=aliases, source=PeptideSource.CUSTOM)
    db.add(card)
    db.commit()
    return card


def item(name, amount, price, *, unit="mg", pack=10, card=None, code="ZX"):
    pack_type = "kit" if pack == 10 else "box" if pack and pack < 10 else None
    return PriceListItem(code=code, product_name=name, peptide_id=card.id if card else None, vial_amount=amount,
                         vial_unit=unit, pack_size=pack, pack_price=price, pack_type=pack_type)


def make_list(db, vendor, when, *items, warehouse=Warehouse.CHINA):
    plist = PriceList(vendor_name=vendor.name, vendor_id=vendor.id, warehouse=warehouse,
                      warehouse_source=WarehouseSource.FILENAME, list_date=when,
                      source_filename=f"{vendor.name} - {warehouse.value} - {when.isoformat()}.pdf")
    plist.items.extend(items)
    db.add(plist)
    db.commit()
    return plist


# ---------------------------------------------------------------- ignore table

def test_an_ignored_product_round_trips_and_its_key_is_unique(db):
    db.add(PriceAlertIgnore(product_key="zorvex", product_name="Zorvex"))
    db.commit()
    assert db.query(PriceAlertIgnore).one().product_name == "Zorvex"
    db.add(PriceAlertIgnore(product_key="zorvex", product_name="Zorvex again"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ---------------------------------------------------------------- current lists

def test_current_list_is_the_newest_per_vendor_and_warehouse(db):
    from app.library.price_lists.analysis import current_lists
    acme, zephyr = make_vendor(db, "Acme"), make_vendor(db, "Zephyr")
    make_list(db, acme, date(2026, 8, 1), item("Zorvex", 10, 50))
    newest = make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 55))
    usa = make_list(db, acme, date(2026, 7, 1), item("Zorvex", 10, 60), warehouse=Warehouse.US)
    other = make_list(db, zephyr, date(2026, 6, 1), item("Zorvex", 10, 40))
    assert {p.id for p in current_lists(db)} == {newest.id, usa.id, other.id}


# ---------------------------------------------------------------- price range (per vial)

def test_range_is_per_vial_for_the_most_commonly_listed_size(db):
    from app.library.price_lists.analysis import price_range
    card = make_card(db, "Zorvex")
    acme, zephyr, borealis = (make_vendor(db, n) for n in ("Acme", "Zephyr", "Borealis"))
    make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 50, card=card), item("Zorvex", 5, 30, card=card))
    make_list(db, zephyr, date(2026, 9, 2), item("Zorvex", 10, 80, card=card))
    make_list(db, borealis, date(2026, 9, 3), item("Zorvex", 5, 20, card=card), item("Zorvex", 10, 60, card=card))
    r = price_range(db, card.id)
    assert (r.amount, r.unit, r.low, r.high, r.lists) == (10, "mg", 5.0, 8.0, 3)  # 10mg: 3 lists, 5mg: 2


def test_a_single_price_gives_equal_low_and_high(db):
    from app.library.price_lists.analysis import price_range
    card = make_card(db, "Zorvex")
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Zorvex", 10, 50, card=card))
    r = price_range(db, card.id)
    assert (r.low, r.high, r.lists) == (5.0, 5.0, 1)


def test_range_counts_boxes_per_vial_and_skips_unsized_packs_and_liquids(db):
    from app.library.price_lists.analysis import price_range
    card = make_card(db, "Zorvex")
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1),
              item("Zorvex", 250, 30, pack=1, card=card),          # a one-vial box: 30.00 per vial
              item("Zorvex", 250, 99, pack=None, card=card),       # no pack size: skipped
              item("Zorvex", 10, 5, unit="ml", card=card))         # a liquid: skipped
    r = price_range(db, card.id)
    assert (r.amount, r.low, r.high) == (250, 30.0, 30.0)


def test_range_uses_only_current_lists_and_none_without_prices(db):
    from app.library.price_lists.analysis import price_range
    card = make_card(db, "Zorvex")
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 8, 1), item("Zorvex", 10, 500, card=card))  # superseded
    assert price_range(db, card.id).low == 50.0  # until a newer list arrives
    make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 50, card=card))
    r = price_range(db, card.id)
    assert (r.low, r.high, r.lists) == (5.0, 5.0, 1)
    assert price_range(db, make_card(db, "Quillamine").id) is None


# ---------------------------------------------------------------- vendor price history

def test_history_groups_by_product_then_size_and_warehouse_keeping_every_dated_price(db):
    from app.library.price_lists.analysis import vendor_price_history
    card = make_card(db, "Zorvex")
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 8, 1), item("Zorvex", 10, 50, card=card), item("Zorvex", 5, 30, card=card),
              item("Quillamine X", 5, 20))
    make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 55, card=card))
    make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 70, card=card), warehouse=Warehouse.US)
    history = vendor_price_history(db, acme.id)
    assert [p.label for p in history] == ["Quillamine X", "Zorvex"]
    zorvex = history[1]
    assert [(pt.list_date, pt.pack_price) for pt in zorvex.series[(10.0, "mg", "china")]] == [
        (date(2026, 8, 1), 50.0), (date(2026, 9, 1), 55.0)]
    assert zorvex.series[(10.0, "mg", "us")][0].pack_price == 70.0
    assert zorvex.series[(5.0, "mg", "china")][0].pack_price == 30.0
    point = zorvex.series[(10.0, "mg", "china")][0]
    assert (point.pack_size, point.pack_type, point.per_vial) == (10, "kit", 5.0)


def test_history_keeps_the_lowest_price_when_a_list_repeats_a_size_and_groups_unmatched_names(db):
    from app.library.price_lists.analysis import vendor_price_history
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 9, 1), item("Quillamine X", 5, 25), item("quillamine x", 5, 20),
              item("Quillamine X", 10, None))
    (product,) = vendor_price_history(db, acme.id)
    assert [pt.pack_price for pt in product.series[(5.0, "mg", "china")]] == [20.0]
    assert list(product.series) == [(5.0, "mg", "china")]  # the unpriced row adds nothing


def test_history_is_only_this_vendors_and_empty_without_lists(db):
    from app.library.price_lists.analysis import vendor_price_history
    acme, zephyr = make_vendor(db, "Acme"), make_vendor(db, "Zephyr")
    make_list(db, zephyr, date(2026, 9, 1), item("Zorvex", 10, 50))
    assert vendor_price_history(db, acme.id) == []


# ---------------------------------------------------------------- new peptides

def test_new_peptides_are_unmatched_sized_products_with_their_vendors(db):
    from app.library.price_lists.analysis import new_peptides
    make_card(db, "Zorvex")
    acme, zephyr = make_vendor(db, "Acme"), make_vendor(db, "Zephyr")
    make_list(db, acme, date(2026, 9, 1), item("Quillamine", 5, 20), item("Zorvex", 10, 50),
              item("Diluent Water", 10, 5, unit="ml"))
    make_list(db, zephyr, date(2026, 9, 2), item("quillamine", 5, 22), item("Marnitol", 5, 9))
    assert [(n.name, n.vendors) for n in new_peptides(db)] == [
        ("Marnitol", ("Zephyr",)), ("Quillamine", ("Acme", "Zephyr"))]


def test_a_product_dropped_from_the_newest_list_no_longer_alerts(db):
    from app.library.price_lists.analysis import new_peptides
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 8, 1), item("Quillamine", 5, 20))
    make_list(db, acme, date(2026, 9, 1), item("Marnitol", 5, 9))
    assert [n.name for n in new_peptides(db)] == ["Marnitol"]


def test_ignoring_a_product_clears_it(db):
    from app.library.price_lists.analysis import ignore_product, new_peptides
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Quillamine", 5, 20), item("Marnitol", 5, 9))
    ignore_product(db, "quillamine")
    db.commit()
    assert [n.name for n in new_peptides(db)] == ["Marnitol"]
    ignore_product(db, "Quillamine")  # twice is harmless
    db.commit()
    assert db.query(PriceAlertIgnore).count() == 1


def test_adding_a_card_or_an_alias_clears_the_alert_by_itself(db):
    from app.library.price_lists.analysis import new_peptides
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Quillamine", 5, 20), item("Marnitol", 5, 9))
    assert [n.name for n in new_peptides(db)] == ["Marnitol", "Quillamine"]
    make_card(db, "Quillamine")
    make_card(db, "Mannitol Peptide", aliases="Marnitol")
    assert new_peptides(db) == []


def test_products_already_matched_at_import_never_alert(db):
    from app.library.price_lists.analysis import new_peptides
    card = make_card(db, "Zorvex")
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Zorvex Special", 5, 20, card=card))
    assert new_peptides(db) == []

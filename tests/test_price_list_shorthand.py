"""Price lists posted as plain message text in vendor shorthand ("T10 $41", "R10: 77/kit"): codes with the vial size in them, a price, and
no spec. Prices and layout here are invented; the shapes are what vendors post in their group chats."""

from datetime import date

import pytest
from sqlalchemy import select

from app.ingest import readers
from app.library.price_lists.importer import store_price_list
from app.library.price_lists.names import DEFAULT_PREFIX_NAMES, apply_default_names
from app.library.price_lists.reader import rows_from_shorthand
from app.models import Peptide, PeptideSource, PriceList, Vendor
from price_helpers import make_card, make_vendor

CHINA = """Dear friends
T10,KISS,MT1,MT2,CAG,kpv,PT141 has arrived, new update coming
Warehouse stock(1.01)
Sema20 $81
T10 $41
T30$77
R10$93
R40 $251
Glow70 $149
Klow $163
Mots10$63
Tesa10 $171
5 Amino 50mg $139
BPC5mg+TB5mg=BB10mg $87
Ahk100 $91
BAC10 $9
"""

US = """US warehouse
Until 1.02 (US time)
Shipping Fee： 17$
R10: 87/kit
R20
TESA10： 151/kit
BPC10:71/kit
CU50: 49/kit
SS31-10： 89/kit
KLOW80： 183/kit
MOT10： 69/kit
Semax10： 66/kit
"""


def rows(text):
    return {(r.code, r.name): r for r in rows_from_shorthand([line.strip() for line in text.splitlines() if line.strip()])}


def test_code_amount_and_price_become_a_kit_row():
    r = rows("T10 $41")[("T10", None)]
    assert (r.spec.amount, r.spec.unit, r.spec.pack_size, r.pack_price) == (10.0, "mg", 10, 41.0)
    assert "text-shorthand" in r.flags


@pytest.mark.parametrize("line,code,amount,price", [
    ("T30$77", "T30", 30, 77), ("R10: 87/kit", "R10", 10, 87), ("TESA10： 151/kit", "TESA10", 10, 151), ("Mots10$63", "Mots10", 10, 63),
    ("Sema20 $81", "Sema20", 20, 81), ("Ahk100 $91", "Ahk100", 100, 91), ("R40 $251", "R40", 40, 251), ("BPC10:71/kit", "BPC10", 10, 71),
    ("Glow70 $149", "Glow70", 70, 149), ("CU50: 49/kit", "CU50", 50, 49), ("KLOW80： 183/kit", "KLOW80", 80, 183),
])
def test_the_ways_vendors_write_a_code_and_price(line, code, amount, price):
    [r] = rows_from_shorthand([line])
    assert (r.code, r.spec.amount, r.pack_price) == (code, amount, price)


def test_a_hyphenated_code_takes_the_last_number_as_the_size_and_a_blend_line_uses_the_right_of_the_equals_sign():
    [ss] = rows_from_shorthand(["SS31-10： 89/kit"])
    assert ss.spec.amount == 10 and ss.code == "SS10" and ss.pack_price == 89
    [bb] = rows_from_shorthand(["BPC5mg+TB5mg=BB10mg $87"])
    assert bb.code == "BB10" and bb.spec.amount == 10 and bb.pack_price == 87


def test_a_name_then_size_line_keeps_the_name():
    [r] = rows_from_shorthand(["5 Amino 50mg $139"])
    assert (r.name, r.spec.amount, r.spec.unit, r.pack_price) == ("5 Amino", 50, "mg", 139)


def test_several_items_on_one_line_are_read_separately():
    found = rows_from_shorthand(["Warehouse stock(1.01) T10 $41 T20 $63 R10 $93..."])
    assert [(r.code, r.pack_price) for r in found] == [("T10", 41.0), ("T20", 63.0), ("R10", 93.0)]


def test_bacteriostatic_water_is_in_ml():
    [r] = rows_from_shorthand(["BAC10 $9"])
    assert r.spec.unit == "ml" and r.spec.amount == 10


def test_chatter_shipping_fees_unpriced_and_unsized_lines_are_not_products():
    skipped = ["Shipping Fee： 17$", "order over $350 (not include bac), offer free shipping", "R20", "Klow $163", "Until 1.02 (US time)",
               "T10,KISS,MT1,MT2,CAG,kpv,PT141 has arrived", "Dear friends", "Warehouse stock(1.01)"]
    assert rows_from_shorthand(skipped) == []


def test_a_whole_china_style_message_is_a_price_list_with_the_warehouse_from_its_text():
    data = readers.read_text(CHINA)
    codes = {r.code for r in data.rows}
    assert {"Sema20", "T10", "T30", "R10", "R40", "Glow70", "Mots10", "Tesa10", "BB10", "Ahk100", "BAC10"} <= codes
    assert readers.looks_like_price_list(data, None, None)[0] is True
    assert data.warehouse_hint is None or data.warehouse_hint == "china"


def test_a_us_style_message_reads_its_shipping_note_and_prices_per_kit():
    data = readers.read_text(US)
    assert {r.code for r in data.rows} == {"R10", "TESA10", "BPC10", "CU50", "SS10", "KLOW80", "MOT10", "Semax10"}
    assert data.shipping_note and "17" in data.shipping_note
    assert readers.looks_like_price_list(data, None, None)[0] is True


def test_lines_with_a_full_spec_still_read_as_before():
    data = readers.read_text("Zorvex ZX10 10mg*10vials $55\nQuillamine QM5 5mg*10vials $31\nBorealin BR20 20mg*10vials $97")
    assert [r.pack_price for r in data.rows] == [55.0, 31.0, 97.0]


def test_default_names_fill_a_nameless_row_when_the_library_knows_the_product_and_say_so():
    found = rows_from_shorthand(["T10 $41", "Tesa10 $171", "Zzq10 $12", "BAC10 $9", "SS31-10： 89/kit"])
    apply_default_names(found, lambda name: name in {"Tirzepatide", "Tesamorelin", "SS-31 (Elamipretide)"})
    by_code = {r.code: r for r in found}
    assert by_code["T10"].name == "Tirzepatide" and "name-from-shorthand" in by_code["T10"].flags
    assert by_code["Tesa10"].name == "Tesamorelin" and by_code["SS10"].name == "SS-31 (Elamipretide)"
    assert by_code["Zzq10"].name is None
    assert by_code["BAC10"].name == "BAC Water"                     # water is not a library peptide, so it needs no library match


def test_a_name_a_row_already_has_is_never_replaced():
    [r] = rows_from_shorthand(["5 Amino 50mg $139"])
    apply_default_names([r], lambda name: True)
    assert r.name == "5 Amino" and "name-from-shorthand" not in r.flags


def test_the_default_table_only_names_things_with_distinct_abbreviations():
    assert DEFAULT_PREFIX_NAMES["T"] == "Tirzepatide" and DEFAULT_PREFIX_NAMES["R"] == "Retatrutide" and "MT" not in DEFAULT_PREFIX_NAMES


def test_a_shorthand_list_imports_end_to_end_matched_to_library_peptides(db):
    vendor = make_vendor(db, "Shorthand Test Vendor")
    card = db.scalar(select(Peptide).where(Peptide.name == "Tirzepatide")) or make_card(db, "Tirzepatide")
    data = readers.read_text("Warehouse stock(1.01)\nT10 $41\nT30$77\nR10$93\nTesa10 $171\n")
    report = store_price_list(db, "shorthand test - china - 2026-10-14.pdf", data, prefix_table={}, vendor=vendor, list_date=date(2026, 10, 14))
    assert report.skipped is None and report.rows == 4
    plist = db.scalar(select(PriceList).where(PriceList.id == report.list_id))
    t10 = next(i for i in plist.items if i.code == "T10")
    assert t10.product_name == "Tirzepatide" and t10.peptide_id == card.id and t10.pack_size == 10 and t10.pack_price == 41.0
    assert "name-from-shorthand" in (t10.flags or [])
    db.query(PriceList).filter(PriceList.id == plist.id).delete()
    db.query(Vendor).filter(Vendor.id == vendor.id).delete()
    if card.source == PeptideSource.CUSTOM:
        db.query(Peptide).filter(Peptide.id == card.id).delete()
    db.commit()

"""Every way a vendor writes vitamin B-12 finds the B-12 card, and only that card."""

from types import SimpleNamespace as NS

import pytest

from app.library.matching import match_name

B12 = NS(id=1, name="B-12", aliases="Vitamin B12, Cyanocobalamin, Cobalamin, Cyanocobalamin injection")
CARDS = [
    B12,
    NS(id=2, name="BPC-157", aliases="BPC 157"),
    NS(id=3, name="Lipo-C with B12", aliases=None),
    NS(id=4, name="Lipo-C without B12", aliases=None),
    NS(id=5, name="Vitamin D3", aliases=None),
    NS(id=6, name="Semaglutide", aliases=None),
]


@pytest.mark.parametrize("vendor_name", [
    "B-12", "B12", "b12", "B 12", "B_12", "B.12", "B - 12", "Vitamin B12", "Vitamin B-12", "Vitamin B 12", "Vit B12", "Vit. B12", "Vit B-12",
    "VB12", "V-B12", "V B12", "Cyanocobalamin", "Cobalamin", "B12 (Cyanocobalamin)", "Methylcobalamin", "Mecobalamin", "Methyl B12",
    "Methyl-B12", "MB12", "Hydroxocobalamin", "Hydroxy B12", "Adenosylcobalamin", "Methylcobalamin (B12)",
])
def test_every_spelling_of_b12_finds_the_b12_card(vendor_name):
    match = match_name(vendor_name, CARDS)
    assert match is not None and [c.name for c in match.cards] == ["B-12"], vendor_name


@pytest.mark.parametrize("vendor_name, expected", [
    ("Lipo-C with B12", "Lipo-C with B12"), ("Lipo-C without B12", "Lipo-C without B12"),      # B12 inside a different product stays that product
    ("Vitamin D3", "Vitamin D3"),                                                              # and the vitamin-word rule does not touch other vitamins
    ("BPC157", "BPC-157"),
])
def test_other_products_are_not_pulled_onto_the_b12_card(vendor_name, expected):
    match = match_name(vendor_name, CARDS)
    assert match is not None and [c.name for c in match.cards] == [expected], vendor_name


@pytest.mark.parametrize("vendor_name", ["B6", "B-6", "Vitamin B6", "B120", "B-1", "Vitamin C"])
def test_other_b_vitamins_do_not_match(vendor_name):
    match = match_name(vendor_name, CARDS)
    assert match is None or "B-12" not in [c.name for c in match.cards], vendor_name


def test_lines_left_unmatched_before_are_linked_to_the_b12_card_by_the_rematch(db):
    from datetime import date

    from app.library.price_lists.analysis import rematch_items
    from price_helpers import item, make_card, make_list, make_vendor
    card = make_card(db, "B-12")
    vendor = make_vendor(db, "Acme")
    plist = make_list(db, vendor, date(2026, 9, 1), item("VB12", 10, 58, unit="ml", card=None), item("Methylcobalamin", 10, 40, card=None), item("Zephyr Other", 10, 40, card=None))
    assert rematch_items(db) == 2
    db.commit()
    db.refresh(plist)
    assert [i.peptide_id == card.id for i in plist.items] == [True, True, False]

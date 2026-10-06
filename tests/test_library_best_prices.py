"""The library card's "best price per vial" popup: top vendors per vial size. Invented vendors and peptides only."""

import html
import json
import re
from datetime import date

from app.library.price_lists.analysis import best_prices
from app.models import Peptide, PeptideSource, Warehouse
from price_helpers import item, make_card, make_list, make_vendor


def stock(db, vendor, price, *, size=10, pack=10, when=date(2026, 9, 1), warehouse=Warehouse.CHINA, card=None, unit="mg"):
    return make_list(db, vendor, when, item(card.name, size, price, card=card, pack=pack, unit=unit), warehouse=warehouse)


def vendors(db, *names):
    return [make_vendor(db, n) for n in names]


def names(option):
    return [v.vendor_name for v in option.vendors]


def test_vendors_are_ranked_by_price_per_vial_cheapest_first(db):
    card = make_card(db, "Zorvex")
    a, b, c = vendors(db, "Acme", "Borealis", "Cygnus")
    stock(db, a, 80, card=card)       # $8.00 per vial
    stock(db, b, 50, card=card)       # $5.00
    stock(db, c, 65, card=card)       # $6.50
    (option,) = best_prices(db, card.id)
    assert names(option) == ["Borealis", "Cygnus", "Acme"]
    assert [v.per_vial for v in option.vendors] == [5.0, 6.5, 8.0]


def test_the_price_is_per_vial_not_per_pack(db):
    card = make_card(db, "Zorvex")
    (a,) = vendors(db, "Acme")
    stock(db, a, 72, card=card, pack=10)
    (option,) = best_prices(db, card.id)
    (v,) = option.vendors
    assert (v.per_vial, v.pack_price, v.pack_size) == (7.2, 72.0, 10)


def test_only_the_top_five_vendors_are_listed(db):
    card = make_card(db, "Zorvex")
    for i, vendor in enumerate(vendors(db, *"ABCDEFG")):
        stock(db, vendor, 50 + i, card=card)
    (option,) = best_prices(db, card.id)
    assert names(option) == list("ABCDE")


def test_each_vial_size_is_its_own_option_ordered_smallest_first(db):
    card = make_card(db, "Zorvex")
    (a,) = vendors(db, "Acme")
    make_list(db, a, date(2026, 9, 1), item("Zorvex", 20, 150, card=card), item("Zorvex", 5, 40, card=card),
              item("Zorvex", 10, 70, card=card))
    options = best_prices(db, card.id)
    assert [(o.amount, o.unit) for o in options] == [(5, "mg"), (10, "mg"), (20, "mg")]


def test_a_vendor_appears_once_with_its_cheapest_warehouse(db):
    card = make_card(db, "Zorvex")
    (a,) = vendors(db, "Acme")
    stock(db, a, 80, card=card, warehouse=Warehouse.US)
    stock(db, a, 55, card=card, warehouse=Warehouse.CHINA)
    (option,) = best_prices(db, card.id)
    (v,) = option.vendors
    assert (v.warehouse, v.per_vial) == ("china", 5.5)


def test_a_tie_between_a_vendors_own_warehouses_goes_to_the_us_warehouse(db):
    card = make_card(db, "Zorvex")
    (a,) = vendors(db, "Acme")
    stock(db, a, 60, card=card, warehouse=Warehouse.CHINA)
    stock(db, a, 60, card=card, warehouse=Warehouse.US)
    (option,) = best_prices(db, card.id)
    assert option.vendors[0].warehouse == "us"


def test_a_tie_between_vendors_puts_the_us_warehouse_first(db):
    card = make_card(db, "Zorvex")
    a, b, c = vendors(db, "Acme", "Borealis", "Cygnus")
    stock(db, a, 60, card=card, warehouse=Warehouse.CHINA)
    stock(db, b, 60, card=card, warehouse=Warehouse.US)
    stock(db, c, 60, card=card, warehouse=Warehouse.CHINA)
    (option,) = best_prices(db, card.id)
    assert names(option) == ["Borealis", "Acme", "Cygnus"]          # US first, then by name


def test_prices_that_round_to_the_same_cent_tie(db):
    card = make_card(db, "Zorvex")
    a, b = vendors(db, "Acme", "Borealis")
    stock(db, a, 60.001, card=card, warehouse=Warehouse.CHINA)
    stock(db, b, 60.0, card=card, warehouse=Warehouse.US)
    (option,) = best_prices(db, card.id)
    assert names(option) == ["Borealis", "Acme"]


def test_only_each_vendors_current_list_counts(db):
    card = make_card(db, "Zorvex")
    (a,) = vendors(db, "Acme")
    stock(db, a, 40, card=card, when=date(2026, 8, 1))
    stock(db, a, 70, card=card, when=date(2026, 9, 1))
    (option,) = best_prices(db, card.id)
    assert option.vendors[0].per_vial == 7.0 and option.vendors[0].list_date == date(2026, 9, 1)


def test_lines_without_a_pack_size_or_price_cannot_give_a_per_vial_price_and_are_left_out(db):
    card = make_card(db, "Zorvex")
    (a,) = vendors(db, "Acme")
    make_list(db, a, date(2026, 9, 1), item("Zorvex", 10, 70, card=card, pack=None), item("Zorvex", 5, None, card=card))
    assert best_prices(db, card.id) == []


def test_other_peptides_and_unmatched_lines_are_not_included(db):
    card, other = make_card(db, "Zorvex"), make_card(db, "Quillamine")
    (a,) = vendors(db, "Acme")
    make_list(db, a, date(2026, 9, 1), item("Quillamine", 10, 30, card=other), item("Zorvex", 10, 70))
    assert best_prices(db, card.id) == []


def test_a_list_whose_vendor_was_deleted_still_shows_its_name_without_a_link(db):
    from app.models import PriceList
    card = make_card(db, "Zorvex")
    (a,) = vendors(db, "Acme")
    stock(db, a, 60, card=card)
    db.delete(a)
    db.commit()
    db.expire_all()
    (option,) = best_prices(db, card.id)
    assert option.vendors[0].vendor_name == "Acme" and option.vendors[0].vendor_id is None


def test_a_peptide_with_no_prices_has_no_options(db):
    assert best_prices(db, make_card(db, "Zorvex").id) == []


# ---------------------------------------------------------------- on the page

def sheet(db, name="Zorvex"):
    p = Peptide(name=name, source=PeptideSource.SHEET, half_life_text="About 4 hours")
    db.add(p)
    db.commit()
    return p


def popup_data(text):
    return json.loads(re.search(r'<script type="application/json" id="price-compare-data">(.*?)</script>', text, re.S).group(1))


def test_the_price_range_opens_a_per_vial_popup_with_the_vendors_ranked(client, db):
    p = sheet(db)
    for vendor, price, warehouse in (("Acme", 80, Warehouse.US), ("Borealis", 50, Warehouse.CHINA)):
        stock(db, make_vendor(db, vendor), price, card=p, warehouse=warehouse)
    text = client.get(f"/library/{p.id}").text
    assert 'data-price-compare' in text and '<dialog id="price-compare"' in text
    shown = re.sub("<[^>]+>", "", html.unescape(text)).lower()
    assert "per vial" in shown and "not the kit or box price" in shown
    data = popup_data(text)
    assert [s["label"] for s in data["sizes"]] == ["10mg"] and data["default"] == 0
    assert [v["name"] for v in data["sizes"][0]["vendors"]] == ["Borealis", "Acme"]
    assert data["sizes"][0]["vendors"][0]["per_vial"] == "5.00" and data["sizes"][0]["vendors"][0]["warehouse"] == "China"


def test_the_popup_opens_on_the_size_the_cards_range_shows(client, db):
    p = sheet(db)
    a, b = vendors(db, "Acme", "Borealis")
    make_list(db, a, date(2026, 9, 1), item("Zorvex", 5, 40, card=p), item("Zorvex", 10, 70, card=p))
    make_list(db, b, date(2026, 9, 1), item("Zorvex", 10, 65, card=p))
    data = popup_data(client.get(f"/library/{p.id}").text)
    assert [s["label"] for s in data["sizes"]] == ["5mg", "10mg"] and data["default"] == 1     # 10mg is on two lists


def test_a_card_with_no_prices_has_no_popup(client, db):
    p = sheet(db)
    text = client.get(f"/library/{p.id}").text
    assert "price-compare" not in text and "data-price-compare" not in text


def test_vendor_names_in_the_popup_data_are_safe_to_embed(client, db):
    p = sheet(db)
    stock(db, make_vendor(db, "</script><b>x"), 60, card=p)
    text = client.get(f"/library/{p.id}").text
    assert "</script><b>x" not in text.split('id="price-compare-data">')[1].split("</script>")[0]

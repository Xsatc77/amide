"""The vendor page's Price list section shows the lists imported for the vendor (an import from a group does not attach a file)."""

import html
from datetime import date

from app.models import Warehouse
from price_helpers import item, make_card, make_list, make_vendor


def text(response) -> str:
    return html.unescape(response.text)


def test_an_imported_list_shows_instead_of_no_price_list_on_file(client, db):
    card = make_card(db, "Zorvex")
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 10, 5), item("Zorvex", 10, 55, card=card, code="ZV10"), item("Quillamine X", 5, 20, code="QX5"))
    page = text(client.get(f"/vendors/{acme.id}"))
    assert "No price list on file" not in page
    for expected in ("China warehouse", "10/05/2026", "2 products", "ZV10", "Zorvex", "10 mg", "kit of 10", "$55.00", "$5.50", "QX5"):
        assert expected in page, expected


def test_each_warehouse_shows_its_newest_list_only(client, db):
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 9, 1), item("Oldline", 10, 50, code="OLD1"))
    make_list(db, acme, date(2026, 10, 1), item("Newline", 10, 60, code="NEW1"))
    make_list(db, acme, date(2026, 10, 2), item("Usline", 10, 70, code="US1"), warehouse=Warehouse.US)
    page = text(client.get(f"/vendors/{acme.id}"))
    assert "NEW1" in page and "US1" in page and "OLD1" not in page
    assert "China warehouse" in page and "US warehouse" in page


def test_another_vendors_list_is_not_shown(client, db):
    acme, zephyr = make_vendor(db, "Acme"), make_vendor(db, "Zephyr")
    make_list(db, zephyr, date(2026, 10, 1), item("Zline", 10, 60, code="ZEPH1"))
    assert "ZEPH1" not in text(client.get(f"/vendors/{acme.id}"))


def test_a_vendor_with_no_lists_still_says_none_on_file(client, db):
    acme = make_vendor(db, "Acme")
    assert "No price list on file" in text(client.get(f"/vendors/{acme.id}"))

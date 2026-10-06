import html
import re
from datetime import date

from app.library.price_lists.vendor_view import build_price_history
from app.models import Warehouse
from price_helpers import item, make_card, make_list, make_vendor


def text(response) -> str:
    return html.unescape(response.text)


def panels(page: str) -> dict[str, str]:
    """product label -> that panel's html"""
    options = dict(re.findall(r'<option value="([^"]+)">([^<]+)</option>', page))
    out = {}
    for key, body in re.findall(r'<div class="price-panel" data-product="([^"]+)"[^>]*>(.*?)</div>\s*(?=<div class="price-panel"|<p class="muted small">|</section>)', page, re.S):
        out[options.get(key, key)] = body
    return out


def legend(panel: str) -> dict[str, str]:
    return {label.strip(): color for color, label in re.findall(r'<span class="swatch" style="background: (#\w+)"></span>([^<]+)</li>', panel)}


def test_vendor_page_shows_a_product_dropdown_and_a_line_per_size(client, db):
    card = make_card(db, "Zorvex")
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 8, 1), item("Zorvex", 10, 50, card=card), item("Zorvex", 5, 30, card=card),
              item("Quillamine X", 5, 20))
    make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 55, card=card), item("Zorvex", 5, 33, card=card))
    page = text(client.get(f"/vendors/{acme.id}"))
    assert 'id="price-product-select"' in page
    assert re.findall(r'<option value="[^"]+">([^<]+)</option>', page)[-2:] == ["Quillamine X", "Zorvex"]
    z = panels(page)["Zorvex"]
    assert set(legend(z)) == {"5mg", "10mg"} and z.count("<polyline") == 2
    assert "kit of 10" in z and "per vial" in z and "$5.50 per vial" in z


def test_the_same_size_has_the_same_color_on_every_product(client, db):
    card = make_card(db, "Zorvex")
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 50, card=card), item("Zorvex", 5, 30, card=card),
              item("Quillamine X", 5, 20))
    shown = panels(text(client.get(f"/vendors/{acme.id}")))
    assert legend(shown["Quillamine X"])["5mg"] == legend(shown["Zorvex"])["5mg"]
    assert legend(shown["Zorvex"])["5mg"] != legend(shown["Zorvex"])["10mg"]


def test_a_vendor_with_both_warehouses_gets_a_line_per_warehouse_with_the_usa_dashed(client, db):
    card = make_card(db, "Zorvex")
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 50, card=card))
    make_list(db, acme, date(2026, 9, 2), item("Zorvex", 10, 70, card=card), warehouse=Warehouse.US)
    z = panels(text(client.get(f"/vendors/{acme.id}")))["Zorvex"]
    assert set(legend(z)) == {"10mg · China", "10mg · USA"}
    assert z.count("stroke-dasharray") == 1 and legend(z)["10mg · China"] == legend(z)["10mg · USA"]


def test_a_box_is_described_as_a_box(client, db):
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 9, 1), item("Zorvex Oil", 250, 30, unit="mg/ml", pack=1))
    assert "box of 1" in text(client.get(f"/vendors/{acme.id}"))


def test_a_vendor_without_price_lists_says_so(client, db):
    acme = make_vendor(db, "Acme")
    page = text(client.get(f"/vendors/{acme.id}"))
    assert "No price lists imported for this vendor yet." in page and "price-product-select" not in page
    assert build_price_history(db, acme.id) is None

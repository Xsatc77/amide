import html
import re
from datetime import date

from app.models import Peptide, PeptideSource, PriceList
from price_helpers import item, make_list, make_vendor


def card(db, name, source=PeptideSource.SHEET, **fields):
    p = Peptide(name=name, source=source, **fields)
    db.add(p)
    db.commit()
    return p


def page(client, p) -> str:
    return html.unescape(client.get(f"/library/{p.id}").text)


def facts(text: str) -> str:
    return re.search(r'<dl class="kv kv-tight quick-facts">(.*?)</dl>', text, re.S).group(1)


def three_vendors(db, p):
    for vendor, price in (("Acme", 50), ("Zephyr", 80), ("Borealis", 60)):
        make_list(db, make_vendor(db, vendor), date(2026, 9, 1), item(p.name, 10, price, card=p))


def test_range_sits_at_the_far_end_of_the_half_life_line_of_a_reference_sheet_card(client, db):
    p = card(db, "Zorvex", half_life_text="About 4 hours", route_summary="Subcutaneous")
    three_vendors(db, p)
    row = facts(page(client, p))
    assert row.index("About 4 hours") < row.index("Subcutaneous") < row.index('class="price-range')
    assert "10mg · $5.00 – $8.00 per vial · 3 lists" in row

def test_a_single_price_shows_once(client, db):
    p = card(db, "Zorvex", half_life_text="About 4 hours")
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Zorvex", 10, 50, card=p))
    assert "10mg · $5.00 per vial · 1 list<" in page(client, p)


def test_without_a_half_life_the_range_still_shows_on_that_line(client, db):
    p = card(db, "Zorvex", route_summary="Subcutaneous")
    three_vendors(db, p)
    row = facts(page(client, p))
    assert "Half-life" not in row and "10mg · $5.00 – $8.00 per vial · 3 lists" in row


def test_a_card_with_nothing_else_in_its_facts_still_shows_the_range(client, db):
    p = card(db, "Zorvex")
    three_vendors(db, p)
    assert "per vial · 3 lists" in facts(page(client, p))

def test_no_prices_no_range(client, db):
    p = card(db, "Zorvex", half_life_text="About 4 hours")
    text = page(client, p)
    assert "About 4 hours" in text and "price-range" not in text


def test_a_peptide_card_shows_the_range_beside_its_half_life(client, db):
    # CARD-source rows are shared seed data the suite's cleanup never removes, so this test removes its own
    p = card(db, "Zorvex Cardtest", source=PeptideSource.CARD,
             card_details={"quick_info": {"Half-life": "6 hours", "Routes": "SC"}})
    try:
        three_vendors(db, p)
        row = re.search(r"<dt>Half-life</dt>\s*<dd[^>]*>(.*?)</dd>", page(client, p), re.S).group(1)
        assert "6 hours" in row and "$5.00 – $8.00 per vial" in row
    finally:
        db.query(PriceList).delete()
        db.delete(p)
        db.commit()

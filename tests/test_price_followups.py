import html
from datetime import date

from sqlalchemy import select

from app.library.price_lists.analysis import current_lists, price_range, rematch_items
from app.library.price_lists.importer import resolve_vendor
from app.library.price_lists.vendor_view import build_price_history
from app.models import PriceAlertIgnore, PriceList, Vendor
from price_helpers import item, make_card, make_list, make_vendor


def test_a_re_added_vendor_takes_its_orphaned_lists_back(db):
    acme = make_vendor(db, "Acme")
    old = make_list(db, acme, date(2026, 1, 1), item("Zorvex", 10, 50))
    db.delete(acme)
    db.commit()
    db.refresh(old)
    assert old.vendor_id is None
    vendor, created = resolve_vendor(db, "Acme", date(2026, 6, 1))
    db.commit()
    assert created and old.vendor_id == vendor.id
    new = make_list(db, vendor, date(2026, 6, 1), item("Zorvex", 10, 60))
    assert [p.id for p in current_lists(db)] == [new.id]


def test_adding_a_card_links_items_already_imported(db):
    acme = make_vendor(db, "Acme")
    plist = make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 50))
    assert price_range(db, 0) is None and plist.items[0].peptide_id is None
    card = make_card(db, "Zorvex")
    assert rematch_items(db) == 1
    db.commit()
    assert plist.items[0].peptide_id == card.id
    assert price_range(db, card.id).low == 5.0


def test_the_chart_plots_price_per_vial_so_a_kit_and_a_box_do_not_jump(db):
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 8, 1), item("Zorvex", 10, 120), item("Zorvex", 10, 15, pack=1))
    make_list(db, acme, date(2026, 9, 1), item("Zorvex", 10, 118))
    chart = build_price_history(db, acme.id)["products"][0]["chart"]
    tips = [dot["tip"] for s in chart["series"].values() for dot in s["points"]]
    assert any("$11.80 per vial" in html.unescape(t) for t in tips)
    assert not any("$118.00 per vial" in t for t in tips)


def test_a_pack_with_no_stated_size_is_left_off_the_chart(db):
    acme = make_vendor(db, "Acme")
    make_list(db, acme, date(2026, 8, 1), item("Zorvex", 10, 50, pack=None))
    assert build_price_history(db, acme.id) is None or not build_price_history(db, acme.id)["products"]


def test_a_peptide_with_no_card_still_shows_its_price_range(client, db):
    card = make_card(db, "Zorvex")
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Zorvex", 10, 50, card=card))
    page = html.unescape(client.get(f"/library/{card.id}").text)
    assert "$5.00 per vial" in page


def test_ignoring_twice_is_harmless(client, db):
    for _ in range(2):
        assert client.post("/price-alerts/ignore", data={"name": "Mannitol Peptide"},
                                 follow_redirects=False).status_code == 303
    assert len(db.scalars(select(PriceAlertIgnore)).all()) == 1

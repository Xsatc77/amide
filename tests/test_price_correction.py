"""Price correction (administrator): fix which library card a vendor's product name is matched to, and add the cards that are missing. Invented names only."""

import html
import re
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.library.matching import name_key
from app.library.price_lists import corrections
from app.library.price_lists.importer import store_price_list
from app.library.price_lists.reader import PriceListData
from app.library.price_lists.rows import ParsedRow, Spec
from app.main import app
from app.models import Peptide, PeptideSource, PriceCorrection, PriceListItem
from price_helpers import item, make_card, make_list, make_vendor

URL = "/settings/price-correction"


def page(client, url=URL) -> str:
    return html.unescape(client.get(url).text)


def post(client, url=URL, **fields):
    return client.post(url, data=fields, follow_redirects=False)


def two_vendors(db):
    zorvex, marnitol = make_card(db, "Zorvex"), make_card(db, "Marnitol")
    acme, zephyr = make_vendor(db, "Acme"), make_vendor(db, "Zephyr")
    make_list(db, acme, date(2026, 9, 1), item("Zorvx", 10, 50, card=zorvex), item("Quillamine", 5, 20))
    make_list(db, zephyr, date(2026, 9, 2), item("Zorvx", 10, 55, card=zorvex), item("Marnitol 5", 5, 12, card=marnitol))
    return zorvex, marnitol


def test_only_the_administrator_can_see_or_use_it(client, db):
    two_vendors(db)
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "pricecorrother", "password": "Other1!", "confirm": "Other1!"})
    assert other.get(URL).status_code == 404
    assert other.post(URL, data={}).status_code == 404
    assert other.post(URL + "/add", data={}).status_code == 404


def test_settings_links_to_it_for_the_administrator(client):
    assert f'href="{URL}"' in client.get("/settings").text


def test_each_product_is_listed_once_with_every_vendors_price_and_its_current_match(client, db):
    zorvex, marnitol = two_vendors(db)
    text = page(client)
    assert "Acme" in text and "Zephyr" in text
    groups = {g.name: g for g in corrections.product_groups(db)}
    assert set(groups) == {"Zorvx", "Quillamine", "Marnitol 5"}
    assert len(groups["Zorvx"].lines) == 2 and {line.vendor for line in groups["Zorvx"].lines} == {"Acme", "Zephyr"}
    assert groups["Zorvx"].peptide_id == zorvex.id and groups["Quillamine"].peptide_id is None
    assert [g.name for g in corrections.product_groups(db)][0] == "Quillamine"          # unmatched first


def test_the_dropdown_data_lists_every_peptide(client, db):
    two_vendors(db)
    make_card(db, "Unlisted Card")
    text = page(client)
    for name in ("Zorvex", "Marnitol", "Unlisted Card"):
        assert f'"{name}"' in text


def test_correcting_a_match_relinks_every_line_with_that_name_on_every_list_and_remembers_it(client, db):
    zorvex, marnitol = two_vendors(db)
    key = name_key("Zorvx")
    r = post(client, **{f"match__{key}": f"p{marnitol.id}"})
    assert r.status_code in (200, 303)
    db.expire_all()
    assert {i.peptide_id for i in db.scalars(select(PriceListItem).where(PriceListItem.product_name == "Zorvx"))} == {marnitol.id}
    saved = db.scalar(select(PriceCorrection).where(PriceCorrection.product_key == key))
    assert saved.peptide_id == marnitol.id and saved.product_name == "Zorvx"


def test_a_row_left_as_it_was_stores_nothing(client, db):
    zorvex, marnitol = two_vendors(db)
    post(client, **{f"match__{name_key('Zorvx')}": f"p{zorvex.id}", f"match__{name_key('Quillamine')}": "none"})
    assert db.query(PriceCorrection).count() == 0


def test_no_match_unlinks_and_a_later_automatic_rematch_does_not_undo_it(client, db):
    two_vendors(db)
    key = name_key("Zorvx")
    post(client, **{f"match__{key}": "none"})
    db.expire_all()
    assert all(i.peptide_id is None for i in db.scalars(select(PriceListItem).where(PriceListItem.product_name == "Zorvx")))
    from app.library.price_lists.analysis import rematch_items
    rematch_items(db)
    db.commit()
    assert all(i.peptide_id is None for i in db.scalars(select(PriceListItem).where(PriceListItem.product_name == "Zorvx")))


def test_a_correction_applies_to_lists_imported_later(db):
    marnitol = make_card(db, "Marnitol")
    make_card(db, "Zorvex")
    db.add(PriceCorrection(product_key=name_key("Zorvx"), product_name="Zorvx", peptide_id=marnitol.id))
    db.commit()
    data = PriceListData(rows=[ParsedRow(code="ZX10", name="Zorvx", spec=Spec(10, "mg", 10), pack_price=50.0)])
    store_price_list(db, "Acme Labs - Price List - 2026-09-01.pdf", data, prefix_table={})
    assert db.scalar(select(PriceListItem.peptide_id).where(PriceListItem.product_name == "Zorvx")) == marnitol.id


def test_a_correction_can_be_changed_again(client, db):
    zorvex, marnitol = two_vendors(db)
    key = name_key("Zorvx")
    post(client, **{f"match__{key}": f"p{marnitol.id}"})
    post(client, **{f"match__{key}": f"p{zorvex.id}"})
    assert db.query(PriceCorrection).filter_by(product_key=key).one().peptide_id == zorvex.id


def test_an_unknown_peptide_is_refused_and_nothing_changes(client, db):
    two_vendors(db)
    r = post(client, **{f"match__{name_key('Zorvx')}": "p999999"})
    assert r.status_code == 422
    assert db.query(PriceCorrection).count() == 0


def test_a_product_that_is_not_on_the_lists_is_ignored(client, db):
    zorvex, marnitol = two_vendors(db)
    assert post(client, **{"match__nosuchproduct": f"p{zorvex.id}"}).status_code in (200, 303)
    assert db.query(PriceCorrection).count() == 0


def test_choosing_add_shows_the_products_to_include_after_the_corrections_are_saved(client, db):
    two_vendors(db)
    make_list(db, make_vendor(db, "Borealis"), date(2026, 9, 3), item("Wexamine", 5, 30))
    r = post(client, **{f"match__{name_key('Quillamine')}": "new"})
    assert r.status_code == 200
    text = html.unescape(r.text)
    assert "Quillamine" in text and f'name="add__{name_key("Quillamine")}"' in text
    assert "Wexamine" in text and re.search(rf'name="add__{name_key("Wexamine")}"(?![^>]*checked)', text)    # still unmatched: offered, not ticked


def test_adding_creates_the_card_and_links_every_line_and_remembers_it(client, db):
    two_vendors(db)
    key = name_key("Quillamine")
    r = post(client, URL + "/add", **{f"add__{key}": "1", f"name__{key}": "Quillamine"})
    assert r.status_code == 303
    db.expire_all()
    card = db.scalar(select(Peptide).where(Peptide.name == "Quillamine"))
    assert card is not None and card.source == PeptideSource.CUSTOM
    assert db.scalar(select(PriceListItem.peptide_id).where(PriceListItem.product_name == "Quillamine")) == card.id
    assert db.query(PriceCorrection).filter_by(product_key=key).one().peptide_id == card.id


def test_adding_with_an_edited_name_uses_that_name(client, db):
    two_vendors(db)
    key = name_key("Quillamine")
    post(client, URL + "/add", **{f"add__{key}": "1", f"name__{key}": "Quillamine HCl"})
    assert db.scalar(select(Peptide).where(Peptide.name == "Quillamine HCl")) is not None


def test_adding_a_name_that_already_has_a_card_links_to_that_card(client, db):
    zorvex, marnitol = two_vendors(db)
    key = name_key("Quillamine")
    post(client, URL + "/add", **{f"add__{key}": "1", f"name__{key}": "Marnitol"})
    assert db.query(Peptide).filter(Peptide.name == "Marnitol").count() == 1
    assert db.scalar(select(PriceListItem.peptide_id).where(PriceListItem.product_name == "Quillamine")) == marnitol.id


def test_unticked_products_are_not_added(client, db):
    two_vendors(db)
    key = name_key("Quillamine")
    post(client, URL + "/add", **{f"name__{key}": "Quillamine"})
    assert db.scalar(select(Peptide).where(Peptide.name == "Quillamine")) is None

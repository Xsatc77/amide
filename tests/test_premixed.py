"""A product listed with a strength per mL ("B12 1mg/ml") is premixed: it finds its card, is priced as a size of its own, and needs no BAC water."""

import math
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.library.matching import match_name
from app.library.price_lists.analysis import best_prices
from app.library.price_lists.premixed import premixed_form, strength_mg_per_ml, vial_mg
from app.library.price_lists.vendor_view import build_price_compare
from app.models import DoseUnit, Frequency, Peptide, Protocol, ProtocolItem, Route
from price_helpers import item, make_card, make_list, make_vendor
from types import SimpleNamespace as NS

TODAY = date.today()


@pytest.mark.parametrize("text, expected", [
    ("B12 1mg/ml", 1.0), ("B12 1MG/ML", 1.0), ("B12 1000mcg/ml", 1.0), ("B12 500 mcg / mL", 0.5), ("Zephyr 2.5 mg/mL (10ml)", 2.5),
    ("Zephyr 5mg", None), ("Zephyr 100IU/ml", None), ("Zephyr", None), (None, None), ("", None),
])
def test_the_strength_per_ml_in_a_name(text, expected):
    assert strength_mg_per_ml(text) == expected


def test_only_a_volume_vial_with_a_strength_in_its_name_is_premixed():
    liquid = item("B12 1mg/ml", 10, 58, unit="ml")
    assert premixed_form(liquid) == "premixed:1" and vial_mg(liquid) == 10.0
    assert premixed_form(item("B12", 10, 58, unit="ml")) is None                       # a volume alone says nothing about the strength
    assert premixed_form(item("B12 1mg/ml", 10, 58, unit="mg")) is None               # a weight-sized vial is not a liquid one
    assert vial_mg(item("B12", 10, 58, unit="ml")) is None


def test_a_name_with_a_strength_per_ml_finds_its_card():
    cards = [NS(id=1, name="B-12", aliases="Vitamin B12"), NS(id=2, name="BPC-157", aliases=None)]
    for name in ("B12 1mg/ml", "B12 1000mcg/ml", "Vitamin B12 1mg/ml", "B-12 1 mg / mL"):
        assert [c.name for c in match_name(name, cards).cards] == ["B-12"], name
    assert match_name("BPC-157 5mg", cards) is None                                   # a plain weight in a name is still left alone


def test_premixed_is_a_size_of_its_own_in_the_best_price_list(db):
    card = make_card(db, "Zephyr B-12")
    acme, borealis, quill = make_vendor(db, "Acme"), make_vendor(db, "Borealis"), make_vendor(db, "Quill")
    make_list(db, acme, TODAY, item("Zephyr B-12", 10, 35, card=card))
    make_list(db, borealis, TODAY, item("Zephyr B-12 1mg/ml", 10, 58, unit="ml", card=card))
    make_list(db, quill, TODAY, item("Zephyr B-12", 10, 70, unit="ml", card=card))
    options = best_prices(db, card.id)
    assert [(o.amount, o.unit, o.form) for o in options] == [(10, "mg", None), (10, "ml", None), (10, "ml", "premixed:1")]
    labels = [s["label"] for s in build_price_compare(db, card.id, None)["sizes"]]
    assert labels == ["10mg", "10ml", "10ml premixed (1mg/mL, 10mg a vial)"]


def protocol(uid, peptide_id, dose=250, unit=DoseUnit.MCG, days=40):
    with SessionLocal() as s:
        p = Protocol(name="Course", start_date=TODAY, end_date=TODAY + timedelta(days=days - 1), owner_id=uid)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=peptide_id, dose=dose, dose_unit=unit, frequency=Frequency.DAILY, route=Route.SUBQ))
        s.commit()
        return p.id


def with_supplied(db, card):
    card.normally_supplied_amount, card.normally_supplied_unit = 10, DoseUnit.MG
    db.commit()


def test_the_shop_plan_buys_premixed_when_it_is_cheapest_and_adds_no_bac_water(client, db, me):
    card = make_card(db, "Zephyr B-12")
    with_supplied(db, card)
    acme, borealis = make_vendor(db, "Acme"), make_vendor(db, "Borealis")
    make_list(db, acme, TODAY, item("Zephyr B-12", 10, 200, card=card))                                         # $20 a 10 mg vial of powder
    make_list(db, borealis, TODAY, item("Zephyr B-12 1mg/ml", 10, 120, unit="ml", card=card))                   # $12 a vial of 10 mg, ready to use
    data = client.get(f"/protocols/{protocol(me, card.id)}/shop").json()
    line = data["plan"]["sources"][0]["lines"][0]
    assert data["plan"]["sources"][0]["vendor"] == "Borealis" and line["per_vial"] == pytest.approx(12.0)
    assert line["size_label"] == "10 mL premixed (1 mg/mL)"
    assert data["bac"] is None                                                                                    # nothing to reconstitute


def test_bac_water_is_still_planned_when_the_powder_is_the_choice(client, db, me):
    card = make_card(db, "Zephyr B-12")
    with_supplied(db, card)
    acme, borealis = make_vendor(db, "Acme"), make_vendor(db, "Borealis")
    make_list(db, acme, TODAY, item("Zephyr B-12", 10, 100, card=card))                                         # $10 a vial of powder
    make_list(db, borealis, TODAY, item("Zephyr B-12 1mg/ml", 10, 120, unit="ml", card=card))                   # $12 premixed
    data = client.get(f"/protocols/{protocol(me, card.id)}/shop").json()
    assert data["plan"]["sources"][0]["vendor"] == "Acme" and data["bac"]["ml"] > 0


def test_a_liquid_without_a_strength_is_never_priced_for_the_plan(client, db, me):
    card = make_card(db, "Zephyr B-12")
    borealis = make_vendor(db, "Borealis")
    make_list(db, borealis, TODAY, item("Zephyr B-12", 10, 58, unit="ml", card=card))
    data = client.get(f"/protocols/{protocol(me, card.id)}/shop").json()
    assert data["plan"] is None


def liquid_only(db, me):
    card = make_card(db, "Zephyr B-12")
    with_supplied(db, card)
    borealis = make_vendor(db, "Borealis")
    make_list(db, borealis, TODAY, item("Zephyr B-12", 10, 58, unit="ml", card=card))                         # $5.80 a 10 mL vial, strength not stated
    return card, protocol(me, card.id)


def test_a_liquid_with_no_strength_is_asked_about_not_called_unavailable(client, db, me):
    card, pid = liquid_only(db, me)
    data = client.get(f"/protocols/{pid}/shop").json()
    assert data["plan"] is None
    assert data["needs_strength"] == [{"peptide_id": card.id, "name": "Zephyr B-12", "volume_ml": 10.0, "pack_type": "kit", "pack_size": 10, "pack_price": 58.0}]
    assert not any(u["name"] == "Zephyr B-12" for u in data["unshoppable"])


def test_with_the_strength_typed_the_liquid_is_priced_and_adds_no_bac_water(client, db, me):
    card, pid = liquid_only(db, me)
    data = client.get(f"/protocols/{pid}/shop", params={f"strength_{card.id}": "1"}).json()
    line = data["plan"]["sources"][0]["lines"][0]
    assert line["size_label"] == "10 mL × 1 mg/mL" and line["per_vial"] == pytest.approx(5.8) and line["vials_needed"] == 2
    assert data["bac"] is None and data["needs_strength"] == []


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1001"])
def test_a_bad_strength_is_refused(client, db, me, value):
    card, pid = liquid_only(db, me)
    assert client.get(f"/protocols/{pid}/shop", params={f"strength_{card.id}": value}).status_code == 422


def test_a_strength_is_not_asked_when_a_vendor_states_one(client, db, me):
    card = make_card(db, "Zephyr B-12")
    with_supplied(db, card)
    borealis = make_vendor(db, "Borealis")
    make_list(db, borealis, TODAY, item("Zephyr B-12 1mg/ml", 10, 58, unit="ml", card=card))
    assert client.get(f"/protocols/{protocol(me, card.id)}/shop").json()["needs_strength"] == []

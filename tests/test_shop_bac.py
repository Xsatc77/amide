"""BAC water in the shopping plan: found on the price lists by name, ranked by the user's brand order, added to an order or bought alone."""

import pytest

from app.shopping.bac import BacOffer, is_bac_water, plan_bac, rank_for

SHIP = {"china": 60.0, "us": 30.0}


def offer(vendor="Acme", warehouse="us", product="Bacteriostatic Water", size=30.0, pack=1, price=18.0, rank=1, vendor_id=None):
    return BacOffer(vendor_id=vendor_id if vendor_id is not None else hash(vendor) % 10_000, vendor_name=vendor, warehouse=warehouse, product=product,
                    size_ml=size, pack_size=pack, pack_price=price, rank=rank)


@pytest.mark.parametrize("name", ["Bacteriostatic Water 0.9%", "bac.water", "BAC water", "Bac water (0.9% benzyl alcohol)", "Pfizer Hospira Bacteriostatic Water",
                                  "bac.water（Contains 9% benzyl alcohol）"])
def test_bac_water_is_recognised_by_name(name):
    assert is_bac_water(name)


@pytest.mark.parametrize("name", ["Acetic Acid Water 0.6%", "SterileWater", "water", "Sterile water", "Pfizer Hospira Bacteriostatic Sodium Chloride",
                                  "Bacteriostatic Saline", "Semaglutide", "Acetic Acid water 0.6% 醋酸水"])
def test_other_waters_and_products_are_not_bac_water(name):
    assert not is_bac_water(name)


def test_rank_follows_the_users_ranked_bac_items_by_brand_words():
    ranked = [(1, "Pfizer Hospira BAC Water"), (2, "Genetek BAC Water"), (None, "Supplier Generic BAC Water")]
    assert rank_for("Pfizer Hospira Bacteriostatic Water", ranked) == 1
    assert rank_for("Genetek bac water 30ml", ranked) == 2
    assert rank_for("bac.water", ranked) == 99                      # no brand match: after every ranked brand
    assert rank_for("Lambda Bacteriostatic Water", ranked) == 99


def test_no_offers_means_no_purchase():
    assert plan_bac(9.0, [], [], SHIP) is None


def test_the_ml_needed_gets_a_buffer_and_whole_units_and_packs():
    buy = plan_bac(9.0, [offer(size=3.0, pack=10, price=15.0, warehouse="china")], [], SHIP)
    assert (buy["units"], buy["packs"], buy["cost"]) == (4, 1, 15.0)       # 9 mL + 5% = 9.45 mL: four 3 mL vials, one kit of 10


def test_the_cheaper_pack_for_the_need_wins_within_a_vendor():
    single, case = offer(size=30.0, pack=1, price=18.0), offer(size=30.0, pack=25, price=435.0)
    buy = plan_bac(9.0, [case, single], [], SHIP)
    assert (buy["packs"], buy["cost"]) == (1, 18.0) and buy["pack_label"] == "single"


def test_water_from_a_vendor_already_in_the_plan_adds_no_shipping():
    buy = plan_bac(9.0, [offer("Acme", "china", price=5.0, size=30.0, vendor_id=1)], [(1, "china")], SHIP)
    assert buy["mode"] == "add" and buy["shipping"] == 0.0 and buy["extra"] == pytest.approx(5.0)


def test_water_from_another_vendor_is_a_separate_order_with_its_own_shipping():
    buy = plan_bac(9.0, [offer("Sterility", "us", price=18.0, vendor_id=2)], [(1, "china")], SHIP)
    assert buy["mode"] == "separate" and buy["shipping"] == 30.0 and buy["extra"] == pytest.approx(48.0)


def test_a_vendor_in_the_plan_is_matched_by_warehouse_too():
    buy = plan_bac(9.0, [offer("Acme", "us", price=18.0, vendor_id=1)], [(1, "china")], SHIP)
    assert buy["mode"] == "separate"                                   # Acme's US warehouse is a different shipment


def test_an_unranked_vendor_water_is_never_offered_even_when_cheaper():
    brand = offer("Sterility", "us", "Pfizer Hospira Bacteriostatic Water", price=18.0, rank=1, vendor_id=2)
    cheap = offer("Acme", "china", "bac.water", size=3.0, pack=10, price=12.0, rank=99, vendor_id=1)
    buy = plan_bac(9.0, [cheap, brand], [(1, "china")], SHIP)
    assert buy["vendor"] == "Sterility" and buy["rank"] == 1 and buy["extra"] == pytest.approx(48.0)
    assert plan_bac(9.0, [cheap], [(1, "china")], SHIP) is None             # only ranked brands are bought; the rest is not trusted


def test_a_better_ranked_brand_beats_a_cheaper_lower_ranked_one():
    first = offer("Far", "us", "Pfizer Hospira Bacteriostatic Water", price=18.0, rank=1, vendor_id=2)
    second = offer("Near", "china", "Genetek Bac Water", price=5.0, rank=2, vendor_id=1)
    assert plan_bac(9.0, [second, first], [(1, "china")], SHIP)["vendor"] == "Far"


def test_among_the_same_rank_the_lowest_extra_cost_wins():
    a = offer("Far", "us", price=18.0, rank=1, vendor_id=2)
    b = offer("Near", "china", price=20.0, rank=1, vendor_id=1)
    buy = plan_bac(9.0, [a, b], [(1, "china")], SHIP)
    assert buy["vendor"] == "Near" and buy["extra"] == pytest.approx(20.0)         # no shipping beats $18 + $30


def test_a_minimum_number_of_bottles_overrides_the_volume_when_the_course_outlasts_one_bottle():
    buy = plan_bac(9.0, [offer(size=30.0, pack=1, price=18.0)], [], SHIP, min_units=3)
    assert (buy["units"], buy["packs"], buy["cost"]) == (3, 3, 54.0)
    assert plan_bac(9.0, [offer(size=30.0, pack=1, price=18.0)], [], SHIP, min_units=1)["units"] == 1

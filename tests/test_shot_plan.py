"""The library card's vial-costs popup: a shot size picks the best vial, counts its shots, and charges waste to the price. Invented vendors only."""

from datetime import date

from app.library.price_lists.shots import shot_plan
from price_helpers import item, make_card, make_list, make_vendor


def sizes():
    return [{"mg": 5, "iu": None, "vendors": [{"price": 10.0}, {"price": 12.0}]},
            {"mg": 10, "iu": None, "vendors": [{"price": 16.0}]},
            {"mg": 30, "iu": None, "vendors": [{"price": 40.0}]}]


def test_a_daily_shot_fills_a_small_vial_and_wastes_most_of_a_big_one_and_waste_raises_the_price():
    plan = shot_plan(sizes(), 250, "mcg", per_week=7)
    small, middle, big = plan["sizes"]
    assert (small["shots"], small["used"], small["expires"], small["per_shot"]) == (20, 20, False, [0.5, 0.6])
    assert (middle["shots"], middle["used"], middle["expires"], middle["wasted"], middle["wasted_pct"]) == (40, 30, True, 2500, 25)
    assert middle["per_shot"] == [0.53]                                  # 16 / 30 used, not 16 / 40: the wasted shots make each used one dearer
    assert (big["used"], big["wasted"], big["wasted_pct"], big["per_shot"]) == (30, 22500, 75, [1.33])
    assert plan["best"] == 0


def test_a_weekly_shot_wastes_what_four_shots_cannot_use():
    plan = shot_plan(sizes(), 1, "mg", per_week=1)                        # 4 shots fit in 30 days
    small, middle, big = plan["sizes"]
    assert (small["shots"], small["used"], small["expires"], small["wasted"]) == (5, 4, True, 1)
    assert (middle["used"], middle["wasted"]) == (4, 6) and plan["best"] == 0
    assert middle["per_shot"] == [4.0] and small["per_shot"] == [2.5, 3.0]


def test_a_shot_larger_than_the_vial_and_an_unmatched_unit_are_reported_not_priced():
    assert shot_plan(sizes(), 6, "mg")["sizes"][0] == {"shots": 0, "used": 0, "expires": False, "wasted": 0, "wasted_pct": 0, "per_shot": []}
    iu = shot_plan(sizes(), 5, "IU")
    assert all(s["shots"] is None for s in iu["sizes"]) and iu["best"] is None
    mixed = shot_plan([{"mg": None, "iu": 36, "vendors": [{"price": 30.0}]}], 2, "IU", per_week=7)
    assert mixed["sizes"][0]["shots"] == 18 and mixed["best"] == 0


def test_the_popup_has_the_shot_fields_above_the_vial_size_and_the_endpoint_plans_the_card(client, db):
    card = make_card(db, "Zorvex")
    a = make_vendor(db, "Acme")
    make_list(db, a, date(2026, 9, 1), item("Zorvex", 5, 40, card=card, pack=4), item("Zorvex", 10, 90, card=card, pack=5))
    page = client.get(f"/library/{card.id}").text
    assert page.index("data-shot-amount") < page.index("data-price-size") and 'data-peptide-id="' in page
    r = client.get(f"/library/{card.id}/shot-plan", params={"amount": 250, "unit": "mcg"})
    assert r.status_code == 200
    body = r.json()
    assert body["best"] == 0 and body["sizes"][0]["shots"] == 20 and body["sizes"][1]["expires"] is True and body["window"] == 30
    assert client.get(f"/library/{card.id}/shot-plan", params={"amount": 250, "unit": "kg"}).status_code == 422
    assert client.get(f"/library/{card.id}/shot-plan", params={"amount": 0, "unit": "mg"}).status_code == 422
    assert client.get("/library/99999999/shot-plan", params={"amount": 1, "unit": "mg"}).status_code == 404

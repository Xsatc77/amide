"""The Recommend buttons on the Food tab: foods that fill what is left of protein, carbs or fiber with the fewest carbs."""

from types import SimpleNamespace

from app.food.recommend import recommend


def food(i, name, cal, p, c, f=0.0):
    return SimpleNamespace(id=i, name=name, serving="1 serving", calories=cal, protein_g=p, carb_g=c, fat_g=0, fiber_g=f)


def test_among_foods_close_to_what_is_left_the_lowest_carb_wins():
    foods = [food(1, "Shake", 200, 30, 25), food(2, "Chicken", 200, 30, 0), food(3, "Yogurt", 150, 30, 8)]
    assert [r["name"] for r in recommend(foods, "protein", 30)] == ["Chicken", "Yogurt", "Shake"]


def test_a_larger_portion_is_used_when_it_gets_closer():
    r = recommend([food(1, "Egg", 70, 6, 0.5)], "protein", 12)
    assert r[0]["servings"] == 2 and r[0]["protein_g"] == 12


def test_foods_over_the_calories_left_are_left_out_and_nothing_is_left_means_nothing_to_suggest():
    foods = [food(1, "Steak", 700, 50, 0), food(2, "Tuna", 120, 26, 0)]
    assert [r["name"] for r in recommend(foods, "protein", 50, calories_left=300)] == ["Tuna"]
    assert recommend(foods, "protein", 0) == [] and recommend(foods, "protein", -5) == []


def test_when_nothing_is_close_the_closest_come_first_and_foods_without_the_nutrient_are_skipped():
    foods = [food(1, "Water", 0, 0, 0), food(2, "Nuts", 170, 6, 6, 3), food(3, "Bran", 90, 3, 20, 14)]
    assert [r["name"] for r in recommend(foods, "fiber", 40)] == ["Bran", "Nuts"]
    assert recommend(foods, "fiber", 40)[0]["servings"] == 3


def test_the_route_suggests_from_starter_foods_and_asks_for_a_profile_first(client, db):
    assert client.get("/food/recommend", params={"nutrient": "sugar"}).status_code == 422
    r = client.get("/food/recommend", params={"nutrient": "protein"})
    assert r.status_code == 200 and r.json()["status"] in ("ok", "missing_profile", "missing_weight")


def test_with_a_profile_the_route_suggests_and_the_tab_has_the_buttons(client, db, me):
    from test_food_page import page, profile, reset_profile
    from app.models import Food
    profile(db, me)
    db.add(Food(owner_id=me, source="mine", name="Recommend test chicken", serving="100 g", calories=165, protein_g=31, carb_g=0, fat_g=3.6, fiber_g=0))
    db.commit()
    try:
        body = client.get("/food/recommend", params={"nutrient": "protein"}).json()
        assert body["status"] == "ok" and body["remaining"] > 0 and 1 <= len(body["foods"]) <= 5
        assert all(f["protein_g"] > 0 for f in body["foods"])
        html_ = page(client)
        assert all(f'data-food-recommend="{k}"' in html_ for k in ("protein", "carb", "fiber")) and 'data-food-recommend="fat"' not in html_
    finally:
        db.query(Food).filter(Food.name == "Recommend test chicken").delete()
        db.commit()
        reset_profile(db, me)

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Food, FoodLog


def mine(me, **kw):
    base = dict(owner_id=me, source="mine", name="Test oats", serving="1 cup", calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4)
    return Food(**{**base, **kw})


def test_a_food_and_a_log_round_trip(db, me):
    food = mine(me)
    db.add(food)
    db.commit()
    db.add(FoodLog(owner_id=me, eaten_on=date(2026, 10, 6), meal="breakfast", food_id=food.id, name=food.name,
                   serving=food.serving, servings=2, calories=300, protein_g=10, carb_g=54, fat_g=6, fiber_g=8))
    db.commit()
    log = db.query(FoodLog).one()
    assert (log.name, log.servings, log.calories, log.created_at is not None) == ("Test oats", 2, 300, True)


def test_the_database_refuses_bad_rows(db, me):
    for bad in (mine(me, calories=-1), mine(me, source="other"), mine(me, owner_id=None),
                Food(source="starter", owner_id=me, name="x", serving="y", calories=1, protein_g=0, carb_g=0, fat_g=0, fiber_g=0)):
        db.add(bad)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_a_person_cannot_have_the_same_food_and_serving_twice_and_starter_rows_are_unique_too(db, me):
    db.add(mine(me))
    db.commit()
    db.add(mine(me))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    starter = dict(source="starter", owner_id=None, name="Test egg", serving="1 large", calories=70, protein_g=6, carb_g=0.4, fat_g=5, fiber_g=0)
    db.add(Food(**starter))
    db.commit()
    db.add(Food(**starter))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_log_with_a_bad_meal_or_servings_is_refused(db, me):
    base = dict(owner_id=me, eaten_on=date(2026, 10, 6), name="x", serving="y", calories=1, protein_g=0, carb_g=0, fat_g=0, fiber_g=0)
    for extra in ({"meal": "brunch", "servings": 1}, {"meal": "lunch", "servings": 0}, {"meal": "lunch", "servings": 51}):
        db.add(FoodLog(**base, **extra))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


import json

from app.food.foods import LIMITS, copy_to_mine, load_starter, own_food, parse_food, search
from app.models import User
from photo_helpers import other_client


def good(**kw):
    return {"name": "Greek yogurt", "serving": "3/4 cup", "calories": "100", "protein_g": "17", "carb_g": "6", "fat_g": "0.7", "fiber_g": "0", **kw}


def test_a_valid_food_parses_to_numbers():
    values, errors = parse_food(good(serving_g="170"))
    assert errors == {} and values["calories"] == 100.0 and values["serving_g"] == 170.0 and values["name"] == "Greek yogurt"


@pytest.mark.parametrize("field, bad", [("name", ""), ("name", "x" * 121), ("serving", ""), ("serving", "y" * 61), ("calories", "abc"),
                                        ("calories", "-1"), ("calories", "5000.1"), ("protein_g", "500.1"), ("fiber_g", "nan"),
                                        ("fat_g", "inf"), ("carb_g", "")])
def test_bad_input_is_rejected_per_field(field, bad):
    values, errors = parse_food(good(**{field: bad}))
    assert field in errors


def test_the_limits_themselves_are_allowed():
    _, errors = parse_food(good(name="x" * 120, serving="y" * 60, calories=str(LIMITS["calories"]), protein_g="500"))
    assert errors == {}


def seed(db, me):
    db.add_all([
        Food(source="starter", owner_id=None, name="Testegg, whole, cooked", serving="1 large", calories=72, protein_g=6.3, carb_g=0.4, fat_g=4.8, fiber_g=0),
        Food(source="starter", owner_id=None, name="Testchicken breast, cooked", serving="3 oz", calories=140, protein_g=26, carb_g=0, fat_g=3, fiber_g=0),
        Food(owner_id=me, source="mine", name="Testegg white wrap", serving="1 wrap", calories=120, protein_g=12, carb_g=14, fat_g=2, fiber_g=3),
    ])
    db.commit()


def test_search_finds_own_and_starter_foods_by_every_word_own_first(client, db, me):
    seed(db, me)
    assert [f.name for f in search(db, me, "testegg")] == ["Testegg white wrap", "Testegg, whole, cooked"]
    assert [f.name for f in search(db, me, "TESTCHICKEN cooked")] == ["Testchicken breast, cooked"]
    assert search(db, me, "testegg quinoa") == []


def test_search_never_returns_another_persons_foods(client, db, me):
    seed(db, me)
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        assert [f.name for f in search(db, other_id, "testegg")] == ["Testegg, whole, cooked"]


def test_an_empty_search_lists_the_persons_own_foods(client, db, me):
    seed(db, me)
    assert [f.name for f in search(db, me, "")] == ["Testegg white wrap"]


def test_search_treats_like_wildcards_as_plain_text(client, db, me):
    seed(db, me)
    assert search(db, me, "%") == [] and search(db, me, "_") == [] and search(db, me, "% _ %") == []


def test_own_food_refuses_other_peoples_and_starter_foods(client, db, me):
    seed(db, me)
    starter = db.query(Food).filter_by(source="starter", name="Testegg, whole, cooked").one()
    mine_food = db.query(Food).filter_by(source="mine").one()
    assert own_food(db, mine_food.id, me).id == mine_food.id
    with pytest.raises(LookupError):
        own_food(db, starter.id, me)
    with pytest.raises(LookupError):
        own_food(db, mine_food.id, me + 999)
    with pytest.raises(LookupError):
        own_food(db, 999999, me)


def test_copying_a_starter_food_makes_a_personal_copy_once(client, db, me):
    seed(db, me)
    starter = db.query(Food).filter_by(source="starter", name="Testegg, whole, cooked").one()
    first = copy_to_mine(db, me, starter)
    assert (first.source, first.owner_id, first.calories) == ("mine", me, 72)
    assert copy_to_mine(db, me, starter).id == first.id


def test_the_starter_loader_adds_updates_and_is_idempotent_and_never_deletes(db, tmp_path):
    path = tmp_path / "starter.json"
    row = {"name": "Test rice", "serving": "1 cup cooked", "serving_g": 158, "calories": 205, "protein_g": 4.3, "carb_g": 45, "fat_g": 0.4, "fiber_g": 0.6}
    path.write_text(json.dumps([row]), encoding="utf-8")
    assert load_starter(db, path) == {"added": 1, "updated": 0}
    assert load_starter(db, path) == {"added": 0, "updated": 0}
    path.write_text(json.dumps([{**row, "calories": 210}]), encoding="utf-8")
    assert load_starter(db, path) == {"added": 0, "updated": 1}
    path.write_text(json.dumps([]), encoding="utf-8")
    load_starter(db, path)
    assert db.query(Food).filter_by(name="Test rice").one().calories == 210
    db.query(Food).filter_by(name="Test rice").delete()
    db.commit()


def test_the_starter_loader_ignores_a_malformed_row_and_keeps_the_rest(db, tmp_path):
    path = tmp_path / "starter.json"
    ok = {"name": "Test beans", "serving": "1/2 cup", "calories": 110, "protein_g": 7, "carb_g": 20, "fat_g": 0.5, "fiber_g": 7}
    path.write_text(json.dumps([{"name": "Broken"}, ok]), encoding="utf-8")
    assert load_starter(db, path)["added"] == 1
    db.query(Food).filter_by(name="Test beans").delete()
    db.commit()

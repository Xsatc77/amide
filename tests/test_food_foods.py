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

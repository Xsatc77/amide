import html
from datetime import date, timedelta

import pytest

from app.food.logs import parse_servings
from app.models import Food, FoodLog, User
from photo_helpers import other_client

TODAY = date.today().isoformat()


def mine(db, me, **kw):
    food = Food(**{**dict(owner_id=me, source="mine", name="Test oats", serving="1 cup", calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4), **kw})
    db.add(food)
    db.commit()
    return food


def starter(db, **kw):
    food = Food(**{**dict(owner_id=None, source="starter", name="Test starter egg", serving="1 large", calories=72, protein_g=6.3, carb_g=0.4, fat_g=4.8, fiber_g=0), **kw})
    db.add(food)
    db.commit()
    return food


def post_log(client, **fields):
    data = {"date": TODAY, "meal": "breakfast", "mode": "existing", "servings": "1", **fields}
    return client.post("/food/log", data=data, follow_redirects=False)


def logs(db):
    db.expire_all()
    return db.query(FoodLog).order_by(FoodLog.id).all()


@pytest.mark.parametrize("raw, ok", [("1", True), ("0.1", True), ("50", True), (" 2.5 ", True), ("0", False), ("-1", False), ("50.1", False),
                                     ("abc", False), ("", False), ("nan", False), ("inf", False)])
def test_servings_limits(raw, ok):
    value, error = parse_servings(raw)
    assert (error is None) == ok and (value is not None) == ok


def test_logging_an_existing_food_stores_a_scaled_snapshot(client, db, me):
    food = mine(db, me)
    r = post_log(client, food_id=food.id, servings="2")
    assert r.status_code == 303 and r.headers["location"] == f"/measurements?tab=food&date={TODAY}#meal-breakfast"
    (log,) = logs(db)
    assert (log.name, log.serving, log.servings, log.calories, log.protein_g, log.carb_g, log.fat_g, log.fiber_g, log.food_id) == (
        "Test oats", "1 cup", 2, 300, 10, 54, 6, 8, food.id)


def test_a_starter_food_can_be_logged(client, db, me):
    egg = starter(db)
    assert post_log(client, food_id=egg.id).status_code == 303 and logs(db)[0].name == "Test starter egg"


def test_another_persons_food_cannot_be_logged(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        theirs = mine(db, other_id, name="Their private food")
        r = post_log(client, food_id=theirs.id)
        assert r.status_code == 404 and logs(db) == []


def test_bad_servings_date_or_meal_are_refused_and_nothing_is_saved(client, db, me):
    food = mine(db, me)
    for fields in ({"servings": "0"}, {"servings": "51"}, {"meal": "brunch"}, {"date": "garbage"}, {"date": "1999-01-01"}):
        r = post_log(client, food_id=food.id, **fields)
        assert r.status_code == 422, fields
    assert logs(db) == []


def test_an_error_page_shows_the_message(client, db, me):
    food = mine(db, me)
    r = post_log(client, food_id=food.id, servings="0")
    assert "servings" in html.unescape(r.text).lower()


def test_create_mode_saves_a_my_food_once_and_logs_it(client, db, me):
    fields = dict(mode="create", name="Cottage cheese", serving="1/2 cup", calories="90", protein_g="12", carb_g="5", fat_g="2.5", fiber_g="0")
    assert post_log(client, **fields).status_code == 303
    assert post_log(client, servings="2", **fields).status_code == 303
    assert db.query(Food).filter_by(owner_id=me, name="Cottage cheese").count() == 1
    assert [l.calories for l in logs(db)] == [90, 180]


def test_create_mode_validates_the_food(client, db, me):
    r = post_log(client, mode="create", name="", serving="1 cup", calories="-5", protein_g="1", carb_g="1", fat_g="1", fiber_g="1")
    assert r.status_code == 422 and db.query(Food).filter_by(owner_id=me).count() == 0 and logs(db) == []


def test_quick_add_logs_a_one_off_without_saving_a_food(client, db, me):
    r = post_log(client, mode="quick", name="Restaurant burger", serving="1 burger", calories="650", protein_g="30", carb_g="45", fat_g="35", fiber_g="2")
    assert r.status_code == 303
    (log,) = logs(db)
    assert (log.name, log.calories, log.food_id) == ("Restaurant burger", 650, None)
    assert db.query(Food).filter_by(owner_id=me).count() == 0


def test_editing_servings_rescales_from_the_entry_not_the_food(client, db, me):
    food = mine(db, me)
    post_log(client, food_id=food.id, servings="1")
    food.calories = 999                                                    # the food changes after it was logged
    db.commit()
    (log,) = logs(db)
    r = client.post(f"/food/log/{log.id}/edit", data={"servings": "3", "date": TODAY}, follow_redirects=False)
    assert r.status_code == 303
    (log,) = logs(db)
    assert (log.servings, log.calories, log.protein_g) == (3, 450, 15)


def test_editing_can_move_an_entry_to_another_meal(client, db, me):
    food = mine(db, me)
    post_log(client, food_id=food.id)
    (log,) = logs(db)
    client.post(f"/food/log/{log.id}/edit", data={"meal": "dinner", "servings": "1", "date": TODAY})
    assert logs(db)[0].meal == "dinner"


def test_deleting_an_entry(client, db, me):
    food = mine(db, me)
    post_log(client, food_id=food.id)
    (log,) = logs(db)
    assert client.post(f"/food/log/{log.id}/delete", data={"date": TODAY}, follow_redirects=False).status_code == 303
    assert logs(db) == []


def test_other_peoples_entries_are_404_for_edit_and_delete(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        db.add(FoodLog(owner_id=other_id, eaten_on=date.today(), meal="lunch", name="x", serving="y", servings=1, calories=1, protein_g=0, carb_g=0, fat_g=0, fiber_g=0))
        db.commit()
        log_id = logs(db)[0].id
        assert client.post(f"/food/log/{log_id}/edit", data={"servings": "2"}).status_code == 404
        assert client.post(f"/food/log/{log_id}/delete").status_code == 404
        assert logs(db)[0].servings == 1


def test_changing_or_deleting_a_food_never_changes_past_logs(client, db, me):
    food = mine(db, me)
    post_log(client, food_id=food.id, servings="2")
    client.post(f"/food/foods/{food.id}/edit", data=dict(name="Renamed", serving="1 cup", calories="1", protein_g="0", carb_g="0", fat_g="0", fiber_g="0"))
    assert (logs(db)[0].name, logs(db)[0].calories) == ("Test oats", 300)
    client.post(f"/food/foods/{food.id}/delete")
    (log,) = logs(db)
    assert (log.name, log.calories, log.food_id) == ("Test oats", 300, None)


def test_search_returns_json_for_own_and_starter_foods_only(client, db, me):
    mine(db, me, name="Testegg white wrap")
    starter(db, name="Testegg, whole, cooked")
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        mine(db, other_id, name="Testegg private to them")
    rows = client.get("/food/search?q=testegg").json()
    assert [r["name"] for r in rows] == ["Testegg white wrap", "Testegg, whole, cooked"]
    assert rows[0]["starter"] is False and rows[1]["starter"] is True and {"id", "serving", "calories", "protein_g", "carb_g", "fat_g", "fiber_g"} <= set(rows[0])


def test_my_foods_can_be_edited_and_deleted_but_starter_foods_cannot(client, db, me):
    food, egg = mine(db, me), starter(db)
    food_id, egg_id = food.id, egg.id
    assert client.post(f"/food/foods/{food.id}/edit", data=dict(name="Edited", serving="1 cup", calories="10", protein_g="1", carb_g="1", fat_g="1", fiber_g="1")).status_code in (200, 303)
    db.expire_all()
    assert db.get(Food, food_id).name == "Edited"
    assert client.post(f"/food/foods/{egg_id}/edit", data=dict(name="Hacked", serving="1", calories="1", protein_g="0", carb_g="0", fat_g="0", fiber_g="0")).status_code == 404
    assert client.post(f"/food/foods/{egg_id}/delete").status_code == 404
    db.expire_all()
    assert db.get(Food, egg_id).name == "Test starter egg"
    assert client.post(f"/food/foods/{food_id}/delete").status_code in (200, 303)
    db.expire_all()
    assert db.get(Food, food_id) is None


def test_another_persons_food_cannot_be_edited_or_deleted(client, db, me):
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        theirs = mine(db, other_id, name="Theirs")
        assert client.post(f"/food/foods/{theirs.id}/delete").status_code == 404
        assert client.post(f"/food/foods/{theirs.id}/edit", data=dict(name="x", serving="y", calories="1", protein_g="0", carb_g="0", fat_g="0", fiber_g="0")).status_code == 404


def test_copying_a_starter_food_to_my_foods(client, db, me):
    egg = starter(db)
    r = client.post(f"/food/foods/{egg.id}/copy")
    assert r.status_code == 200 and r.json()["name"] == "Test starter egg"
    assert db.query(Food).filter_by(owner_id=me, name="Test starter egg").count() == 1
    client.post(f"/food/foods/{egg.id}/copy")
    assert db.query(Food).filter_by(owner_id=me, name="Test starter egg").count() == 1


def test_the_page_shows_edit_delete_add_and_my_foods(client, db, me):
    food = mine(db, me, name="Pantry oats")
    post_log(client, food_id=food.id)
    text = html.unescape(client.get("/measurements?tab=food").text)
    assert "data-food-add" in text and 'action="/food/log/' in text and "Pantry oats" in text and "My foods" in text

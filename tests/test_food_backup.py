import json
from datetime import date

import pytest

from app.backup import load as loader
from app.backup.archive import read_archive
from app.backup.container import BackupError
from app.backup.export import build_archive
from app.models import Food, FoodLog, User
from photo_helpers import other_client


def export(db, me, kind="backup"):
    return read_archive(build_archive(db, kind=kind, uid=me, creator="Tester", keys=["food"]), max_bytes=50_000_000)


def run(db, me, archive, plan):
    return loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan=plan)


def seed(db, me):
    mine = Food(owner_id=me, source="mine", name="Test oats", serving="1 cup", calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4)
    egg = Food(owner_id=None, source="starter", name="Test starter egg", serving="1 large", calories=72, protein_g=6.3, carb_g=0.4, fat_g=4.8, fiber_g=0)
    db.add_all([mine, egg])
    db.commit()
    for food, meal in ((mine, "breakfast"), (egg, "lunch")):
        db.add(FoodLog(owner_id=me, eaten_on=date(2026, 10, 6), meal=meal, food_id=food.id, name=food.name, serving=food.serving, servings=2,
                       calories=food.calories * 2, protein_g=food.protein_g * 2, carb_g=food.carb_g * 2, fat_g=food.fat_g * 2, fiber_g=food.fiber_g * 2))
    db.commit()
    return mine, egg


def wipe(db, me):
    db.query(FoodLog).filter_by(owner_id=me).delete()
    db.query(Food).filter_by(owner_id=me).delete()
    db.commit()


def test_a_backup_carries_own_foods_and_logs_but_not_starter_foods(client, db, me):
    seed(db, me)
    archive = export(db, me)
    name = next(n for n in archive.names() if n.startswith(("sections/", "persons/")) and n.endswith("food.json"))
    tables = json.loads(archive.read(name))["tables"]
    assert [row["name"] for row in tables["foods"]] == ["Test oats"]                    # the starter food is not in the file
    assert sorted(row["name"] for row in tables["food_logs"]) == ["Test oats", "Test starter egg"]   # but its log (a snapshot) is


def test_food_can_never_be_in_a_share_file(client, db, me):
    seed(db, me)
    with pytest.raises(BackupError, match="cannot be put in a share file"):
        export(db, me, kind="share")


def test_loading_food_into_an_empty_account_restores_logs_and_relinks_starter_foods(client, db, me):
    mine, egg = seed(db, me)
    egg_id = egg.id
    archive = export(db, me)
    wipe(db, me)
    run(db, me, archive, {"food": loader.ADD})
    db.expire_all()
    logs = {l.name: l for l in db.query(FoodLog).filter_by(owner_id=me)}
    assert set(logs) == {"Test oats", "Test starter egg"}
    assert logs["Test oats"].food_id is not None and logs["Test oats"].food_id == db.query(Food).filter_by(owner_id=me).one().id
    assert logs["Test starter egg"].food_id == egg_id            # found again by name and serving
    assert logs["Test oats"].calories == 300


def test_a_log_whose_starter_food_is_gone_keeps_its_snapshot(client, db, me):
    mine, egg = seed(db, me)
    archive = export(db, me)
    wipe(db, me)
    db.delete(db.get(Food, egg.id))
    db.commit()
    run(db, me, archive, {"food": loader.ADD})
    db.expire_all()
    log = db.query(FoodLog).filter_by(owner_id=me, name="Test starter egg").one()
    assert log.food_id is None and log.calories == 144


def test_replace_swaps_the_persons_food_data_and_leaves_starter_foods(client, db, me):
    seed(db, me)
    archive = export(db, me)
    db.add(FoodLog(owner_id=me, eaten_on=date(2026, 10, 7), meal="dinner", name="Extra", serving="x", servings=1, calories=1, protein_g=0, carb_g=0, fat_g=0, fiber_g=0))
    db.commit()
    run(db, me, archive, {"food": loader.REPLACE})
    db.expire_all()
    assert db.query(FoodLog).filter_by(owner_id=me).count() == 2 and db.query(Food).filter_by(name="Test starter egg").count() == 1


def test_food_loads_for_the_loader_never_for_the_name_in_the_file(client, db, me):
    seed(db, me)
    archive = export(db, me)
    with other_client() as other:
        other_id = db.query(User).filter_by(username_key="photoother").one().id
        loader.load(db, archive, uid=other_id, username_key="photoother", is_admin=False, plan={"food": loader.ADD})
        db.expire_all()
        assert db.query(FoodLog).filter_by(owner_id=other_id).count() == 2 and db.query(Food).filter_by(owner_id=other_id).count() == 1


def test_deleting_an_account_removes_its_food_but_never_starter_foods(client, db, me):
    with other_client("foodgone"):
        gone = db.query(User).filter_by(username_key="foodgone").one()
        gone_id, gone_name = gone.id, gone.username
        seed(db, gone_id)
        r = client.post(f"/settings/admin/users/{gone_id}/delete", data={"username": gone_name}, follow_redirects=False)
        assert r.status_code == 303
    db.expire_all()
    assert db.query(Food).filter_by(owner_id=gone_id).count() == 0 and db.query(FoodLog).filter_by(owner_id=gone_id).count() == 0
    assert db.query(Food).filter_by(source="starter", name="Test starter egg").count() == 1


def test_the_backup_page_offers_food(client, db, me):
    assert "Food" in client.get("/backup").text

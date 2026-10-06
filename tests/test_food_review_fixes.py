"""Fixes from the independent review of the food tracking feature."""

import json
from datetime import date

import pytest
from sqlalchemy import text

from app.backup import load as loader
from app.backup import restore
from app.backup.archive import read_archive, write_archive
from app.backup.export import build_archive
from app.models import Food, FoodLog
from test_backup_restore import PASS, scratch, whole  # noqa: F401  (scratch is a fixture)

TODAY = date.today().isoformat()


def rewrite(archive, entries):
    manifest = {k: v for k, v in archive.manifest.items() if k not in ("format", "entries")}
    return read_archive(write_archive(manifest, entries), max_bytes=50_000_000)


def seed_foods(s, admin):
    starter = Food(owner_id=None, source="starter", name="Review starter egg", serving="1 large", calories=72, protein_g=6.3, carb_g=0.4, fat_g=4.8, fiber_g=0)
    mine = Food(owner_id=admin, source="mine", name="Review oats", serving="1 cup", calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4)
    s.add_all([starter, mine])
    s.commit()
    for food in (starter, mine):
        s.add(FoodLog(owner_id=admin, eaten_on=date(2026, 10, 6), meal="lunch", food_id=food.id, name=food.name, serving=food.serving, servings=2,
                      calories=food.calories * 2, protein_g=food.protein_g * 2, carb_g=food.carb_g * 2, fat_g=food.fat_g * 2, fiber_g=food.fiber_g * 2))
    s.commit()
    return starter.id, mine.id


def links(s, admin):
    rows = s.execute(text("SELECT l.name, l.food_id, f.owner_id, f.name FROM food_logs l LEFT JOIN foods f ON f.id = l.food_id "
                          "WHERE l.owner_id = :u ORDER BY l.name"), {"u": admin}).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


# ---------------------------------------------------------------- Critical: restore everything with logged starter foods

def test_restore_everything_keeps_the_starter_foods_and_relinks_their_logs(scratch):
    s, admin, _ = scratch
    starter_id, _mine = seed_foods(s, admin)
    archive = whole(s, admin)
    report = restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert s.execute(text("SELECT COUNT(*) FROM foods WHERE owner_id IS NULL")).scalar() == 1              # starter foods were not wiped
    found = links(s, admin)
    assert found["Review starter egg"] == (starter_id, None, "Review starter egg")
    own_id, owner, name = found["Review oats"]
    assert owner == admin and name == "Review oats"
    assert report.rows["food_logs"] == 2


def test_restore_relinks_to_the_starter_food_even_when_its_id_differs_on_this_install(scratch):
    s, admin, _ = scratch
    starter_id, _mine = seed_foods(s, admin)
    archive = whole(s, admin)
    s.execute(text("UPDATE food_logs SET food_id = NULL"))                  # a fresh install: same food, different id
    s.execute(text("DELETE FROM foods WHERE owner_id IS NULL"))
    s.execute(text("INSERT INTO foods (id, owner_id, source, name, serving, calories, protein_g, carb_g, fat_g, fiber_g, created_at) "
                   "VALUES (9999, NULL, 'starter', 'Review starter egg', '1 large', 72, 6.3, 0.4, 4.8, 0, '2026-01-01')"))
    s.commit()
    restore.restore_installation(s, archive, uid=admin, creator="Admin", safety_passphrase=PASS)
    assert links(s, admin)["Review starter egg"][0] == 9999


def test_restore_never_imports_a_starter_row_from_the_file_and_survives_id_clashes(scratch):
    s, admin, _ = scratch
    seed_foods(s, admin)
    archive = whole(s, admin)
    entries = {n: archive.read(n) for n in archive.names()}
    key = next(n for n in entries if n.startswith("persons/admin/") and n.endswith("food.json"))
    payload = json.loads(entries[key])
    payload["tables"]["foods"].append({"id": 424242, "owner_id": None, "source": "starter", "name": "Forged starter", "serving": "1", "serving_g": None,
                                       "calories": 1, "protein_g": 0, "carb_g": 0, "fat_g": 0, "fiber_g": 0, "created_at": "2026-01-01 00:00:00"})
    entries[key] = json.dumps(payload).encode()
    restore.restore_installation(s, rewrite(archive, entries), uid=admin, creator="Admin", safety_passphrase=PASS)
    assert s.execute(text("SELECT COUNT(*) FROM foods WHERE name = 'Forged starter'")).scalar() == 0


# ---------------------------------------------------------------- Important: Add does not drop logs of foods already present

def test_adding_a_backup_relinks_logs_to_the_food_the_account_already_has(client, db, me):
    mine = Food(owner_id=me, source="mine", name="Review oats", serving="1 cup", calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4)
    db.add(mine)
    db.commit()
    db.add(FoodLog(owner_id=me, eaten_on=date(2026, 10, 6), meal="breakfast", food_id=mine.id, name="Review oats", serving="1 cup", servings=1,
                   calories=150, protein_g=5, carb_g=27, fat_g=3, fiber_g=4))
    db.commit()
    keep_id = mine.id
    archive = read_archive(build_archive(db, kind="backup", uid=me, creator="Tester", keys=["food"]), max_bytes=50_000_000)
    db.query(FoodLog).filter_by(owner_id=me).delete()                      # the logs are gone, the food is still there
    db.commit()
    loader.load(db, archive, uid=me, username_key="tester", is_admin=True, plan={"food": loader.ADD})
    db.expire_all()
    (log,) = db.query(FoodLog).filter_by(owner_id=me).all()
    assert (log.name, log.food_id, log.calories) == ("Review oats", keep_id, 150)
    assert db.query(Food).filter_by(owner_id=me, name="Review oats").count() == 1


# ---------------------------------------------------------------- Important: tiny values survive editing servings

def test_tiny_nutrient_values_are_not_lost_when_servings_are_edited(client, db, me):
    food = Food(owner_id=me, source="mine", name="Review trace food", serving="1 tsp", calories=5, protein_g=0.04, carb_g=1, fat_g=0.04, fiber_g=0.04)
    db.add(food)
    db.commit()
    client.post("/food/log", data={"date": TODAY, "meal": "snack", "mode": "existing", "servings": "0.1", "food_id": food.id})
    db.expire_all()
    log = db.query(FoodLog).filter_by(name="Review trace food").one()
    assert log.protein_g > 0
    client.post(f"/food/log/{log.id}/edit", data={"servings": "50", "date": TODAY})
    db.expire_all()
    log = db.query(FoodLog).filter_by(name="Review trace food").one()
    assert log.protein_g == pytest.approx(2.0, abs=0.01) and log.fiber_g == pytest.approx(2.0, abs=0.01)


def test_repeated_edits_do_not_drift(client, db, me):
    food = Food(owner_id=me, source="mine", name="Review drift food", serving="1 cup", calories=123.45, protein_g=12.35, carb_g=1, fat_g=1, fiber_g=1)
    db.add(food)
    db.commit()
    client.post("/food/log", data={"date": TODAY, "meal": "snack", "mode": "existing", "servings": "1", "food_id": food.id})
    log_id = db.query(FoodLog).filter_by(name="Review drift food").one().id
    for servings in ("3", "7", "0.5", "10"):
        client.post(f"/food/log/{log_id}/edit", data={"servings": servings, "date": TODAY})
    db.expire_all()
    log = db.get(FoodLog, log_id)
    assert log.protein_g == pytest.approx(123.5, abs=0.01) and log.calories == pytest.approx(1234.5, abs=0.01)

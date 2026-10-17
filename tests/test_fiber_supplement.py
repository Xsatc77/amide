"""A fiber pill (psyllium husk, say) adds its fiber to the day's fiber when the dose is taken."""

from datetime import date

import pytest

from app.db import SessionLocal
from app.food import summary
from app.models import DoseLog, FoodLog, InventoryItem, Medium, User
from tests.test_unit_doses import TODAY, clean as _clean_unit_doses, log, make  # noqa: F401  (the autouse cleaner and the item builder)


@pytest.fixture(autouse=True)
def clean_food(client, me):
    yield
    with SessionLocal() as s:
        s.query(FoodLog).delete()
        s.commit()


def set_fiber(item_id, grams):
    with SessionLocal() as s:
        s.get(InventoryItem, item_id).fiber_g_per_dose = grams
        s.commit()


def day_fiber(me, day=TODAY):
    with SessionLocal() as s:
        return summary.day_summary(s, s.get(User, me), day)["eaten"].fiber_g


def test_a_taken_dose_adds_the_items_fiber_to_the_days_fiber(client, me):
    item_id, pid, piid = make(me, medium=Medium.PILL)
    set_fiber(item_id, 3.5)
    assert day_fiber(me) == 0
    log(client, pid, piid)
    assert day_fiber(me) == pytest.approx(3.5)
    with SessionLocal() as s:
        assert s.query(DoseLog).one().fiber_g == pytest.approx(3.5)


def test_it_adds_to_the_fiber_in_the_food_log(client, db, me):
    db.add(FoodLog(owner_id=me, eaten_on=TODAY, meal="lunch", name="Beans", serving="1", servings=1, calories=100, protein_g=5, carb_g=10, fat_g=1, fiber_g=6))
    db.commit()
    item_id, pid, piid = make(me)
    set_fiber(item_id, 2)
    log(client, pid, piid)
    assert day_fiber(me) == pytest.approx(8)


def test_an_item_with_no_fiber_set_adds_nothing(client, me):
    item_id, pid, piid = make(me)
    log(client, pid, piid)
    assert day_fiber(me) == 0


def test_undo_takes_the_fiber_back_out(client, me):
    item_id, pid, piid = make(me)
    set_fiber(item_id, 3.5)
    log(client, pid, piid)
    with SessionLocal() as s:
        log_id = s.query(DoseLog).one().id
    client.post("/today/undo", data={"dose_log_id": str(log_id)}, follow_redirects=False)
    assert day_fiber(me) == 0


def test_a_skipped_dose_adds_no_fiber(client, me):
    item_id, pid, piid = make(me)
    set_fiber(item_id, 3.5)
    client.post("/today/skip", data={"protocol_id": str(pid), "protocol_item_id": str(piid), "scheduled_date": TODAY.isoformat()}, follow_redirects=False)
    assert day_fiber(me) == 0


def test_the_fiber_amount_is_set_on_the_item_form(client, me):
    item_id, _, _ = make(me)
    page = client.get(f"/inventory/{item_id}").text
    assert 'name="fiber_g_per_dose"' in page
    form_page = client.get("/inventory").text
    assert 'name="fiber_g_per_dose"' in form_page


def edit_fiber(client, item_id, value):
    with SessionLocal() as s:
        it = s.get(InventoryItem, item_id)
        data = {"name": it.name, "category": "Medicine", "count": "0", "vial_size_mg": str(it.vial_size_mg), "vial_size_unit": it.vial_size_unit.value,
                "medium": it.medium.value, "units_per_package": "60", "fiber_g_per_dose": value}
    return client.post(f"/inventory/{item_id}", data=data, follow_redirects=False)


def test_the_fiber_amount_saves_from_the_edit_form_and_is_checked(client, me):
    item_id, _, _ = make(me, medium=Medium.PILL)
    r = edit_fiber(client, item_id, "3.5")
    assert r.status_code == 303, [m for m in __import__("re").findall(r"<small class=\"error\">(.*?)</small>", r.text)]
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).fiber_g_per_dose == pytest.approx(3.5)
    assert edit_fiber(client, item_id, "500").status_code == 422
    assert edit_fiber(client, item_id, "").status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).fiber_g_per_dose is None


def test_the_food_tab_says_how_much_of_the_fiber_came_from_a_pill(client, me):
    item_id, pid, piid = make(me, medium=Medium.PILL)
    set_fiber(item_id, 3.5)
    log(client, pid, piid)
    with SessionLocal() as s:
        u = s.get(User, me)
        from app.models import BiologicalSex, ActivityLevel, BodyMeasurement
        from datetime import datetime
        u.sex, u.birth_date, u.height_in, u.activity_level = BiologicalSex.MALE, date(1990, 1, 1), 70, ActivityLevel.MODERATELY_ACTIVE
        s.add(BodyMeasurement(owner_id=me, measured_at=datetime.now(), weight_lbs=190))
        s.commit()
    try:
        page = client.get("/measurements?tab=food").text
        assert "3.5 g" in page and "from Unitdose Peptide" in page
    finally:
        with SessionLocal() as s:
            from app.models import BodyMeasurement
            s.query(BodyMeasurement).delete()
            u = s.get(User, me)
            u.sex = u.birth_date = u.height_in = u.activity_level = None
            s.commit()


def test_setting_the_fiber_amount_after_a_dose_was_logged_counts_that_dose_too(client, me):
    item_id, pid, piid = make(me, medium=Medium.PILL)
    log(client, pid, piid)                                   # taken before any fiber amount was set
    assert day_fiber(me) == 0
    assert edit_fiber(client, item_id, "3.5").status_code == 303
    assert day_fiber(me) == pytest.approx(3.5)
    edit_fiber(client, item_id, "5")                         # changing it later does not rewrite a dose that already has its amount
    assert day_fiber(me) == pytest.approx(3.5)

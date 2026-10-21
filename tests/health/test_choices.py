from datetime import datetime

from app.health import choices
from app.models import HealthEvent, HealthMetricChoice, HealthReading
from photo_helpers import other_client


def add_reading(db, owner, metric, unit="count", at=datetime(2024, 2, 6, 12)):
    db.add(HealthReading(owner_id=owner, metric=metric, start_at=at, end_at=at, grain="day", value=1.0, unit=unit, source="Phone"))
    db.commit()


def add_event(db, owner, kind="symptom"):
    db.add(HealthEvent(owner_id=owner, kind=kind, external_id=f"{kind}-1", start_at=datetime(2024, 2, 6, 12), name="Headache"))
    db.commit()


def test_nothing_received_means_no_popup(db, me):
    assert choices.received_metrics(db, me) == []
    assert choices.undecided(db, me) == []
    assert choices.needs_popup(db, me) is False


def test_popup_lists_only_received_metrics_with_recommended_marked(db, me):
    add_reading(db, me, "step_count")
    add_reading(db, me, "heart_rate", unit="bpm")
    add_reading(db, me, "weight_&_body_mass", unit="lb")
    add_reading(db, me, "weight_&_body_mass", unit="kg", at=datetime(2024, 2, 7))      # the newest reading names the unit
    add_reading(db, me, "zephyr_index", unit="zz")
    add_event(db, me)
    got = choices.received_metrics(db, me)
    by_key = {m["metric"]: m for m in got}
    assert set(by_key) == {"step_count", "heart_rate", "weight_&_body_mass", "zephyr_index", "event:symptom"}
    assert by_key["step_count"]["label"] == "Steps" and by_key["step_count"]["recommended"] is True
    assert by_key["heart_rate"]["recommended"] is False and by_key["heart_rate"]["unit"] == "bpm"
    assert by_key["weight_&_body_mass"]["unit"] == "kg" and by_key["weight_&_body_mass"]["label"] == "Weight"
    assert by_key["zephyr_index"]["label"] == "Zephyr index" and by_key["zephyr_index"]["section"] == "Mind and other"
    assert by_key["event:symptom"]["section"] == "Mind and other" and by_key["event:symptom"]["label"] == "Symptoms" and by_key["event:symptom"]["recommended"]
    sections = [m["section"] for m in got]
    assert sections == sorted(sections, key=choices.SECTIONS.index)       # grouped in the popup's section order
    assert choices.needs_popup(db, me) is True


def test_saving_records_on_and_off_and_ends_the_popup(db, me):
    add_reading(db, me, "step_count")
    add_reading(db, me, "heart_rate", unit="bpm")
    choices.save_choices(db, me, {"step_count"}, {"step_count", "heart_rate"})
    assert choices.is_enabled(db, me, "step_count") is True
    assert choices.is_enabled(db, me, "heart_rate") is False
    assert db.query(HealthMetricChoice).count() == 2
    assert choices.undecided(db, me) == [] and choices.needs_popup(db, me) is False
    choices.save_choices(db, me, set(), {"step_count"})                     # saving again changes the row, not the count
    assert choices.is_enabled(db, me, "step_count") is False and db.query(HealthMetricChoice).count() == 2


def test_a_new_metric_later_brings_the_popup_back_for_only_that_one(db, me):
    add_reading(db, me, "step_count")
    choices.save_choices(db, me, {"step_count"}, {"step_count"})
    assert choices.needs_popup(db, me) is False
    add_reading(db, me, "flights_climbed")
    assert choices.undecided(db, me) == ["flights_climbed"] and choices.needs_popup(db, me) is True


def test_an_undecided_metric_is_not_enabled(db, me):
    add_reading(db, me, "step_count")
    assert choices.is_enabled(db, me, "step_count") is False


def test_choices_are_per_person(db, me):
    add_reading(db, me, "step_count")
    choices.save_choices(db, me, {"step_count"}, {"step_count"})
    with other_client("healthchoice") as member:
        from app.models import User
        other = db.query(User).filter_by(username_key="healthchoice").one().id
        assert choices.needs_popup(db, other) is False and choices.received_metrics(db, other) == []
        add_reading(db, other, "step_count")
        assert choices.needs_popup(db, other) is True
        assert choices.is_enabled(db, other, "step_count") is False
        r = member.post("/settings/apple-health/choices", data={"metric": ["step_count"], "offered": ["step_count"]})
        assert r.status_code == 303
        assert choices.is_enabled(db, other, "step_count") is True
        db.query(HealthReading).filter_by(owner_id=other).delete()
        db.query(HealthMetricChoice).filter_by(owner_id=other).delete()
        db.commit()
    assert choices.is_enabled(db, me, "step_count") is True


def test_route_saves_and_redirects_back(client, db, me):
    add_reading(db, me, "step_count")
    add_reading(db, me, "heart_rate", unit="bpm")
    r = client.post("/settings/apple-health/choices", data={"metric": ["step_count"], "offered": ["step_count", "heart_rate"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings#integrations"
    assert choices.is_enabled(db, me, "step_count") and not choices.is_enabled(db, me, "heart_rate")


def test_dialog_opens_on_a_page_only_when_something_is_undecided(client, db, me):
    assert 'id="health-choices-dialog"' not in client.get("/dashboard").text           # no Apple data: nothing rendered
    add_reading(db, me, "step_count")
    page = client.get("/dashboard").text
    assert 'id="health-choices-dialog"' in page and "data-open-on-load" in page
    choices.save_choices(db, me, {"step_count"}, {"step_count"})
    page = client.get("/dashboard").text
    assert 'id="health-choices-dialog"' in page and "data-open-on-load" not in page      # still there to reopen, but quiet


def test_dialog_html_has_section_and_metric_switches(client, db, me):
    add_reading(db, me, "step_count")
    add_reading(db, me, "heart_rate", unit="bpm")
    page = client.get("/dashboard").text
    assert 'name="metric" value="step_count"' in page and 'name="metric" value="heart_rate"' in page
    assert 'name="offered" value="step_count"' in page and 'name="offered" value="heart_rate"' in page
    assert 'data-section-switch' in page and "Activity" in page and "Heart and vitals" in page and "Sleep" not in page.split("health-choices-dialog")[1]
    assert page.count("Recommended</span>") == 1            # steps only
    for text in ("Turn all off", "Use recommended", "Save choices", "of 2 on"):
        assert text in page

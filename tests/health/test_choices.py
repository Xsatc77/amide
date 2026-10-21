import re
from datetime import datetime
from pathlib import Path

from app.health import choices
from app.models import HealthEvent, HealthMetricChoice, HealthReading
from photo_helpers import other_client


def add_reading(db, owner, metric, unit="count", at=datetime(2024, 2, 6, 12)):
    """A reading as the parser leaves it: the row, and its metric noted as received (pending until the person decides)."""
    db.add(HealthReading(owner_id=owner, metric=metric, start_at=at, end_at=at, grain="day", value=1.0, unit=unit, source="Phone"))
    choices.note_received(db, owner, {metric})
    db.commit()


def add_event(db, owner, kind="symptom"):
    db.add(HealthEvent(owner_id=owner, kind=kind, external_id=f"{kind}-1", start_at=datetime(2024, 2, 6, 12), name="Headache"))
    choices.note_received(db, owner, {f"event:{kind}"})
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
    assert db.query(HealthMetricChoice).count() == 2 and db.query(HealthMetricChoice).filter_by(decided_at=None).count() == 0
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
    assert 'id="health-choices-dialog"' not in page                    # nothing to decide: other pages draw nothing (Settings keeps the manual list)
    assert 'id="health-choices-dialog"' in client.get("/settings").text


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


def test_automatic_popup_for_a_new_metric_lists_only_that_one(client, db, me):
    add_reading(db, me, "step_count")
    add_reading(db, me, "heart_rate", unit="bpm")
    choices.save_choices(db, me, {"step_count"}, {"step_count", "heart_rate"})
    add_reading(db, me, "flights_climbed")
    page = client.get("/dashboard").text
    dialog = page.split('id="health-choices-dialog"')[1].split("</dialog>")[0]
    assert "data-open-on-load" in page and "New Apple Health Data Arrived" in dialog and "earlier choices are kept" in dialog
    assert 'value="flights_climbed"' in dialog and "step_count" not in dialog and "heart_rate" not in dialog and "1 of 1 on" in dialog
    choices.save_choices(db, me, {"flights_climbed"}, {"flights_climbed"})
    assert choices.is_enabled(db, me, "step_count") and not choices.is_enabled(db, me, "heart_rate")      # earlier choices untouched


def test_first_popup_lists_everything_with_the_first_title(client, db, me):
    add_reading(db, me, "step_count")
    add_reading(db, me, "heart_rate", unit="bpm")
    dialog = client.get("/dashboard").text.split('id="health-choices-dialog"')[1]
    assert "Your Apple Health Data Arrived" in dialog and "step_count" in dialog and "heart_rate" in dialog


def test_manual_list_on_settings_shows_all_and_marks_new_ones(client, db, me):
    add_reading(db, me, "step_count")
    choices.save_choices(db, me, {"step_count"}, {"step_count"})
    add_reading(db, me, "flights_climbed")
    page = client.get("/settings").text
    dialog = page.split('id="health-choices-dialog"')[1].split("</dialog>")[0]
    assert "step_count" in dialog and "flights_climbed" in dialog and dialog.count(">New<") == 1
    assert "data-open-on-load" not in page.split('id="health-choices-dialog"')[1].split(">")[0]


def test_a_page_for_someone_with_no_data_runs_one_health_query_at_most(client, db, me):
    from sqlalchemy import event

    from app.db import engine
    seen = []

    def record(conn, cursor, statement, *rest):
        if "health_" in statement:
            seen.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        client.get("/today")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(seen) <= 1 and not any("GROUP BY" in q or "DISTINCT" in q for q in seen)


def test_saving_ignores_names_that_were_never_received(db, me):
    add_reading(db, me, "step_count")
    choices.save_choices(db, me, {"step_count", "junk_metric"}, {"step_count", "junk_metric", "event:other"})
    assert [c.metric for c in db.query(HealthMetricChoice).filter(HealthMetricChoice.decided_at.is_not(None))] == ["step_count"]


def test_units_read_well_count_is_blank_and_underscores_open_out(db, me):
    add_reading(db, me, "step_count", unit="count")
    add_reading(db, me, "dietary_water", unit="fl_oz_us")
    add_reading(db, me, "zephyr_index", unit="zz_per_min")
    db.commit()
    by_key = {r["metric"]: r for r in choices.received_metrics(db, me)}
    assert by_key["step_count"]["unit"] == "" and by_key["dietary_water"]["unit"] == "fl oz" and by_key["zephyr_index"]["unit"] == "zz per min"


def test_the_parser_notes_received_metrics_as_pending(db, me):
    import gzip
    import json

    from app import config
    from app.health import parse
    from app.models import HealthRaw
    payload = {"metrics": [{"name": "step_count", "units": "count", "data": [{"qty": 3, "date": "2024-02-06 10:00:00 +0000"}]},
                           {"name": "heart_rate", "units": "bpm", "data": [{"Avg": 60, "Min": 50, "Max": 70, "date": "2024-02-06 10:00:00 +0000"}]}],
               "symptoms": [{"name": "Headache", "start": "2024-02-06 10:00:00 +0000"}], "workouts": [{"id": "w", "name": "Walk", "start": "2024-02-06 10:00:00 +0000"}]}
    config.HEALTH_RAW_DIR.mkdir(parents=True, exist_ok=True)
    (config.HEALTH_RAW_DIR / "pending.json.gz").write_bytes(gzip.compress(json.dumps(payload).encode()))
    raw = HealthRaw(owner_id=me, sha256="pending", size_bytes=1, file_name="pending.json.gz")
    db.add(raw)
    db.commit()
    parse.import_raw(db, raw)
    pending = {c.metric: (c.enabled, c.decided_at) for c in db.query(HealthMetricChoice)}
    assert pending == {"step_count": (False, None), "heart_rate": (False, None), "event:symptom": (False, None)}     # workouts are not a choice
    assert choices.undecided(db, me) == ["event:symptom", "heart_rate", "step_count"]
    choices.save_choices(db, me, {"step_count"}, {"step_count", "heart_rate", "event:symptom"})
    parse.import_raw(db, raw)                                                  # importing again never undoes a decision
    assert choices.is_enabled(db, me, "step_count") and choices.undecided(db, me) == []


def test_a_person_with_many_readings_runs_no_scan_on_an_ordinary_page(client, db, me):
    from datetime import timedelta

    from sqlalchemy import event, insert

    from app.db import engine
    base = datetime(2024, 1, 1)
    db.execute(insert(HealthReading), [{"owner_id": me, "metric": f"metric_{i % 50}", "start_at": base + timedelta(minutes=i), "end_at": base,
                                        "grain": "minute", "value": 1.0, "unit": "count", "source": "Phone", "offset_minutes": 0} for i in range(5000)])
    choices.note_received(db, me, {f"metric_{i}" for i in range(50)})
    db.commit()
    seen = []

    def record(conn, cursor, statement, *rest):
        if "health_" in statement:
            seen.append(statement)
    for state in ("pending", "decided"):
        if state == "decided":
            choices.save_choices(db, me, set(), {f"metric_{i}" for i in range(50)})
        seen.clear()
        event.listen(engine, "before_cursor_execute", record)
        try:
            page = client.get("/today").text
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert not any("GROUP BY" in q or "DISTINCT" in q for q in seen), (state, seen)
        assert ("data-open-on-load" in page) == (state == "pending")
    assert len(seen) <= 2


def test_readings_from_before_pending_rows_still_bring_the_list_once(client, db, me):
    db.add(HealthReading(owner_id=me, metric="step_count", start_at=datetime(2024, 2, 6), end_at=datetime(2024, 2, 6), grain="day", value=1.0,
                         unit="count", source="Phone"))          # stored without a pending row, as before this change
    db.commit()
    assert "step_count" in client.get("/settings").text.split('id="health-choices-dialog"')[1]
    assert choices.undecided(db, me) == ["step_count"]          # seeded once; ordinary pages now see it cheaply


def test_dialog_has_a_cancel_button_and_a_close_x_that_save_nothing(client, db, me):
    add_reading(db, me, "step_count")
    dialog = client.get("/dashboard").text.split('id="health-choices-dialog"')[1].split("</dialog>")[0]
    head = dialog.split("</header>")[0]
    assert re.search(r'<button type="button"[^>]*data-close[^>]*aria-label="Close"[^>]*>×</button>', head)          # the X in the header
    foot = dialog.split('class="dialog-foot"')[1]
    assert re.search(r'<button type="button" class="btn" data-close>Cancel</button>', foot)
    js = (Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "health_choices.js").read_text(encoding="utf-8")
    assert "[data-close]" in js and "dialog.close()" in js
    assert "data-open-on-load" in client.get("/dashboard").text          # still undecided: it opens again on the next page


def test_the_real_weight_name_is_a_body_metric_recommended_and_called_weight():
    from app.health.groups import RECOMMENDED, metric_group
    assert metric_group("weight_body_mass") == "Body" and "weight_body_mass" in RECOMMENDED       # the name Health Auto Export really sends
    assert choices.label_for("weight_body_mass") == "Weight"


def test_the_dialog_form_is_not_refilled_by_the_browser(client, db, me):
    add_reading(db, me, "step_count")
    form = client.get("/dashboard").text.split('id="health-choices-dialog"')[1].split("<form", 1)[1].split(">", 1)[0]
    assert 'autocomplete="off"' in form       # Firefox otherwise restores old switch positions over what is saved

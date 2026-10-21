"""The Dashboard's Apple Health card: set up, waiting, or live with today's steps and distance."""

from datetime import datetime, timedelta

import pytest

from app import clock
from app.health import choices
from app.health.dashboard import card
from app.ingest import tokens
from app.models import HealthMetricChoice, HealthRaw, HealthReading, IngestToken, naive_utcnow


@pytest.fixture(autouse=True)
def clean(db, me):
    def wipe():
        db.rollback()
        for model in (IngestToken, HealthMetricChoice, HealthReading, HealthRaw):
            db.query(model).delete()
        db.commit()
    wipe()
    yield
    wipe()


def reading(db, me, metric, value, unit="count"):
    now = naive_utcnow()
    db.add(HealthReading(owner_id=me, metric=metric, start_at=now, end_at=now, offset_minutes=0, grain="point", value=value, unit=unit, source="Phone"))
    db.commit()


def test_a_person_with_nothing_is_offered_the_setup(db, me):
    assert card(db, me, naive_utcnow().date())["state"] == "setup"


def test_a_token_without_data_means_waiting(db, me):
    tokens.create_token(db, me, "iPhone", scope="health")
    assert card(db, me, naive_utcnow().date())["state"] == "waiting"


def test_live_shows_when_data_last_arrived_and_todays_switched_on_figures(db, me):
    now = naive_utcnow()
    reading(db, me, "step_count", 8123)
    reading(db, me, "walking_running_distance", 3.46, "mi")
    reading(db, me, "flights_climbed", 9)
    db.add(HealthRaw(owner_id=me, received_at=now - timedelta(hours=3), sha256="a" * 64, size_bytes=10, file_name="", status="parsed", note="", readings_count=3))
    db.commit()
    choices.note_received(db, me, {"step_count", "walking_running_distance", "flights_climbed"})
    db.commit()
    choices.save_choices(db, me, {"step_count", "walking_running_distance"}, {"step_count", "walking_running_distance", "flights_climbed"})
    data = card(db, me, clock.local_date(now, None), now)
    assert data["state"] == "live" and data["ago"] == "3 hours ago"
    assert [(f["label"], f["value"]) for f in data["figures"]] == [("Steps today", "8,123"), ("Walking and running today", "3.5")]


def test_a_metric_left_off_is_not_shown(db, me):
    reading(db, me, "step_count", 500)
    choices.note_received(db, me, {"step_count"})
    db.commit()
    choices.save_choices(db, me, set(), {"step_count"})
    assert card(db, me, clock.local_date(naive_utcnow(), None))["figures"] == []


def test_the_dashboard_page_shows_the_card_only_for_your_own_dashboard(client, db, me):
    page = client.get("/dashboard").text
    assert "Set up Apple Health" in page and "Coming in a future update" not in page
    tokens.create_token(db, me, "iPhone", scope="health")
    assert "Setup steps" in client.get("/dashboard").text

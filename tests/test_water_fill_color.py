"""The Water Goal card's fill goes blue, then yellow from half the goal, then green once the goal is reached."""

import pytest

from app.db import SessionLocal
from app.models import BodyMeasurement, User, WaterLog

from datetime import datetime


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(WaterLog).delete()
    db.query(BodyMeasurement).delete()
    db.get(User, me).water_goal_oz = None
    db.commit()


def level(client, db, me, oz, goal=100):
    db.query(WaterLog).delete()
    db.get(User, me).water_goal_oz = goal
    db.add(BodyMeasurement(owner_id=me, measured_at=datetime(2026, 9, 1, 8, 0), weight_lbs=200))
    if oz:
        db.add(WaterLog(owner_id=me, logged_at=__import__("app.clock", fromlist=["today"]).today(), ounces=oz))
    db.commit()
    page = client.get("/dashboard").text
    return page.split('data-water-level="', 1)[1].split('"', 1)[0]


@pytest.mark.parametrize("oz,expected", [(0, "low"), (49, "low"), (49.9, "low"), (50, "half"), (80, "half"), (99.4, "half"), (100, "full"), (130, "full")])
def test_the_fill_level_follows_the_share_of_the_goal(client, db, me, oz, expected):
    assert level(client, db, me, oz) == expected


def test_the_fill_colors_are_defined_for_each_level():
    css = open("app/static/css/app.css", encoding="utf-8").read()
    assert '.water-panel[data-water-level="half"] .water-fill' in css and '.water-panel[data-water-level="full"] .water-fill' in css

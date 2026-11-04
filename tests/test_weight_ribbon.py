"""A diagonal banner on the dashboard weight chart: the total lost or gained since the first protocol started."""

import re
from datetime import date, timedelta

import pytest

from app import clock
from app.measurements.calculations import weight_since_start
from app.models import BodyMeasurement, Protocol, User

TODAY = clock.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(BodyMeasurement).delete()
    db.query(Protocol).filter(Protocol.name.like("Ribbon%")).delete(synchronize_session=False)
    db.get(User, me).units = "us"
    db.commit()


def weigh(db, me, days_ago, lbs):
    db.add(BodyMeasurement(owner_id=me, measured_at=TODAY - timedelta(days=days_ago), weight_lbs=lbs))
    db.commit()


def protocol(db, me, days_ago, name="Ribbon A", **over):
    db.add(Protocol(name=name, start_date=TODAY - timedelta(days=days_ago), owner_id=me, **over))
    db.commit()


def ribbon(page):
    found = re.search(r'<div class="weight-ribbon (\w+)"[^>]*>(.*?)</div>', page, re.S)
    return (found.group(1), " ".join(found.group(2).split())) if found else None


def test_weight_lost_since_the_first_protocol_started(client, db, me):
    weigh(db, me, 40, 290.0)
    weigh(db, me, 0, 277.6)
    protocol(db, me, 30)
    protocol(db, me, 10, name="Ribbon B")                     # a later protocol does not move the start
    weigh(db, me, 30, 288.0)                                  # the weigh-in on the start day is the baseline
    assert ribbon(client.get("/dashboard").text) == ("down", "Lost 10.4 lbs")


def test_weight_gained_is_said_plainly(client, db, me):
    weigh(db, me, 20, 180.0)
    weigh(db, me, 0, 183.5)
    protocol(db, me, 20)
    assert ribbon(client.get("/dashboard").text) == ("up", "Gained 3.5 lbs")


def test_no_protocol_means_no_banner(client, db, me):
    weigh(db, me, 20, 180.0)
    weigh(db, me, 0, 175.0)
    assert ribbon(client.get("/dashboard").text) is None


def test_no_weigh_in_before_or_after_the_start_other_than_one_means_no_banner(client, db, me):
    weigh(db, me, 0, 175.0)
    protocol(db, me, 20)                                      # the only weigh-in is also the latest: nothing to compare
    assert ribbon(client.get("/dashboard").text) is None


def test_when_no_weigh_in_is_before_the_start_the_first_one_after_it_is_the_baseline(client, db, me):
    weigh(db, me, 10, 200.0)
    weigh(db, me, 0, 195.0)
    protocol(db, me, 30)
    assert ribbon(client.get("/dashboard").text) == ("down", "Lost 5.0 lbs")


def test_it_is_shown_in_the_users_own_units(client, db, me):
    db.get(User, me).units = "metric"
    db.commit()
    weigh(db, me, 20, 220.0)
    weigh(db, me, 0, 200.0)
    protocol(db, me, 20)
    assert ribbon(client.get("/dashboard").text) == ("down", "Lost 9.1 kg")


def test_the_function_uses_the_earliest_start_and_the_last_weigh_in_of_a_day():
    day = date(2026, 5, 1)
    entries = [(date(2026, 4, 1), 200.0), (date(2026, 5, 1), 190.0), (date(2026, 6, 1), 185.0)]
    assert weight_since_start(entries, day) == (-5.0, day)
    assert weight_since_start([], day) is None
    assert weight_since_start(entries, None) is None

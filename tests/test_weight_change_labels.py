"""The weight chart labels each day's weigh-in with what was lost or gained since the day before."""

import re
from datetime import timedelta

import pytest

from app import clock
from app.models import BodyMeasurement, User

TODAY = clock.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(BodyMeasurement).delete()
    db.get(User, me).units = "us"
    db.commit()


def weigh(db, me, days_ago, lbs, waist=None):
    db.add(BodyMeasurement(owner_id=me, measured_at=TODAY - timedelta(days=days_ago), weight_lbs=lbs, waist_in=waist))
    db.commit()


def deltas(page):
    return re.findall(r'<text class="chart-delta (\w+)"[^>]*>([^<]*)</text>', page)


def test_the_dashboard_weight_chart_shows_each_change_between_weigh_ins(client, db, me):
    weigh(db, me, 14, 290.0)
    weigh(db, me, 7, 284.8)
    weigh(db, me, 0, 285.9)
    assert deltas(client.get("/dashboard").text) == [("down", "−5.2"), ("up", "+1.1")]            # the first weigh-in has nothing before it


def test_several_weigh_ins_on_one_day_count_once_by_the_last_one(client, db, me):
    weigh(db, me, 7, 284.8)
    weigh(db, me, 0, 279.8)             # three readings the same morning, as a smart scale gives
    weigh(db, me, 0, 280.0)
    weigh(db, me, 0, 279.8)
    assert deltas(client.get("/dashboard").text) == [("down", "−5.0")]                              # not 0.0 and +0.2 stacked on one spot


def test_a_single_weigh_in_has_no_change_to_show(client, db, me):
    weigh(db, me, 3, 200.0)
    assert deltas(client.get("/dashboard").text) == []


def test_an_unchanged_weight_reads_zero_in_a_neutral_color(client, db, me):
    weigh(db, me, 5, 200.0)
    weigh(db, me, 0, 200.0)
    assert deltas(client.get("/dashboard").text) == [("flat", "0.0")]


def test_labels_never_sit_on_top_of_each_other_and_the_newest_day_is_always_labeled(client, db, me):
    for n in range(20):
        weigh(db, me, 19 - n, 200.0 - n)
    page = client.get("/dashboard").text
    xs = [float(x) for x in re.findall(r'<text class="chart-delta \w+" x="([\d.]+)"', page)]
    assert 2 <= len(xs) <= 8 and xs == sorted(xs) and all(b - a >= 60 for a, b in zip(xs, xs[1:]))
    assert xs[-1] > 500                                       # the latest weigh-in has its label


def test_a_metric_user_sees_the_change_in_kilograms(client, db, me):
    db.get(User, me).units = "metric"
    db.commit()
    weigh(db, me, 7, 220.0)
    weigh(db, me, 0, 209.0)                                         # 11 lb = about 5.0 kg
    assert deltas(client.get("/dashboard").text) == [("down", "−5.0")]


def test_the_measurements_page_weight_chart_has_them_and_other_charts_do_not(client, db, me):
    weigh(db, me, 7, 200.0, waist=40.0)
    weigh(db, me, 0, 198.0, waist=39.0)
    page = client.get("/measurements?tab=measurements&range=1mo").text
    weight_panel = page.split('data-metric="weight_lbs"', 1)[1].split("overview-chart-panel", 1)[0]
    waist_panel = page.split('data-metric="waist_in"', 1)[1].split("overview-chart-panel", 1)[0]
    assert deltas(weight_panel) == [("down", "−2.0")] and deltas(waist_panel) == []


def test_the_numbers_are_big_enough_to_read_on_a_phone():
    css = open("app/static/css/app.css", encoding="utf-8").read()
    size = int(re.search(r"\.chart-delta \{[^}]*font-size: (\d+)px", css).group(1))
    assert size >= 22              # the chart is drawn 560 wide and shown about half that on a phone

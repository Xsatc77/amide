from datetime import date, datetime

from app.health import choices, series
from app.models import HealthMetricChoice, HealthReading


def add(db, owner, metric, start, value, grain="hour", unit="count", source="Phone"):
    db.add(HealthReading(owner_id=owner, metric=metric, start_at=start, end_at=start, grain=grain, value=value, unit=unit, source=source))
    db.commit()


def test_cumulative_metric_sums_per_local_day(db, me):
    for hour, v in ((8, 1000), (12, 2500), (18, 500)):
        add(db, me, "step_count", datetime(2024, 2, 6, hour), v)
    add(db, me, "step_count", datetime(2024, 2, 7, 9), 40)
    got = series.daily_series(db, me, "step_count", date(2024, 2, 1), date(2024, 2, 28), "UTC")
    assert got == [(date(2024, 2, 6), 4000.0), (date(2024, 2, 7), 40.0)]       # a day with no data is simply absent


def test_weight_takes_the_last_value_of_the_day(db, me):
    add(db, me, "weight_&_body_mass", datetime(2024, 2, 6, 7), 180.0, grain="point", unit="lb")
    add(db, me, "weight_&_body_mass", datetime(2024, 2, 6, 20), 179.0, grain="point", unit="lb")
    add(db, me, "weight_&_body_mass", datetime(2024, 2, 6, 13), 181.0, grain="point", unit="lb")
    assert series.daily_series(db, me, "weight_&_body_mass", date(2024, 2, 1), date(2024, 2, 28), "UTC") == [(date(2024, 2, 6), 179.0)]


def test_two_grains_are_never_added_together(db, me):
    add(db, me, "step_count", datetime(2024, 2, 6, 8), 100, grain="hour")
    add(db, me, "step_count", datetime(2024, 2, 6, 9), 200, grain="hour")
    add(db, me, "step_count", datetime(2024, 2, 6, 0), 9999, grain="day")
    assert series.daily_series(db, me, "step_count", date(2024, 2, 6), date(2024, 2, 6), "UTC") == [(date(2024, 2, 6), 300.0)]
    add(db, me, "step_count", datetime(2024, 2, 7, 0), 5000, grain="day")        # a day with only a day reading uses it
    assert series.daily_series(db, me, "step_count", date(2024, 2, 6), date(2024, 2, 7), "UTC")[1] == (date(2024, 2, 7), 5000.0)


def test_a_2330_minus_0800_reading_lands_on_the_owner_local_day(db, me):
    # 23:30 local at UTC-8 is 07:30 UTC the next day; the stored time is naive UTC
    add(db, me, "step_count", datetime(2024, 2, 7, 7, 30), 120)
    got = series.daily_series(db, me, "step_count", date(2024, 2, 1), date(2024, 2, 28), "America/Los_Angeles")
    assert got == [(date(2024, 2, 6), 120.0)]
    assert series.daily_series(db, me, "step_count", date(2024, 2, 1), date(2024, 2, 28), "UTC") == [(date(2024, 2, 7), 120.0)]


def test_only_enabled_metrics_are_charted(db, me):
    from app.models import naive_utcnow
    add(db, me, "step_count", naive_utcnow(), 10)
    add(db, me, "heart_rate", naive_utcnow(), 60, unit="bpm")
    add(db, me, "flights_climbed", naive_utcnow(), 3)
    db.add_all([HealthMetricChoice(owner_id=me, metric="step_count", enabled=True), HealthMetricChoice(owner_id=me, metric="heart_rate", enabled=False)])
    db.commit()                                                  # flights_climbed was never decided
    assert choices.enabled_metrics(db, me) == {"step_count"}
    data = series.apple_tab_data(db, me, None, "UTC")
    assert [m["metric"] for m in data["metrics"]] == ["step_count"]
    assert data["metrics"][0]["today"] == 10.0


def test_cumulative_set():
    assert "step_count" in series.CUMULATIVE and "weight_&_body_mass" not in series.CUMULATIVE

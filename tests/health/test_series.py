from datetime import date, datetime

from app.health import choices, series
from app.models import HealthMetricChoice, HealthReading


def add(db, owner, metric, start, value, grain="hour", unit="count", source="Phone", offset=0):
    db.add(HealthReading(owner_id=owner, metric=metric, start_at=start, end_at=start, grain=grain, value=value, unit=unit, source=source,
                         offset_minutes=offset))
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
    # 23:30 local at UTC-8 is 07:30 UTC the next day; the stored time is naive UTC and the phone's offset is kept beside it
    add(db, me, "step_count", datetime(2024, 2, 7, 7, 30), 120, offset=-480)
    got = series.daily_series(db, me, "step_count", date(2024, 2, 1), date(2024, 2, 28), "America/Los_Angeles")
    assert got == [(date(2024, 2, 6), 120.0)]
    # the phone's own day decides, whatever zone the viewer is in (or none at all)
    assert series.daily_series(db, me, "step_count", date(2024, 2, 1), date(2024, 2, 28), "UTC") == [(date(2024, 2, 6), 120.0)]
    assert series.daily_series(db, me, "step_count", date(2024, 2, 1), date(2024, 2, 28), None) == [(date(2024, 2, 6), 120.0)]


def test_day_rows_stored_at_local_midnight_east_of_utc_keep_their_day(db, me):
    # "2024-02-06 00:00:00 +0100" is stored as 23:00 UTC on the 5th; it is the 6th's total
    add(db, me, "step_count", datetime(2024, 2, 5, 23), 8000, grain="day", offset=60)
    add(db, me, "step_count", datetime(2024, 2, 6, 23), 9000, grain="day", offset=60)
    got = series.daily_series(db, me, "step_count", date(2024, 2, 1), date(2024, 2, 28), None)
    assert got == [(date(2024, 2, 6), 8000.0), (date(2024, 2, 7), 9000.0)]


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


def test_nutrition_and_activity_totals_add_up_over_the_day():
    for name in ("dietary_caffeine", "dietary_sodium", "dietary_sugar", "caffeine", "potassium", "saturated_fat", "apple_exercise_time",
                 "apple_stand_hour", "basal_energy_burned", "cycling_distance", "swimming_distance", "swim_stroke_count", "push_count"):
        assert name in series.CUMULATIVE, name
    for name in ("heart_rate", "resting_heart_rate", "body_fat_percentage", "walking_speed", "vo2_max", "blood_glucose"):
        assert name not in series.CUMULATIVE, name


def test_every_dietary_name_sums_even_one_not_listed():
    assert series.is_cumulative("dietary_zephyrium") and not series.is_cumulative("zephyr_index")


def test_caffeine_is_summed_per_day(db, me):
    add(db, me, "dietary_caffeine", datetime(2024, 2, 6, 8), 95, unit="mg")
    add(db, me, "dietary_caffeine", datetime(2024, 2, 6, 14), 60, unit="mg")
    assert series.daily_series(db, me, "dietary_caffeine", date(2024, 2, 6), date(2024, 2, 6), None) == [(date(2024, 2, 6), 155.0)]


def test_two_sources_of_one_cumulative_metric_are_not_added_together(db, me):
    # the phone and another app both count the same walk: only the source with the larger total that day is used
    for hour, phone, watch in ((8, 1000, 900), (12, 2000, 2300)):
        add(db, me, "step_count", datetime(2024, 2, 6, hour), phone, source="Acme Phone")
        add(db, me, "step_count", datetime(2024, 2, 6, hour, 30), watch, source="Zephyr Watch")
    add(db, me, "step_count", datetime(2024, 2, 7, 9), 50, source="Zephyr Watch")         # the next day only one source
    got = series.daily_series(db, me, "step_count", date(2024, 2, 1), date(2024, 2, 28), None)
    assert got == [(date(2024, 2, 6), 3200.0), (date(2024, 2, 7), 50.0)]


def test_a_non_cumulative_metric_keeps_the_last_value_across_sources(db, me):
    add(db, me, "heart_rate", datetime(2024, 2, 6, 8), 60, grain="minute", source="Acme Phone")
    add(db, me, "heart_rate", datetime(2024, 2, 6, 9), 72, grain="minute", source="Zephyr Watch")
    assert series.daily_series(db, me, "heart_rate", date(2024, 2, 6), date(2024, 2, 6), None) == [(date(2024, 2, 6), 72.0)]

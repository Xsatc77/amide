import html
import re
from datetime import date, timedelta

from app.models import WeightUnit, WorkoutExerciseLog, WorkoutLog

TODAY = date.today()


def add_log(db, me, days_ago, *rows):
    """rows: (name, area, equipment, load_lb, sets, reps, volume, net_kcal)"""
    day = TODAY - timedelta(days=days_ago)
    log = WorkoutLog(owner_id=me, plan_day_id=None, day_label="Push", plan_name="Plan", log_date=day)
    for name, area, equipment, load, sets, reps, volume, net in rows:
        log.exercise_logs.append(WorkoutExerciseLog(
            name=name, db_exercise=name, area=area, equipment=equipment, completed=True, weight_value=load,
            weight_unit=WeightUnit.LB, sets=sets, reps_value=reps, volume_lb=volume, net_kcal=net, gross_kcal=net * 1.4 if net else None))
    db.add(log)
    db.commit()


def progress(client, **params):
    return html.unescape(client.get("/workouts/progress", params=params).text)


def test_with_no_history_the_page_explains_how_to_start(client, db):
    page = progress(client)
    assert "Log a workout" in page and "<svg" not in page


def test_the_picker_lists_exercises_most_recent_first_and_defaults_to_the_latest(client, db, me):
    add_log(db, me, 20, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    add_log(db, me, 5, ("Back Squat", "Quads/Glutes", "Barbell", 185, 3, 5, 2775, 40))
    page = progress(client)
    options = re.findall(r'<option value="([^"]+)"[^>]*>', page.split('id="progress-exercise"', 1)[1].split("</select>", 1)[0])
    assert options == ["Back Squat", "Bench Press"]
    assert 'value="Back Squat" selected' in page              # the default chart is for the latest exercise


def test_an_exercise_chart_has_a_point_per_session_with_tips(client, db, me):
    add_log(db, me, 30, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    add_log(db, me, 15, ("Bench Press", "Chest", "Barbell", 145, 3, 8, 3480, 32), ("Bench Press", "Chest", "Barbell", 155, 1, 3, 465, 6))
    page = progress(client, exercise="Bench Press")
    top = page.split("Top load", 1)[1].split("</svg>", 1)[0]
    assert top.count("<circle") == 2 and "155 lb" in top
    volume = page.split("Total volume", 1)[1].split("</svg>", 1)[0]
    assert "3,945 lb" in volume and "kcal" in volume


def test_records_are_all_time_even_when_the_range_is_short(client, db, me):
    add_log(db, me, 200, ("Bench Press", "Chest", "Barbell", 225, 1, 1, 225, 3))
    add_log(db, me, 3, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    page = progress(client, range="30")
    records = page.split("Personal records", 1)[1]
    assert "225 lb" in records and "Bench Press" in records


def test_weekly_volume_is_stacked_by_area_with_a_legend(client, db, me):
    add_log(db, me, 10, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30), ("Dumbbell Curl", "Biceps", "Dumbbell", 30, 3, 10, 900, 10))
    page = progress(client)
    weekly = page.split("Weekly volume by body area", 1)[1].split("</section>", 1)[0]
    assert "Chest" in weekly and "Biceps" in weekly and weekly.count("<rect") >= 2


def test_the_equipment_ring_and_top_exercises_list_the_burn(client, db, me):
    add_log(db, me, 4, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30), ("Dumbbell Curl", "Biceps", "Dumbbell", 30, 3, 10, 900, 10))
    page = progress(client)
    ring = page.split("Where the burn comes from", 1)[1]
    assert "Barbell" in ring and "Dumbbell" in ring and "<path" in ring
    assert "Bench Press" in ring and "30 kcal" in ring


def test_unknown_exercises_and_ranges_fall_back(client, db, me):
    add_log(db, me, 4, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    page = progress(client, exercise="No Such Exercise", range="nonsense")
    assert 'value="Bench Press" selected' in page and "<svg" in page


def test_the_range_narrows_the_weekly_chart(client, db, me):
    add_log(db, me, 120, ("Back Squat", "Quads/Glutes", "Barbell", 185, 3, 5, 2775, 40))
    add_log(db, me, 3, ("Bench Press", "Chest", "Barbell", 135, 3, 8, 3240, 30))
    short = progress(client, range="30").split("Weekly volume by body area", 1)[1].split("</section>", 1)[0]
    everything = progress(client, range="all").split("Weekly volume by body area", 1)[1].split("</section>", 1)[0]
    assert "Quads/Glutes" not in short and "Quads/Glutes" in everything

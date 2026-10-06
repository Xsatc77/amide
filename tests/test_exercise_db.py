from app.workouts import exercise_db


def test_the_database_holds_the_workbooks_230_exercises():
    exercises = exercise_db.all_exercises()
    assert len(exercises) == 230
    assert len({e.name.casefold() for e in exercises}) == 230


def test_every_alias_points_at_a_real_exercise():
    assert len(exercise_db.aliases()) >= 280
    assert all(exercise_db.get(target) for target in exercise_db.aliases().values())


def test_lookup_ignores_case_and_surrounding_space():
    assert exercise_db.get("  bench press ").name == "Bench Press"
    assert exercise_db.get("not an exercise") is None
    assert exercise_db.get(None) is None


def test_rows_carry_what_the_estimator_needs():
    bench = exercise_db.get("Bench Press")
    assert (bench.equipment, bench.area, bench.style, bench.sec_per_rep, bench.rest_min) == (
        "Barbell", "Chest", "Heavy Strength", 4.5, 3.0)
    assert bench.model == "rep" and not bench.is_duration and bench.evidence == "Mapped estimate"
    assert exercise_db.get("Plank").is_duration and exercise_db.get("Elliptical").evidence == "Direct Compendium activity"


def test_the_style_table_is_the_workbooks():
    styles = exercise_db.styles()
    assert {n: s.met for n, s in styles.items() if n in exercise_db.choosable_styles()} == {
        "Hypertrophy / General": 3.5, "Heavy Strength": 5.0, "Isolation": 3.5, "Explosive / Power": 6.0,
        "Circuit / Superset": 5.8, "Bodyweight": 3.0, "Kettlebell / Conditioning": 7.5}
    assert styles["General"].met == 3.5   # the label the workbook gives its cardio rows


def test_walking_and_running_use_the_compendium_tables_not_a_flat_3_5():
    assert {e.name: e.speed_table for e in exercise_db.all_exercises() if e.speed_table} == {
        "Treadmill Walk": "treadmill_walk", "Treadmill Incline Walk": "hill_walk",
        "Treadmill Jog": "run", "Treadmill Run": "run"}
    tables = exercise_db.speed_tables()
    assert tables["treadmill_walk"].basis == "speed_mph" and tables["hill_walk"].basis == "grade_pct"
    assert tables["run"].rows[0].code == "12026" and tables["run"].rows[-1].met == 23.0
    for table in tables.values():
        mins = [r.min for r in table.rows]
        assert mins == sorted(mins) and len(set(mins)) == len(mins)


def test_search_finds_names_and_aliases_shortest_first():
    names = [e.name for e in exercise_db.search("incline press")]
    assert "Dumbbell Incline Press" in names
    assert [e.name for e in exercise_db.search("plank")][0] == "Plank"
    assert exercise_db.search("") == []

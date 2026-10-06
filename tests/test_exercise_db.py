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
    assert (bench.equipment, bench.area, bench.sec_per_rep, bench.rest_min) == ("Barbell", "Chest", 4.5, 3.0)
    assert (bench.code, bench.met) == ("02054", 3.5)           # its Compendium row, not a style guess
    assert bench.model == "rep" and not bench.is_duration and bench.evidence == "Mapped estimate"
    assert exercise_db.get("Plank").is_duration and exercise_db.get("Elliptical").evidence == "Direct Compendium activity"


def test_every_exercise_traces_to_a_compendium_row():
    rows = exercise_db.compendium()
    for e in exercise_db.all_exercises():
        if e.speed_table:
            table = exercise_db.speed_tables()[e.speed_table]
            assert table.rows and (table.default is not None or table.basis in ("speed_mph", "grade_pct")), e.name
        else:
            assert e.code in rows and e.met == rows[e.code]["met"], (e.name, e.code, e.met)


def test_no_cardio_row_is_left_at_a_flat_resistance_met():
    flat = {e.name for e in exercise_db.all_exercises() if e.is_duration and e.code == "02054"}
    assert flat == set()                                      # bikes, treadmills and the rest all have their own rows
    named = ("Battle Rope Waves", "Burpee", "Plank", "Lateral Shuffle", "Spin Bike", "Jump Squat")
    assert {n: exercise_db.get(n).code for n in named} == {
        "Battle Rope Waves": "02020", "Burpee": "02214", "Plank": "02024", "Lateral Shuffle": "02078",
        "Spin Bike": "01270", "Jump Squat": "02214"}


def test_the_resistance_categories_are_compendium_rows_with_their_mets():
    cats = {c.code: c.met for c in exercise_db.categories()}
    assert cats["02054"] == 3.5 and cats["02050"] == 6.0 and cats["02052"] == 5.0 and cats["02058"] == 9.8
    assert all(c.code in exercise_db.compendium() for c in exercise_db.categories())
    assert exercise_db.category("02050").label.startswith("Resistance") and exercise_db.category("99999") is None


def test_bikes_rowing_elliptical_and_ski_use_compendium_tables_with_a_default_row():
    tables = exercise_db.speed_tables()
    assert tables["stationary_bike"].basis == "watts" and tables["stationary_bike"].default.code == "01200"
    assert tables["rowing"].default.code == "02070" and tables["elliptical"].basis == "effort"
    assert [r.code for r in tables["ski_erg"].rows] == ["02082", "02084"]
    assert exercise_db.get("Assault Bike").speed_table == "stationary_bike"
    assert exercise_db.get("Assault Bike").evidence == "Mapped estimate"


def test_walking_and_running_use_the_compendium_tables_not_a_flat_3_5():
    assert {e.name: e.speed_table for e in exercise_db.all_exercises() if e.speed_table} == {
        "Treadmill Walk": "treadmill_walk", "Treadmill Incline Walk": "hill_walk", "Treadmill Jog": "run",
        "Treadmill Run": "run", "Stationary Bike": "stationary_bike", "Air Bike": "stationary_bike",
        "Assault Bike": "stationary_bike", "Rowing Ergometer": "rowing", "Elliptical": "elliptical",
        "Ski Ergometer": "ski_erg"}
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

from datetime import date
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select

from app.models import WorkoutPlan, WorkoutSource


def _plan_form(**overrides):
    fields = {
        "name": "My Manual Plan",
        "day_label[]": ["Day 1"],
        "exercise_name[0][]": ["Push-up"],
        "exercise_sets[0][]": ["3"],
        "exercise_reps[0][]": ["10 - 12"],
        "exercise_rest[0][]": [""],
    }
    return {**fields, **overrides}


def _minimal_workout_pdf(text: str) -> bytes:
    """Same verified approach as tests/test_workouts_pdf_parser.py's _pdf_bytes (duplicated here
    since these are separate test files) -- a page with no /Font resource extracts back as
    garbled text, not the original, so a real Helvetica font must be registered first."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font[NameObject("/Encoding")] = NameObject("/WinAnsiEncoding")
    font_ref = writer._add_object(font)
    resources = DictionaryObject()
    font_dict = DictionaryObject()
    font_dict[NameObject("/F1")] = font_ref
    resources[NameObject("/Font")] = font_dict
    page[NameObject("/Resources")] = resources
    ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for line in text.split("\n"):
        safe = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"({safe}) Tj")
        ops.append("0 -14 Td")
    ops.append("ET")
    content = DecodedStreamObject()
    content.set_data("\n".join(ops).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_create_manual_plan(client, db):
    r = client.post("/workouts", data=_plan_form(), follow_redirects=False)
    assert r.status_code == 303
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    assert plan is not None
    assert plan.source == WorkoutSource.MANUAL
    assert plan.ended_on is None  # Active
    assert len(plan.days) == 1
    assert plan.days[0].label == "Day 1"
    assert plan.days[0].exercises[0].name == "Push-up"
    assert plan.days[0].exercises[0].reps_text == "10 - 12"


def test_activating_a_plan_ends_the_previous_active_one(client, db):
    client.post("/workouts", data=_plan_form(name="Plan A"))
    client.post("/workouts", data=_plan_form(name="Plan B"))
    plan_a = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Plan A"))
    plan_b = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Plan B"))
    db.refresh(plan_a)
    assert plan_a.ended_on is not None  # ended when Plan B was created as the new Active plan
    assert plan_b.ended_on is None


def test_schedule_sets_weekdays_on_a_plan_day(client, db):
    client.post("/workouts", data=_plan_form())
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    day_id = plan.days[0].id
    r = client.post(f"/workouts/{plan.id}/schedule", data={f"weekdays[{day_id}]": "MWF"}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(plan)
    assert plan.days[0].weekdays == "MWF"


def test_edit_replaces_days_and_exercises(client, db):
    client.post("/workouts", data=_plan_form())
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "My Manual Plan"))
    r = client.post(f"/workouts/{plan.id}", data=_plan_form(
        **{"exercise_name[0][]": ["Sit-up"]}), follow_redirects=False)
    assert r.status_code == 303
    db.refresh(plan)
    assert len(plan.days[0].exercises) == 1
    assert plan.days[0].exercises[0].name == "Sit-up"


def test_upload_pdf_creates_a_prefilled_plan(client, db):
    # Real Muscle & Strength PDFs list every table BEFORE any day/workout label -- see Task 2's
    # pdf_parser.py docstring. This fixture matches that real order.
    pdf_text = "Exercise Sets Reps\nPush-up 3 10 - 12\nDay 1: Upper Body\n"
    r = client.post(
        "/workouts/upload",
        files={"pdf": ("plan.pdf", _minimal_workout_pdf(pdf_text), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    from app.models import WorkoutPlan, WorkoutSource
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.source == WorkoutSource.PDF))
    assert plan is not None
    assert plan.days[0].label == "Upper Body"
    assert plan.days[0].exercises[0].name == "Push-up"


def test_upload_unparseable_pdf_still_creates_an_empty_editable_plan(client, db):
    r = client.post(
        "/workouts/upload",
        files={"pdf": ("blank.pdf", _minimal_workout_pdf("Not a workout sheet at all."), "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303  # never a rejected upload


def test_log_completion_persists_partial_state(client, db):
    client.post("/workouts", data=_plan_form(
        **{"name": "Log Test Plan",
           "exercise_name[0][]": ["Push-up", "Sit-up"],
           "exercise_sets[0][]": ["3", "3"], "exercise_reps[0][]": ["10", "10"],
           "exercise_rest[0][]": ["", ""]}))
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Log Test Plan"))
    day = plan.days[0]
    ex0, ex1 = day.exercises[0], day.exercises[1]

    r = client.post(f"/workouts/day/{day.id}/log", data={
        "log_date": "2026-01-08",
        f"completed[{ex0.id}]": "on",
        f"weight_value[{ex0.id}]": "25",
        f"weight_unit[{ex0.id}]": "lb",
        f"reps_value[{ex0.id}]": "12",
        # ex1 deliberately left unchecked and blank
    }, follow_redirects=False)
    assert r.status_code == 303

    from app.models import WorkoutLog
    log = db.scalar(select(WorkoutLog).where(WorkoutLog.plan_day_id == day.id))
    by_exercise = {el.exercise_id: el for el in log.exercise_logs}
    assert by_exercise[ex0.id].completed is True
    assert by_exercise[ex0.id].weight_value == 25.0
    assert by_exercise[ex0.id].reps_value == 12
    assert by_exercise[ex1.id].completed is False
    assert by_exercise[ex1.id].weight_value is None


_HISTORY_WARNING = "will also delete any logged history for it"


def test_edit_page_shows_no_warning_when_plan_has_no_logged_history(client, db):
    client.post("/workouts", data=_plan_form(name="No History Plan"))
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "No History Plan"))
    r = client.get(f"/workouts/{plan.id}/edit")
    assert r.status_code == 200
    assert _HISTORY_WARNING not in r.text


def test_edit_page_shows_warning_after_a_workout_has_been_logged(client, db):
    client.post("/workouts", data=_plan_form(name="Has History Plan"))
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Has History Plan"))
    day = plan.days[0]
    ex0 = day.exercises[0]
    client.post(f"/workouts/day/{day.id}/log", data={
        "log_date": "2026-01-08",
        f"completed[{ex0.id}]": "on",
    })
    r = client.get(f"/workouts/{plan.id}/edit")
    assert r.status_code == 200
    assert _HISTORY_WARNING in r.text
    assert 'class="alert"' in r.text  # the styled class that actually exists in app.css


# ---------------------------------------------------------------- id-based reconciliation (C1/C2)

def _two_day_plan(client, db, name="Recon Plan"):
    """A two-day plan (Day A: Push-up, Squat; Day B: Plank), Day A scheduled MWF and logged once."""
    client.post("/workouts", data={
        "name": name,
        "day_label[]": ["Day A", "Day B"],
        "exercise_name[0][]": ["Push-up", "Squat"], "exercise_sets[0][]": ["3", "3"],
        "exercise_reps[0][]": ["10", "10"], "exercise_rest[0][]": ["", ""],
        "exercise_name[1][]": ["Plank"], "exercise_sets[1][]": ["1"],
        "exercise_reps[1][]": ["60s"], "exercise_rest[1][]": [""],
    })
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == name))
    day_a, day_b = plan.days
    day_a.weekdays, day_b.weekdays = "MWF", "T"
    db.commit()
    client.post(f"/workouts/day/{day_a.id}/log", data={
        "log_date": "2026-01-05", f"completed[{day_a.exercises[0].id}]": "on",
        f"weight_value[{day_a.exercises[0].id}]": "20", f"weight_unit[{day_a.exercises[0].id}]": "lb",
    })
    client.post(f"/workouts/day/{day_b.id}/log", data={
        "log_date": "2026-01-06", f"completed[{day_b.exercises[0].id}]": "on",
    })
    db.expire_all()
    return plan


def _form_from_plan(plan, name=None):
    """Exactly what the edit page would post back for `plan` unchanged, real ids included."""
    form = {"name": name or plan.name, "day_label[]": [], "day_id[]": []}
    for i, day in enumerate(plan.days):
        form["day_label[]"].append(day.label)
        form["day_id[]"].append(str(day.id))
        form[f"exercise_id[{i}][]"] = [str(ex.id) for ex in day.exercises]
        form[f"exercise_name[{i}][]"] = [ex.name for ex in day.exercises]
        form[f"exercise_sets[{i}][]"] = [ex.sets_text or "" for ex in day.exercises]
        form[f"exercise_reps[{i}][]"] = [ex.reps_text or "" for ex in day.exercises]
        form[f"exercise_rest[{i}][]"] = [ex.rest_text or "" for ex in day.exercises]
    return form


def test_renaming_plan_only_preserves_weekdays_and_logged_history(client, db):
    from app.models import WorkoutExerciseLog, WorkoutLog
    plan = _two_day_plan(client, db)
    day_ids = [d.id for d in plan.days]
    ex_ids = [ex.id for d in plan.days for ex in d.exercises]

    r = client.post(f"/workouts/{plan.id}", data=_form_from_plan(plan, name="Renamed Plan"),
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert plan.name == "Renamed Plan"
    assert [d.id for d in plan.days] == day_ids
    assert [ex.id for d in plan.days for ex in d.exercises] == ex_ids
    assert plan.days[0].weekdays == "MWF"
    assert plan.days[1].weekdays == "T"
    assert db.scalar(select(WorkoutLog).where(WorkoutLog.plan_day_id == day_ids[0])) is not None
    assert db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.exercise_id == ex_ids[0])) is not None


def test_renaming_an_exercise_updates_in_place_and_keeps_its_logs(client, db):
    from app.models import WorkoutExercise, WorkoutExerciseLog
    plan = _two_day_plan(client, db)
    pushup_id = plan.days[0].exercises[0].id
    form = _form_from_plan(plan)
    form["exercise_name[0][]"] = ["Push-up (knees)", "Squat"]

    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()
    assert [ex.name for ex in plan.days[0].exercises] == ["Push-up (knees)", "Squat"]
    assert plan.days[0].exercises[0].id == pushup_id
    assert db.scalar(select(WorkoutExercise).where(WorkoutExercise.name == "Push-up")) is None  # no duplicate
    ex_log = db.scalar(select(WorkoutExerciseLog).where(WorkoutExerciseLog.exercise_id == pushup_id))
    assert ex_log is not None and ex_log.weight_value == 20.0


def test_removing_a_day_deletes_it_and_its_logs(client, db):
    from app.models import WorkoutLog, WorkoutPlanDay
    plan = _two_day_plan(client, db)
    day_a_id, day_b_id = (d.id for d in plan.days)
    form = _form_from_plan(plan)
    # Post back only Day A (index 0) -- Day B's fields/id are gone, as if its fieldset were removed.
    form = {k: v for k, v in form.items() if not k.endswith("[1][]")}
    form["day_label[]"] = form["day_label[]"][:1]
    form["day_id[]"] = form["day_id[]"][:1]

    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()
    assert [d.id for d in plan.days] == [day_a_id]
    assert db.get(WorkoutPlanDay, day_b_id) is None
    assert db.scalar(select(WorkoutLog).where(WorkoutLog.plan_day_id == day_b_id)) is None
    assert db.scalar(select(WorkoutLog).where(WorkoutLog.plan_day_id == day_a_id)) is not None
    assert plan.days[0].weekdays == "MWF"


def test_removing_an_exercise_deletes_only_that_exercise(client, db):
    plan = _two_day_plan(client, db)
    pushup_id = plan.days[0].exercises[0].id
    form = _form_from_plan(plan)
    for key in ("id", "name", "sets", "reps", "rest"):
        form[f"exercise_{key}[0][]"] = form[f"exercise_{key}[0][]"][:1]  # drop Squat

    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()
    assert [(ex.id, ex.name) for ex in plan.days[0].exercises] == [(pushup_id, "Push-up")]


def test_adding_a_day_with_no_id_creates_it_without_disturbing_existing_days(client, db):
    """The server-side contract behind the edit page's "Add day" button: a new day posts an empty
    day_id and array indices beyond the server-rendered days."""
    from app.models import WorkoutLog
    plan = _two_day_plan(client, db)
    day_a_id, day_b_id = (d.id for d in plan.days)
    form = _form_from_plan(plan)
    form["day_label[]"].append("Day C")
    form["day_id[]"].append("")
    form["exercise_id[2][]"] = ["", ""]
    form["exercise_name[2][]"] = ["Lunge", "Burpee"]
    form["exercise_sets[2][]"] = ["3", ""]
    form["exercise_reps[2][]"] = ["12", ""]
    form["exercise_rest[2][]"] = ["", ""]
    # A new exercise appended to existing Day B via its per-day "Add exercise" button.
    form["exercise_id[1][]"].append("")
    form["exercise_name[1][]"].append("Side plank")
    form["exercise_sets[1][]"].append("")
    form["exercise_reps[1][]"].append("")
    form["exercise_rest[1][]"].append("")

    r = client.post(f"/workouts/{plan.id}", data=form, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert [d.label for d in plan.days] == ["Day A", "Day B", "Day C"]
    assert [d.id for d in plan.days][:2] == [day_a_id, day_b_id]
    assert [ex.name for ex in plan.days[2].exercises] == ["Lunge", "Burpee"]
    assert plan.days[2].exercises[0].sets_text == "3"
    assert [ex.name for ex in plan.days[1].exercises] == ["Plank", "Side plank"]
    assert plan.days[0].weekdays == "MWF" and plan.days[1].weekdays == "T"
    assert plan.days[2].weekdays is None
    assert db.scalar(select(WorkoutLog).where(WorkoutLog.plan_day_id == day_a_id)) is not None
    assert db.scalar(select(WorkoutLog).where(WorkoutLog.plan_day_id == day_b_id)) is not None


def test_renaming_a_day_label_keeps_the_same_day(client, db):
    plan = _two_day_plan(client, db)
    day_a_id = plan.days[0].id
    form = _form_from_plan(plan)
    form["day_label[]"][0] = "Upper Body"
    client.post(f"/workouts/{plan.id}", data=form)
    db.expire_all()
    assert (plan.days[0].id, plan.days[0].label, plan.days[0].weekdays) == (day_a_id, "Upper Body", "MWF")


def test_a_foreign_day_id_is_treated_as_a_new_day_not_hijacked(client, db):
    other = _two_day_plan(client, db, name="Other Plan")
    other_day_id = other.days[0].id
    client.post("/workouts", data=_plan_form(name="Mine"))
    mine = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Mine"))
    form = _form_from_plan(mine)
    form["day_id[]"] = [str(other_day_id)]
    client.post(f"/workouts/{mine.id}", data=form)
    db.expire_all()
    assert other.days[0].id == other_day_id and other.days[0].label == "Day A"
    assert len(mine.days) == 1 and mine.days[0].id != other_day_id


def test_edit_page_renders_ids_editable_labels_and_add_controls(client, db):
    client.post("/workouts", data={"name": "Empty Day Plan", "day_label[]": ["Day 1"]})
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "Empty Day Plan"))
    assert len(plan.days) == 1 and plan.days[0].exercises == []
    r = client.get(f"/workouts/{plan.id}/edit")
    assert r.status_code == 200
    assert 'data-action="add-exercise"' in r.text
    assert "Add exercise" in r.text
    assert 'data-action="add-day"' in r.text
    assert f'name="day_id[]" value="{plan.days[0].id}"' in r.text
    assert '<input type="hidden" name="day_label[]"' not in r.text  # label is editable now
    assert "js/workouts.js" in r.text


def test_new_plan_page_has_add_day_control(client, db):
    r = client.get("/workouts/new")
    assert r.status_code == 200
    assert 'data-action="add-day"' in r.text

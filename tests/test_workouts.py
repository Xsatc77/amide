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


def test_edit_page_shows_no_warning_when_plan_has_no_logged_history(client, db):
    client.post("/workouts", data=_plan_form(name="No History Plan"))
    plan = db.scalar(select(WorkoutPlan).where(WorkoutPlan.name == "No History Plan"))
    r = client.get(f"/workouts/{plan.id}/edit")
    assert r.status_code == 200
    assert "will permanently clear logged workout history" not in r.text


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
    assert "will permanently clear logged workout history" in r.text

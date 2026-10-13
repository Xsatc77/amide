"""Exercise video links: the links a workout PDF carries on its exercise names are kept, and every exercise can open a demo.
The PDFs here are built in the test with invented exercise pages."""

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from sqlalchemy import select

from app.models import WorkoutExercise, WorkoutPlan, WorkoutPlanDay
from app.workouts import video
from app.workouts.pdf_parser import extract_links, extract_text, parse_workout_pdf
from test_workouts import _minimal_workout_pdf

TEXT = "Exercise Sets Reps\nZorvex Press 3 10\nQuill Curl 3 12\nPlain Plank 2 30 Sec\nDay 1: Test Day\n"
PRESS, CURL = "https://example.test/exercises/zorvex-press.html", "https://example.test/exercises/quill-curl"


def linked_pdf(links: dict[int, str]) -> bytes:
    """The workout PDF for TEXT with a link on the given text lines (0 is the header), over the line's left part like a real PDF."""
    writer = PdfWriter(clone_from=PdfReader(BytesIO(_minimal_workout_pdf(TEXT))))
    for line, url in links.items():
        y = 720 - 14 * line
        writer.add_annotation(page_number=0, annotation=Link(rect=(72, y - 3, 200, y + 11), url=url))
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_links_are_read_with_the_row_they_sit_on():
    links = extract_links(linked_pdf({1: PRESS, 2: CURL}))
    assert links == {"Zorvex Press 3 10": PRESS, "Quill Curl 3 12": CURL}


def test_a_pdf_with_no_links_or_that_cannot_be_read_gives_none():
    assert extract_links(_minimal_workout_pdf(TEXT)) == {}
    assert extract_links(b"%PDF-1.7\n" + bytes(range(256)) * 8) == {}


def test_only_web_addresses_are_kept():
    links = extract_links(linked_pdf({1: "javascript:alert(1)", 2: "Leg Extension", 3: PRESS}))
    assert list(links.values()) == [PRESS]


def test_parse_puts_each_link_on_its_exercise():
    parsed = parse_workout_pdf(extract_text(linked_pdf({1: PRESS})), extract_links(linked_pdf({1: PRESS})))
    rows = parsed["days"][0]["exercises"]
    assert [(r["name"], r.get("video_url")) for r in rows] == [("Zorvex Press", PRESS), ("Quill Curl", None), ("Plain Plank", None)]


def test_safe_url_accepts_only_short_web_addresses():
    assert video.safe_url(" https://example.test/a ") == "https://example.test/a"
    for bad in ("javascript:alert(1)", "ftp://x.test/a", "Leg Extension", "", None, "https://" + "a" * 600 + ".test"):
        assert video.safe_url(bad) is None


def test_the_demo_link_is_the_stored_video_or_a_search_for_the_name():
    assert video.demo_url("Zorvex Press", PRESS) == PRESS
    search = video.demo_url("Quill Curl & Row", None)
    assert search.startswith("https://www.youtube.com/results?search_query=") and "Quill+Curl" in search and "%26" in search


@pytest.fixture
def imported(client, db):
    pdf = linked_pdf({1: PRESS, 2: CURL})
    assert client.post("/workouts/upload", files={"pdf": ("plan.pdf", pdf, "application/pdf")}, follow_redirects=False).status_code == 303
    return db.scalar(select(WorkoutPlan).order_by(WorkoutPlan.id.desc()))


def test_an_imported_plan_keeps_the_links_and_the_editor_shows_them(client, db, imported):
    names = {e.name: e.video_url for e in db.scalars(select(WorkoutExercise))}
    assert names == {"Zorvex Press": PRESS, "Quill Curl": CURL, "Plain Plank": None}
    page = client.get(f"/workouts/{imported.id}/edit").text
    assert PRESS in page and CURL in page and "youtube.com/results" in page            # the unlinked exercise gets a search


def test_saving_the_editor_keeps_a_link_and_a_typed_link_is_checked(client, db, imported):
    day = imported.days[0]
    first = day.exercises[0]
    form = {"name": imported.name, "day_label[]": [day.label], "day_id[]": [str(day.id)],
            "exercise_id[0][]": [str(e.id) for e in day.exercises], "exercise_name[0][]": [e.name for e in day.exercises],
            "exercise_sets[0][]": ["3", "3", "2"], "exercise_reps[0][]": ["10", "12", "30 Sec"], "exercise_rest[0][]": ["", "", ""],
            "exercise_video[0][]": [PRESS, "javascript:alert(1)", ""]}
    assert client.post(f"/workouts/{imported.id}", data=form, follow_redirects=False).status_code == 303
    db.expire_all()
    got = {e.name: e.video_url for e in db.get(WorkoutPlanDay, day.id).exercises}
    assert got == {"Zorvex Press": PRESS, "Quill Curl": None, "Plain Plank": None} and first.video_url == PRESS


def test_the_workout_log_page_offers_the_demo(client, db, imported):
    page = client.get(f"/workouts/day/{imported.days[0].id}/log").text
    assert PRESS in page and "Watch" in page and 'rel="noopener noreferrer"' in page

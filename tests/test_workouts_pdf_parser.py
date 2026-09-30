from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.workouts.pdf_parser import extract_text, parse_workout_pdf


def _pdf_bytes(text: str) -> bytes:
    """Builds a minimal real PDF whose extracted text is exactly `text` (one line per `\\n`-split
    line), for round-tripping extract_text/parse_workout_pdf without a binary fixture file
    checked into the repo. Registers a real Helvetica /Font resource -- without one, pypdf's own
    text extraction of a hand-built content stream comes back garbled, not the original text."""
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


def test_extract_text_round_trips_real_pdf_content():
    pdf_bytes = _pdf_bytes("Hello Workout Test")
    assert "Hello Workout Test" in extract_text(pdf_bytes)


# Real Muscle & Strength PDFs list every table ("Exercise Sets Reps[ Rest]" header + its rows)
# BEFORE any of the day/workout labels -- the labels appear later, in a separate "Workout
# Summary" block, in the same order as their tables but not adjacent to them. Confirmed directly
# against all 3 of the owner's sample PDFs while writing this plan. So the parser must find every
# table and every label independently, then zip them by position -- never by textual adjacency.

def test_parse_zips_tables_and_labels_by_position_workout_hash_style():
    text = (
        "Exercise Sets Reps Rest\n"
        "Dumbbell Bench Press 2 10 45 Sec\n"
        "Exercise Sets Reps Rest\n"
        "Goblet Squat 2 10 45 Sec\n"
        "Workout #1 - Upper Body Workout A\n"
        "Workout #2 - Lower Body Workout A\n"
    )
    result = parse_workout_pdf(text)
    assert len(result["days"]) == 2
    assert result["days"][0]["label"] == "Upper Body Workout A"
    assert result["days"][1]["label"] == "Lower Body Workout A"
    ex = result["days"][0]["exercises"][0]
    assert ex["name"] == "Dumbbell Bench Press"
    assert ex["sets_text"] == "2" and ex["reps_text"] == "10" and ex["rest_text"] == "45 Sec"


def test_parse_recognizes_day_colon_header_style_and_no_rest_column():
    text = (
        "Exercise Sets Reps\n"
        "Bent Over Dumbbell Row 2 - 3 10 - 12\n"
        "Day 1: Upper Body\n"
    )
    result = parse_workout_pdf(text)
    ex = result["days"][0]["exercises"][0]
    assert result["days"][0]["label"] == "Upper Body"
    assert ex["name"] == "Bent Over Dumbbell Row"
    assert ex["sets_text"] == "2 - 3" and ex["reps_text"] == "10 - 12"
    assert ex["rest_text"] is None  # this style has no Rest column


def test_parse_recognizes_bare_workout_number_header_style():
    text = (
        "Exercise Sets Reps Rest\n"
        "Goblet Squat 3 10 - 12 2 Min\n"
        "Workout 1\n"
    )
    result = parse_workout_pdf(text)
    assert result["days"][0]["label"] == "Day 1"  # no label text in this header style


def test_parse_recognizes_each_leg_and_each_arm_reps_qualifiers():
    text = (
        "Exercise Sets Reps\n"
        "Walking Lunge 2 - 3 10 - 12 Each Leg\n"
        "One Arm Dumbbell Row 2 - 3 10 - 12 Each Arm\n"
        "Day 1: Full Body\n"
    )
    result = parse_workout_pdf(text)
    exercises = result["days"][0]["exercises"]
    assert exercises[0]["reps_text"] == "10 - 12 Each Leg"
    assert exercises[1]["reps_text"] == "10 - 12 Each Arm"


def test_parse_recognizes_duration_reps_for_isometric_holds():
    """Real row from 8weekbeginnerfatlossworkout.pdf: an isometric hold (Plank) is measured in
    time, not rep count, so the Reps column itself holds a duration string like "30 Sec" --
    not just the Rest column. Previously the reps group required a leading digit-count/range
    format, so this row failed to match _ROW_PATTERN entirely and was silently dropped."""
    text = (
        "Exercise Sets Reps Rest\n"
        "Plank 2 30 Sec 30 Sec\n"
        "Workout #1 - Core Workout A\n"
    )
    result = parse_workout_pdf(text)
    assert len(result["days"]) == 1
    exercises = result["days"][0]["exercises"]
    assert len(exercises) == 1
    ex = exercises[0]
    assert ex["name"] == "Plank"
    assert ex["sets_text"] == "2"
    assert ex["reps_text"] == "30 Sec"
    assert ex["rest_text"] == "30 Sec"


def test_parse_more_tables_than_labels_falls_back_to_day_n():
    """A table with no corresponding label found (fewer labels than tables, or a table whose
    zip-position label came back empty) must still get a usable default label."""
    text = "Exercise Sets Reps\nPush-up 3 10\n"  # zero day/workout labels anywhere
    result = parse_workout_pdf(text)
    assert result["days"][0]["label"] == "Day 1"


def test_parse_unrecognized_format_returns_zero_days_never_raises():
    result = parse_workout_pdf("Some random PDF text\nwith no day headers at all.\n")
    assert result["days"] == []

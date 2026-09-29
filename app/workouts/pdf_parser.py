"""Pure-function parser for workout-plan PDFs (e.g. Muscle & Strength downloads).

Mirrors app/library/sheet_parser.py's philosophy: real-world PDFs vary in header style and
column presence, so every lookup here is positional and defensive. A row or day this can't
confidently parse is simply left out or blank -- never a raised exception. The caller (the
review/edit screen) is always the backstop for anything this gets wrong.

Confirmed directly against 3 real Muscle & Strength sample PDFs: every table ("Exercise Sets
Reps[ Rest]" header, then its rows) appears BEFORE any of the day/workout labels in the
extracted text -- the labels live in a separate "Workout Summary" block later in the document,
in the same order as their tables but not textually adjacent to them. So tables and labels are
found independently and zipped together by POSITION, never by "the label right before/after a
table" (there is no such adjacency in the real files).
"""

from __future__ import annotations

import re
from io import BytesIO

from pypdf import PdfReader

# The literal table-header row, with or without a trailing Rest column. This is the one thing
# that's exactly consistent across every observed real file -- unlike the day/workout labels,
# which vary in style (see _DAY_LABEL_PATTERNS).
_TABLE_HEADER = re.compile(r"^Exercise\s+Sets\s+Reps(\s+Rest)?$")

# Three known day/workout label shapes, tried in order. Each captures a label when the style has
# one; the bare "Workout N" style has none, so its label defaults to "Day N" by the caller.
_DAY_LABEL_PATTERNS = [
    re.compile(r"^Workout #(\d+) - (.+)$"),
    re.compile(r"^Day (\d+): (.+)$"),
    re.compile(r"^Workout (\d+)$"),
]

# One exercise row, anchored from the RIGHT: real rows are single-space-separated with no
# reliable delimiter between the (possibly multi-word) exercise name and its numeric columns, so
# splitting from the left is ambiguous. Anchoring on the trailing Rest ("45 Sec"/"2 Min"), then
# the Reps (a number or range, optionally with a trailing "*" footnote marker or an "Each
# Leg"/"Each Arm"/"Each Side"/bare "Each" qualifier), then the Sets (a number or range), and
# treating everything left over as the name, correctly parses every real row shape confirmed
# directly against all 3 sample PDFs.
_ROW_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+"
    r"(?P<sets>\d+(?:\s*-\s*\d+)?)\s+"
    r"(?P<reps>\d+(?:\s*-\s*\d+)?\*?(?:,?\s*Each(?:\s+\w+)?)?)"
    r"(?:\s+(?P<rest>\d+\s*(?:Min|Sec)))?$"
)


def extract_text(pdf_bytes: bytes) -> str:
    """Every page's text, joined with newlines. Never raises on a page with no extractable text
    (an image-only PDF) -- that page just contributes nothing."""
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _find_table_starts(lines: list[str]) -> list[int]:
    """Line index of every table's header row, in document order."""
    return [i for i, line in enumerate(lines) if _TABLE_HEADER.match(line.strip())]


def _find_day_labels(lines: list[str]) -> list[str | None]:
    """One label per recognized day/workout header found ANYWHERE in the document, in the order
    they appear -- None for a recognized-but-labelless header (the bare "Workout N" style), so
    the caller can tell "found but blank" apart from "not found at all" if it ever needs to."""
    labels: list[str | None] = []
    for line in lines:
        stripped = line.strip()
        for pattern in _DAY_LABEL_PATTERNS:
            m = pattern.match(stripped)
            if m:
                labels.append(m.group(2) if m.lastindex and m.lastindex >= 2 else None)
                break
    return labels


def _parse_exercise_row(line: str) -> dict | None:
    """One exercise row from its raw line, or None if it doesn't match the known row shape at
    all (e.g. it's blank, or a stray line from something else entirely)."""
    m = _ROW_PATTERN.match(line.strip())
    if not m:
        return None
    return {
        "name": m.group("name"),
        "sets_text": m.group("sets"),
        "reps_text": m.group("reps"),
        "rest_text": m.group("rest"),
    }


def parse_workout_pdf(text: str) -> dict:
    """Parse one workout-plan PDF's extracted text into {"name": str, "days": [...]}.

    Never raises on an unrecognized format -- worst case, "days" is an empty list, and the
    caller (the create/review/edit screen) opens with nothing pre-filled rather than rejecting
    the upload."""
    lines = text.split("\n")
    table_starts = _find_table_starts(lines)
    if not table_starts:
        return {"name": "", "days": []}
    day_labels = _find_day_labels(lines)
    days = []
    for i, start in enumerate(table_starts):
        end = table_starts[i + 1] if i + 1 < len(table_starts) else len(lines)
        exercises = []
        for line in lines[start + 1:end]:
            row = _parse_exercise_row(line)
            if row:
                exercises.append(row)
        label = day_labels[i] if i < len(day_labels) and day_labels[i] else f"Day {i + 1}"
        days.append({"label": label, "exercises": exercises})
    return {"name": "", "days": days}

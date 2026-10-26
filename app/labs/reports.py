"""Lab report PDFs from the labs Amide can import, read into the same rows as the LabCorp importer.

LabCorp and Quest each have a reader written against that lab's own layout. Every other lab goes through a general reader that looks
for lines shaped like "name, result, unit, reference range"; it is a best effort, so the import shows what it found and each line can
be corrected with Edit. As with LabCorp, only the test rows and the collection date are read, never the patient block."""

import io
import re
from datetime import date

from app.labs import labcorp
from app.labs.labcorp import ReportRow, ReportRows
from app.models import LabMarker

LABS = [
    ("labcorp", "LabCorp"), ("quest", "Quest Diagnostics"), ("any_lab_test_now", "Any Lab Test Now!"), ("mylab_box", "myLAB Box"),
    ("direct_labs", "Direct Labs"), ("rythm", "Rythm Health"), ("function", "Function Health"), ("everlywell", "Everlywell"),
    ("rxhometest", "RxHomeTest"), ("drsays", "DrSays"), ("siphox", "SiPhox Health"), ("letsgetchecked", "LetsGetChecked"),
    ("superpower", "Superpower"), ("jinfiniti", "Jinfiniti"), ("other", "Another Lab"),
]
LAB_NAMES = dict(LABS)
CHECKED = ("labcorp", "quest")                       # the labs whose layout a reader was written and tested against

NUM = r"\d+(?:\.\d+)?"
RANGE = rf"(?:{NUM}\s*-\s*{NUM}|[<>]\s*(?:OR\s*=\s*)?{NUM})"
QUEST_ROW = re.compile(
    rf"^(?P<name>[A-Z][A-Z0-9 ,()/.'%&+-]*?)\s+(?P<value>{NUM})(?:\s+(?P<flag>[HLIn*])(?=[\s\d<>]))?\s*(?P<range>{RANGE})?\s*"
    rf"(?P<unit>[A-Za-z%][^\s]*)?(?:\s+\(calc\))?(?:\s+[A-Z]{{2}})?$")
QUEST_FOLLOW = re.compile(rf"^Reference range:\s*(?P<range>{RANGE})$")
QUEST_HEADER = "Test Name"
QUEST_FASTING = re.compile(r"FASTING:\s*(YES|NO)", re.I)

# A name (lowercase) -> the Amide marker with the same meaning; anything else is kept as an "Other" marker under the lab's own name.
KNOWN = {**labcorp.KNOWN, **{m.value.lower(): m for m in LabMarker if m is not LabMarker.OTHER}, **{
    "cholesterol, total": LabMarker.TOTAL_CHOLESTEROL, "total cholesterol": LabMarker.TOTAL_CHOLESTEROL, "cholesterol": LabMarker.TOTAL_CHOLESTEROL,
    "hdl": LabMarker.HDL, "hdl-c": LabMarker.HDL, "ldl": LabMarker.LDL, "ldl-c": LabMarker.LDL, "ldl-cholesterol": LabMarker.LDL,
    "urea nitrogen (bun)": LabMarker.BUN, "ast": LabMarker.AST, "alt": LabMarker.ALT,
    "white blood cell count": LabMarker.WBC, "white blood cells": LabMarker.WBC, "platelet count": LabMarker.PLATELETS,
    "vitamin d,25-oh,total,ia": LabMarker.VITAMIN_D, "vitamin d, 25-oh, total": LabMarker.VITAMIN_D, "vitamin d": LabMarker.VITAMIN_D,
    "25-hydroxy vitamin d": LabMarker.VITAMIN_D, "hba1c": LabMarker.HBA1C, "hemoglobin a1c": LabMarker.HBA1C, "a1c": LabMarker.HBA1C,
    "hs-crp": LabMarker.HS_CRP, "hscrp": LabMarker.HS_CRP, "crp, high sensitivity": LabMarker.HS_CRP, "insulin": LabMarker.FASTING_INSULIN,
    "free testosterone": LabMarker.FREE_TESTOSTERONE, "t3, free": LabMarker.FREE_T3, "t4, free": LabMarker.FREE_T4,
    "estradiol, sensitive": LabMarker.ESTRADIOL, "dhea": LabMarker.OTHER,
}}

GENERIC_ROW = re.compile(rf"^(?P<name>[A-Za-z][A-Za-z0-9 ,()/.'%&+-]*?)\s+(?P<qual>[<>])?(?P<value>{NUM})(?:\s+(?P<tail>.+))?$")
GENERIC_RANGE = re.compile(rf"({NUM})\s*-\s*({NUM})|([<>])\s*=?\s*({NUM})")
GENERIC_UNIT = re.compile(r"^[A-Za-zµ%][^\s]*$")
GENERIC_SKIP = re.compile(r"\b(page|phone|fax|tel|date|dob|age|id|npi|zip|suite|street|avenue|accession|specimen|order|box|www|http|range|desirable|patients|diagnostics|laboratory)\b", re.I)
FLAGS = {"high", "low", "h", "l", "abnormal", "normal", "critical", "in", "out", "range", "flag"}
DATE_AFTER = re.compile(r"(?:collected|collection|draw|drawn|sample|specimen)[^0-9A-Za-z]{0,20}(?:date)?[^0-9A-Za-z]{0,6}"
                        r"(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", re.I)


def _range(text: str) -> tuple[float | None, float | None]:
    text = re.sub(r"\s+", "", text.upper()).replace("OR=", "").replace("=", "")
    if text[0] in "<>":
        bound = float(text[1:])
        return (bound, None) if text[0] == ">" else (None, bound)
    low, high = (float(p) for p in text.split("-", 1))
    return (low, high) if low <= high else (None, None)


def _marker(name: str, fasting: bool) -> tuple[LabMarker, str | None]:
    key = name.lower()
    if key == "glucose" and fasting:
        return LabMarker.FASTING_GLUCOSE, None
    found = KNOWN.get(key)
    if found and found is not LabMarker.OTHER:
        return found, None
    return LabMarker.OTHER, (name.title() if name.isupper() and len(name) > 5 else name)[:80]


def _date(text: str) -> date | None:
    m = DATE_AFTER.search(text)
    if not m:
        return None
    raw = m[1]
    try:
        if "-" in raw:
            y, mo, d = raw.split("-")
        else:
            mo, d, y = raw.split("/")
        return date(int(y), int(mo), int(d))
    except ValueError:
        return None


def parse_quest(text: str) -> ReportRows:
    """Rows from a Quest report's text. Its date is rarely in the text, so the draw date often has to be entered."""
    fasting = (QUEST_FASTING.search(text) or [None, ""])[1].upper() == "YES"
    out = ReportRows(_date(text))
    in_table = False
    seen: set[str] = set()
    last: ReportRow | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(QUEST_HEADER):
            in_table = True
            continue
        if not in_table or not line:
            continue
        follow = QUEST_FOLLOW.match(line)
        if follow and last is not None and last.range_low is None and last.range_high is None:
            last.range_low, last.range_high = _range(follow["range"])
            continue
        m = QUEST_ROW.match(line)
        if not m or not (m["range"] or m["unit"]):
            continue
        name = re.sub(r"\s+", " ", m["name"]).strip()
        if name in seen:
            continue
        seen.add(name)
        marker, other = _marker(name, fasting)
        low, high = _range(m["range"]) if m["range"] else (None, None)
        last = ReportRow(name, marker, other, float(m["value"]), None, m["unit"], low, high)
        out.rows.append(last)
    return out


def parse_generic(text: str) -> ReportRows:
    """Rows from any lab's text: a line that starts with a test name, then a number, then a unit and/or a reference range."""
    fasting = bool(re.search(r"fasting[:\s]+(yes|true)|\bfasted\b", text, re.I))
    out = ReportRows(_date(text))
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        m = GENERIC_ROW.match(line)
        if not m or not m["tail"]:
            continue
        name = re.sub(r"\s+", " ", m["name"]).strip(" :-")
        if len(name) < 2 or len(name.split()) > 6 or GENERIC_SKIP.search(name) or name.lower() in FLAGS or name in seen:
            continue
        tail = m["tail"]
        rng = GENERIC_RANGE.search(tail)
        unit = None
        for token in (rng.string[:rng.start()] + " " + rng.string[rng.end():] if rng else tail).split():
            if token.lower().strip(".") in FLAGS or token == "*":
                continue
            if GENERIC_UNIT.match(token):
                unit = token
            break
        if not rng and not unit:
            continue
        low = high = None
        if rng:
            low, high = _range(rng[0])
        seen.add(name)
        marker, other = _marker(name, fasting)
        out.rows.append(ReportRow(name, marker, other, float(m["value"]), m["qual"], unit, low, high))
    return out


def extract_text(data: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def read_pdf(lab: str, data: bytes) -> ReportRows:
    """Rows from a report PDF's bytes, read the way `lab` lays them out. A PDF with no text (a scan) gives no rows."""
    if lab == "labcorp":
        return labcorp.read_pdf(data)
    text = extract_text(data)
    return parse_quest(text) if lab == "quest" else parse_generic(text)

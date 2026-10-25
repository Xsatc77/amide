"""Read a LabCorp "Patient Report" PDF into lab results.

Only the test rows and the collection date are read. The patient block (name, birth date, address,
ids, physician) is never looked at, and nothing outside `ReportRows` leaves this module.

A test row reads: name, optional performing-lab code ("01", "A, 02"), the current result with an
optional High/Low flag, optionally the previous result and its date, the units, and the reference
interval. The previous columns are skipped: only the current result is imported."""

import io
import re
from dataclasses import dataclass, field
from datetime import date

from app.models import LabMarker

NUMBER = r"-?\d+(?:\.\d+)?"
ROW = re.compile(
    rf"^(?P<name>.+?)\s+(?:(?:[A-Z],\s*)?\d{{2}}\s+)?"
    rf"(?P<qual>[<>])?(?P<value>{NUMBER})(?=\s)\s*(?:High|Low|Abnormal|Critical\w*)?\s*"
    rf"(?:[<>]?{NUMBER}\*?\s+\d\d/\d\d/\d{{4}}\s+)?"
    rf"(?:(?P<unit>[^\s\d<>=.][^\s]*|%)\s+)?"
    rf"(?P<range>{NUMBER}\s*-\s*{NUMBER}|[<>]=?\s*{NUMBER}|Not Estab\.)$")
CONTINUATION = re.compile(rf"^(?:[<>])?{NUMBER}\s")           # a wrapped row: its name sat alone on the line above
HEADER = "Test Current Result"
COLLECTED = re.compile(r"Date Collected:\s*(\d\d)/(\d\d)/(\d{4})")
FASTING = re.compile(r"Fasting:\s*(Yes|No)")

# LabCorp's name (lowercase) -> the Amide marker with the same meaning. Anything not listed is kept
# as an "Other" marker under LabCorp's own name.
KNOWN = {
    "wbc": LabMarker.WBC, "hemoglobin": LabMarker.HEMOGLOBIN, "hematocrit": LabMarker.HEMATOCRIT,
    "platelets": LabMarker.PLATELETS, "creatinine": LabMarker.CREATININE, "egfr": LabMarker.EGFR,
    "bun": LabMarker.BUN, "ast (sgot)": LabMarker.AST, "alt (sgpt)": LabMarker.ALT,
    "cholesterol, total": LabMarker.TOTAL_CHOLESTEROL, "triglycerides": LabMarker.TRIGLYCERIDES,
    "hdl cholesterol": LabMarker.HDL, "ldl chol calc (nih)": LabMarker.LDL, "ldl cholesterol": LabMarker.LDL,
    "hemoglobin a1c": LabMarker.HBA1C, "tsh": LabMarker.TSH, "vitamin d, 25-hydroxy": LabMarker.VITAMIN_D,
    "c-reactive protein, cardiac": LabMarker.HS_CRP, "ferritin": LabMarker.FERRITIN,
    "estradiol": LabMarker.ESTRADIOL, "testosterone": LabMarker.TOTAL_TESTOSTERONE,
    "testosterone, total": LabMarker.TOTAL_TESTOSTERONE, "igf-1": LabMarker.IGF_1,
    "cortisol": LabMarker.CORTISOL, "prolactin": LabMarker.PROLACTIN, "shbg": LabMarker.SHBG,
    "lh": LabMarker.LH, "fsh": LabMarker.FSH,
}


@dataclass
class ReportRow:
    name: str
    marker: LabMarker
    marker_other: str | None
    value: float
    qualifier: str | None
    unit: str | None
    range_low: float | None
    range_high: float | None


@dataclass
class ReportRows:
    drawn_at: date | None
    rows: list[ReportRow] = field(default_factory=list)


def _range(text: str) -> tuple[float | None, float | None]:
    text = text.strip()
    if text.startswith("Not Estab"):
        return None, None
    if text[0] in "<>":
        bound = float(text.lstrip("<>=").strip())
        return (bound, None) if text[0] == ">" else (None, bound)
    low, high = (float(p) for p in re.split(r"\s*-\s*(?=\d)", text, maxsplit=1))
    return (low, high) if low <= high else (None, None)


def _marker(name: str, fasting: bool) -> tuple[LabMarker, str | None]:
    key = name.lower()
    if key == "glucose" and fasting:
        return LabMarker.FASTING_GLUCOSE, None
    if key in KNOWN:
        return KNOWN[key], None
    return LabMarker.OTHER, name[:80]


def parse_text(text: str) -> ReportRows:
    """Rows and draw date from the report's extracted text (see `read_pdf`)."""
    collected = COLLECTED.search(text)
    drawn_at = date(int(collected[3]), int(collected[1]), int(collected[2])) if collected else None
    fasting = (FASTING.search(text) or [None, ""])[1] == "Yes"
    out = ReportRows(drawn_at)
    in_table = False
    pending = ""
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(HEADER):
            in_table, pending = True, ""
            continue
        if not in_table or not line or line[0] in ":_*" or line.startswith("Date Created"):
            continue
        m = ROW.match(line)
        if not m and pending and CONTINUATION.match(line):
            m = ROW.match(f"{pending} {line}")
        pending = "" if m else line
        if not m:
            continue
        name = re.sub(r"\s+", " ", m["name"]).strip()
        if name in seen:
            continue
        seen.add(name)
        marker, other = _marker(name, fasting)
        low, high = _range(m["range"])
        out.rows.append(ReportRow(name, marker, other, float(m["value"]), m["qual"], m["unit"], low, high))
    return out


def read_pdf(data: bytes) -> ReportRows:
    """Rows and draw date from a LabCorp report PDF's bytes. A PDF with no text (a scan) gives no rows."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return parse_text("\n".join((page.extract_text() or "") for page in pdf.pages))

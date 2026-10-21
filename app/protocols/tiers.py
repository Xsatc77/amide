"""What the Protocol Builder's Beginner / Intermediate / Advanced checkboxes can fill in from a peptide's library dosing tiers."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calculator import units as unit_math
from app.models import PeptideDosingTier

_NONE = re.compile(r"^\s*(not established|n/?a|none|-)?\s*$", re.IGNORECASE)
# Only wordings that mean exactly one of the builder's frequencies fill the Frequency box; "2x Daily", "3x/week", "Single dose" and the rest are
# shown to the person but left for them to set (the builder takes a repeated dose as the same peptide added twice).
_FREQUENCIES = (
    (re.compile(r"^(once\s+)?daily\b(?!\s*x)", re.IGNORECASE), "daily"),
    (re.compile(r"^(eod|every other day)\s*(,.*)?$", re.IGNORECASE), "eod"),
    (re.compile(r"^(once\s+)?weekly\b", re.IGNORECASE), "weekly"),
    (re.compile(r"^as needed\b", re.IGNORECASE), "as_needed"),
)


def frequency_value(text: str | None) -> str | None:
    """The builder's frequency value a library wording means, or None when it is anything more involved."""
    for pattern, value in _FREQUENCIES:
        if pattern.match((text or "").strip()):
            return value
    return None


def builder_tiers(session: Session) -> dict[str, dict[str, dict]]:
    """{peptide id: {"Beginner" | "Intermediate" | "Advanced": {...}}} for every tier that actually recommends something: the amount and unit when the
    library gives a plain dose ("500mcg"), the frequency when it is unambiguous, the time of day when one is set, and both library wordings for display.
    A level with no row, or only "Not established", is left out, and its checkbox stays grey."""
    out: dict[str, dict[str, dict]] = {}
    for t in session.scalars(select(PeptideDosingTier)):
        if _NONE.match(t.dose_text or ""):
            continue
        parsed = unit_math.parse_dose_text(t.dose_text)
        out.setdefault(str(t.peptide_id), {})[t.level.value] = {
            "amount": parsed[0] if parsed else None, "unit": parsed[1] if parsed else None, "text": t.dose_text,
            "frequency": frequency_value(t.frequency_text), "frequency_text": t.frequency_text,
            "time_of_day": t.time_of_day.value if t.time_of_day else None}
    return out

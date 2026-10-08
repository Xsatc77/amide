"""Pure-function parser for peptide reference-sheet text.

``parse_sheet`` takes the raw scraped text of a single peptide reference file
and returns a plain dict of structured fields. It performs no database
access and imports no ORM models -- it is text in, dict out.

The source files are real-world scraped text with some structural variance
(old vs. new dosing-tier names, a header column that sometimes reads
"DOSE / INJECTION" instead of "DOSE", an optional Cycling Protocol section
that is entirely absent for continuous-use compounds, etc). Every lookup in
this module is written defensively: a missing optional section must never
raise, it should simply leave the corresponding field ``None``/absent.
"""

from __future__ import annotations

import re


class UnrecognizedSheetError(ValueError):
    """Raised by parse_sheet when the text has none of the known
    peptide-sheet section headers -- i.e. it doesn't look like a peptide
    reference sheet at all (e.g. an index page, or an empty/truncated
    file)."""

# Map of "old" dosing-tier names (still present in some real source files) to
# the current, renamed tier labels the app stores.
_TIER_RENAME = {
    "beginner": "Beginner",
    "intermediate": "Intermediate",
    "advanced": "Advanced",
    "moderate": "Intermediate",
    "aggressive": "Advanced",
}

_TIER_ORDER = ["Beginner", "Intermediate", "Advanced"]

# Recognized time-of-day words/phrases -> stored TimeOfDay value.
_TIME_OF_DAY_PATTERNS = [
    (re.compile(r"\bbed\b|\bbedtime\b", re.IGNORECASE), "bedtime"),
    (re.compile(r"\bmorning\b", re.IGNORECASE), "am"),
    (re.compile(r"\bafternoon\b|\bevening\b", re.IGNORECASE), "pm"),
]

# Narrative sections retained verbatim into sheet_sections, keyed by the
# snake_case role name. Each entry is a callable that builds the possible
# literal header string(s) for a given peptide `name`.
_SECTION_HEADERS = {
    "what_is": lambda name: [f"What Is {name}?"],
    "how_it_works": lambda name: [f"How {name} Works"],
    "benefits": lambda name: [f"{name} Benefits"],
    "side_effects": lambda name: [f"{name} Side Effects"],
    "contraindications": lambda name: ["Contraindications"],
    "drug_interactions": lambda name: ["Drug Interactions"],
    "product_quality": lambda name: ["Product Quality"],
    "legal": lambda name: [f"Is {name} Legal?"],
    "who_should_consider": lambda name: [f"Who Should Consider {name}"],
    "related_peptides": lambda name: ["Related Peptides"],
    "citations": lambda name: ["Citations", "References"],
    # Unlike every other templated header in this format, "How to Use" puts the
    # label BEFORE the peptide name ("How to Use <name>"), not after ("<name>
    # How to Use"). The spec is explicit that this raw text is not discarded
    # entirely -- it still goes into sheet_sections for traceability, even
    # though it's ALSO used as a section boundary (see _all_known_header_indices).
    "how_to_use": lambda name: [f"How to Use {name}"],
    "protocols_by_goal": lambda name: [f"{name} Protocols by Goal"],
}

# Headers that must be recognized as section boundaries so their content is
# never absorbed into the section that precedes them, but whose own text is
# deliberately NOT retained anywhere -- the spec excludes these sections
# (Science-vs-Community-Consensus scoring, the Before/After timeline, and its
# accompanying "What to Expect"/detailed-timeline retelling) from storage.
_DISCARD_ONLY_HEADERS = [
    lambda name: [f"{name}: Science vs Community Consensus"],
    lambda name: [f"{name} Before and After"],
    lambda name: ["WHAT TO EXPECT"],
    lambda name: ["DETAILED TIMELINE: SCIENCE VS COMMUNITY"],
]

# All headers used for slicing the document into sections (superset of the
# narrative ones above, plus the structural ones the parser also relies on).
_STRUCTURAL_HEADERS = [
    "Dosage Guide",
    "Cycling Protocol",
    "Stacking Protocols",
    "Estimated Cost",
    "Recommended Monitoring",
    "Pharmacokinetics",
    "Storage & Stability",
]


def _lines(text: str) -> list[str]:
    return text.split("\n")


def _find_header_index(lines: list[str], header: str) -> int | None:
    """Return the index of the line that equals `header` exactly (stripped),
    or None if not found."""
    target = header.strip()
    for i, line in enumerate(lines):
        if line.strip() == target:
            return i
    return None


def _all_known_header_indices(lines: list[str], name: str) -> list[tuple[int, str]]:
    """Collect (index, header_text) for every known section header found in
    the document, used to compute section boundaries (a section runs from
    its own header to the next known header)."""
    candidates: list[str] = []
    for builder in _SECTION_HEADERS.values():
        candidates.extend(builder(name))
    for builder in _DISCARD_ONLY_HEADERS:
        candidates.extend(builder(name))
    for suffix in _STRUCTURAL_HEADERS:
        if suffix in ("Cycling Protocol", "Estimated Cost", "Recommended Monitoring",
                       "Pharmacokinetics", "Storage & Stability"):
            candidates.append(suffix)
        else:
            candidates.append(f"{name} {suffix}")

    found: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in candidates:
            found.append((i, stripped))
    found.sort(key=lambda pair: pair[0])
    return found


def _section_text(lines: list[str], start_idx: int, all_headers: list[tuple[int, str]]) -> str:
    """Return the raw text between the header at start_idx (exclusive) and
    the next known header (exclusive), stripped of leading/trailing blank
    lines."""
    end_idx = len(lines)
    for idx, _ in all_headers:
        if idx > start_idx:
            end_idx = idx
            break
    body_lines = lines[start_idx + 1:end_idx]
    text = "\n".join(body_lines).strip("\n")
    return text.strip()


def _extract_name(text: str) -> str:
    # Real scraped files begin with the site's own navigation chrome ("Peptide
    # Schedule", "Peptides", "Protocols", ...), so the first non-blank line is
    # never the peptide's name. The literal "Full disclaimer" line is the last
    # piece of chrome before the name itself, which repeats immediately after
    # it -- anchor on that instead. Fall back to the first non-blank line for
    # any text that lacks this landmark (e.g. a minimal synthetic fixture).
    lines = _lines(text)
    for i, line in enumerate(lines):
        if line.strip() == "Full disclaimer":
            for candidate in lines[i + 1:]:
                stripped = candidate.strip()
                if stripped:
                    return stripped
            break
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _extract_aliases(text: str, name: str) -> list[str]:
    match = re.search(r"Also known as:\s*(.+)", text)
    if not match:
        return []
    raw = match.group(1)
    # Stop at the first blank-line boundary (regex already confines to one line).
    names = [n.strip() for n in raw.split(",") if n.strip()]
    return names


def _extract_half_life_text(text: str) -> str | None:
    # Real files express half-life in minutes, hours, or days depending on
    # the compound (e.g. "30 min half-life", "10-15 days half-life").
    match = re.search(
        r"~?[\d.]+(?:-[\d.]+)?\s*(?:min(?:ute)?s?|hours?|hrs?|days?)\s*half-life",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(0).strip()
    return None


def _extract_route_summary(lines: list[str], name: str) -> str | None:
    # The quick-facts block lists the half-life line, then a "·" separator,
    # then the route (e.g. "Injection"), then another "·" separator, then the
    # cycle shorthand (e.g. "6w on / 4w off"). Find the half-life line and
    # read forward past the next separator.
    half_life_idx = None
    for i, line in enumerate(lines):
        if re.search(r"half-life", line, re.IGNORECASE):
            half_life_idx = i
            break
    if half_life_idx is None:
        return None
    i = half_life_idx + 1
    # skip separator(s)
    while i < len(lines) and lines[i].strip() in ("·", ""):
        i += 1
    if i < len(lines):
        return lines[i].strip() or None
    return None


def _extract_cycle_shorthand(lines: list[str], route_idx_hint: str | None) -> str | None:
    for i, line in enumerate(lines):
        if re.search(r"\bw on\b.*\boff\b", line, re.IGNORECASE) or re.search(
            r"\d+w on / \d+w off", line, re.IGNORECASE
        ):
            return line.strip()
    return None


def _is_icon_caption_line(line: str) -> bool:
    # The header block includes one of these two fixed captions under the
    # molecule icon, depending on whether the compound has an amino acid
    # sequence (a peptide) or not (a small molecule). Immediately after this
    # line comes: category, evidence level, safety grade (3 lines), then the
    # classification tags/badges, then the peptide's name repeats again.
    return (
        "Icon reflects category theme only" in line
        or "Each bubble = one amino acid" in line
    )


_GRADE_LINE = re.compile(r"^Grade [A-Za-z]$")


def _extract_tags(lines: list[str], name: str) -> list[str]:
    icon_idx = None
    for i, line in enumerate(lines):
        if _is_icon_caption_line(line):
            icon_idx = i
            break
    if icon_idx is None:
        return []
    # The block between the icon caption and the tags is: category, evidence
    # level, safety grade -- always in that order, but NOT always exactly 3
    # lines: some files (e.g. large/unusual sequences) insert an extra icon
    # sub-caption line ("Uses closest standard amino acids for non-standard
    # residues.") before it. A fixed line-count skip under-skips those files,
    # leaking the safety grade into tags. The safety grade line itself
    # ("Grade A"/"Grade B"/...) is a reliable, small-vocabulary anchor
    # regardless of how many lines precede it -- tags start right after it.
    grade_idx = None
    for i in range(icon_idx + 1, len(lines)):
        if _GRADE_LINE.match(lines[i].strip()):
            grade_idx = i
            break
    start = (grade_idx + 1) if grade_idx is not None else icon_idx + 4
    tags: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == name or stripped.startswith("Also known as:"):
            break
        tags.append(stripped)
        if len(tags) >= 15:
            # Defensive cap: the name should always repeat and end the block:
            # if it doesn't (unexpected file shape), stop rather than
            # consuming the rest of the document as "tags".
            break
    return tags


def _extract_summary(text: str, name: str) -> str | None:
    # The opening TL;DR paragraph sits after the quick-facts block and before
    # "What Is <name>?" -- find the first "long" paragraph line following the
    # "Also known as:" line and preceding the "What Is" header.
    lines = _lines(text)
    aliases_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Also known as:"):
            aliases_idx = i
            break
    what_is_idx = _find_header_index(lines, f"What Is {name}?")
    if what_is_idx is None:
        return None
    if aliases_idx is not None:
        start_idx = aliases_idx + 1
    else:
        # Some compounds have no "Also known as:" line at all. Fall back to
        # scanning from right after "Full disclaimer" instead of from the
        # top of the file -- the fixed boilerplate disclaimer line ("Not
        # medical advice. Talk to your provider...") that precedes it is
        # itself long/wordy enough to otherwise be mistaken for the real
        # opening paragraph.
        disclaimer_idx = _find_header_index(lines, "Full disclaimer")
        start_idx = disclaimer_idx + 1 if disclaimer_idx is not None else 0
    for i in range(start_idx, what_is_idx):
        candidate = lines[i].strip()
        if not candidate or candidate.endswith("?"):
            continue
        if _is_icon_caption_line(candidate):
            continue
        # This gap is full of short UI-chrome lines: buttons ("Calculate
        # dose"), popularity/verification badges ("Hot", "1,000+", "Used?"),
        # counters ("3", "15 references"), and social-proof call-outs ("Used
        # by Huberman, Greenfield + 17 more"). The real opening paragraph is
        # always a genuine multi-sentence paragraph -- require both enough
        # length and enough words, rather than trying to enumerate every
        # possible chrome string.
        if len(candidate) < 60 or len(candidate.split()) < 10:
            continue
        return candidate
    return None


def _parse_dosing_tiers(lines: list[str]) -> list[dict]:
    tiers: list[dict] = []
    seen_levels: set[str] = set()
    for i, line in enumerate(lines):
        label = line.strip().lower()
        if label not in _TIER_RENAME:
            continue
        level = _TIER_RENAME[label]
        if level in seen_levels:
            continue
        # Collect the next few non-empty lines/cells looking for the first
        # two values (dose, frequency) by position -- never by validating a
        # header string.
        values: list[str] = []
        row_text_parts: list[str] = [line]
        j = i + 1
        while j < len(lines) and len(values) < 2:
            raw_line = lines[j]
            row_text_parts.append(raw_line)
            # A row's cells may be tab-separated on one line, or spread over
            # consecutive lines. Split on tabs first.
            for cell in raw_line.split("\t"):
                cell = cell.strip()
                if cell:
                    values.append(cell)
                if len(values) >= 2:
                    break
            j += 1
            if j - i > 6:
                break
        dose_text = values[0] if len(values) > 0 else None
        frequency_text = values[1] if len(values) > 1 else None
        row_text = "\n".join(row_text_parts)
        time_of_day = _match_time_of_day(row_text)
        tiers.append(
            {
                "level": level,
                "dose_text": dose_text,
                "frequency_text": frequency_text,
                "time_of_day": time_of_day,
            }
        )
        seen_levels.add(level)
    # Order canonically.
    tiers.sort(key=lambda t: _TIER_ORDER.index(t["level"]) if t["level"] in _TIER_ORDER else 99)
    return tiers


def _match_time_of_day(text: str) -> str | None:
    for pattern, value in _TIME_OF_DAY_PATTERNS:
        if pattern.search(text):
            return value
    return None


def _parse_cycle(lines: list[str], all_headers: list[tuple[int, str]]) -> dict | None:
    idx = _find_header_index(lines, "Cycling Protocol")
    if idx is None:
        return None
    section = _section_text(lines, idx, all_headers)
    on_match = re.search(r"(\d+)\s*weeks?", section)
    section_lines = [l.strip() for l in section.split("\n")]
    on_weeks = None
    off_weeks = None
    for i, l in enumerate(section_lines):
        if l.upper() == "ON PERIOD" and i + 1 < len(section_lines):
            m = re.search(r"(\d+)", section_lines[i + 1])
            if m:
                on_weeks = int(m.group(1))
        if l.upper() == "OFF PERIOD" and i + 1 < len(section_lines):
            m = re.search(r"(\d+)", section_lines[i + 1])
            if m:
                off_weeks = int(m.group(1))
    # The narrative note is whatever text remains after the ON/OFF lines.
    note_lines = []
    skip_labels = {"on period", "off period"}
    i = 0
    while i < len(section_lines):
        l = section_lines[i]
        if l.lower() in skip_labels:
            i += 2  # skip the label and its value line
            continue
        if l:
            note_lines.append(l)
        i += 1
    note = " ".join(note_lines).strip()
    return {"on_weeks": on_weeks, "off_weeks": off_weeks, "note": note}


def _parse_stack_relations(lines: list[str], name: str, all_headers: list[tuple[int, str]]) -> list[dict]:
    header = f"{name} Stacking Protocols"
    idx = _find_header_index(lines, header)
    if idx is None:
        return []
    section = _section_text(lines, idx, all_headers)
    section_lines = [l.strip() for l in section.split("\n")]

    relations: list[dict] = []
    i = 0
    relation_labels = {"works with": "works_with", "avoid": "avoid"}
    while i < len(section_lines):
        label = section_lines[i].lower()
        if label in relation_labels:
            relation = relation_labels[label]
            partner_name = section_lines[i + 1].strip() if i + 1 < len(section_lines) else ""
            # The rationale/note is the following non-empty line(s) up to the
            # next relation label or end of section.
            note_lines = []
            j = i + 2
            while j < len(section_lines) and section_lines[j].lower() not in relation_labels:
                if section_lines[j]:
                    note_lines.append(section_lines[j])
                j += 1
            relations.append(
                {
                    "partner_name": partner_name,
                    "relation": relation,
                    "note": " ".join(note_lines).strip(),
                }
            )
            i = j
        else:
            i += 1
    return relations


def _parse_monitoring_tests(lines: list[str], all_headers: list[tuple[int, str]]) -> list[dict]:
    idx = _find_header_index(lines, "Recommended Monitoring")
    if idx is None:
        return []
    section = _section_text(lines, idx, all_headers)
    section_lines = [l for l in section.split("\n") if l.strip()]
    if not section_lines:
        return []
    # First line is the table header (TEST WHEN WHY TARGET, tab-separated) --
    # located by position, never validated by exact text.
    tests: list[dict] = []
    for line in section_lines[1:]:
        cells = [c.strip() for c in (line.split("\t") if "\t" in line else line.split(" | "))]      # scraped files use tabs; the library-sheet template uses " | "
        # Filter out fully-empty trailing artifacts but keep positional cells.
        cells = [c for c in cells if c != ""]
        if len(cells) < 2:
            continue
        test_name = cells[0] if len(cells) > 0 else None
        when_text = cells[1] if len(cells) > 1 else None
        why_text = cells[2] if len(cells) > 2 else None
        target_text = cells[3] if len(cells) > 3 else None
        tests.append(
            {
                "test_name": test_name,
                "when_text": when_text,
                "why_text": why_text,
                "target_text": target_text,
            }
        )
    return tests


def _parse_pharmacokinetics(lines: list[str], all_headers: list[tuple[int, str]]) -> dict:
    idx = _find_header_index(lines, "Pharmacokinetics")
    result = {"tmax_text": None, "bioavailability_text": None}
    if idx is None:
        return result
    section = _section_text(lines, idx, all_headers)
    section_lines = [l.strip() for l in section.split("\n")]
    labels = {
        "BIOAVAILABILITY": "bioavailability_text",
        "TMAX": "tmax_text",
    }
    i = 0
    while i < len(section_lines):
        upper = section_lines[i].upper()
        if upper in labels and i + 1 < len(section_lines):
            result[labels[upper]] = section_lines[i + 1].strip() or None
        i += 1
    return result


def _parse_storage(lines: list[str], all_headers: list[tuple[int, str]]) -> dict:
    idx = _find_header_index(lines, "Storage & Stability")
    result = {
        "storage_before_text": None,
        "storage_after_text": None,
        "storage_temperature_text": None,
    }
    if idx is None:
        return result
    section = _section_text(lines, idx, all_headers)
    section_lines = [l.strip() for l in section.split("\n")]
    labels = {
        "BEFORE RECONSTITUTION": "storage_before_text",
        "AFTER RECONSTITUTION": "storage_after_text",
        "TEMPERATURE": "storage_temperature_text",
    }
    i = 0
    while i < len(section_lines):
        upper = section_lines[i].upper()
        if upper in labels and i + 1 < len(section_lines):
            result[labels[upper]] = section_lines[i + 1].strip() or None
        i += 1
    return result


def _parse_cost_estimate(lines: list[str], all_headers: list[tuple[int, str]]) -> str | None:
    idx = _find_header_index(lines, "Estimated Cost")
    if idx is None:
        return None
    section = _section_text(lines, idx, all_headers)
    return section.strip() or None


def _parse_legal_status(lines: list[str], name: str, all_headers: list[tuple[int, str]]) -> str | None:
    idx = _find_header_index(lines, f"Is {name} Legal?")
    if idx is None:
        return None
    section = _section_text(lines, idx, all_headers)
    return section.strip() or None


def _parse_sheet_sections(lines: list[str], name: str, all_headers: list[tuple[int, str]]) -> dict:
    sections: dict[str, str | None] = {}
    for role, header_builder in _SECTION_HEADERS.items():
        headers = header_builder(name)
        value = None
        for header in headers:
            idx = _find_header_index(lines, header)
            if idx is not None:
                text = _section_text(lines, idx, all_headers)
                value = text.strip() or None
                break
        if value is not None:
            sections[role] = value
    return sections


def parse_sheet(text: str) -> dict:
    """Parse one peptide reference-sheet's raw text into a structured dict.

    Never raises on a missing optional section -- the corresponding field is
    simply left None/absent.
    """
    lines = _lines(text)
    name = _extract_name(text)
    all_headers = _all_known_header_indices(lines, name)
    if not all_headers:
        raise UnrecognizedSheetError("no recognized section headers found")

    aliases = _extract_aliases(text, name)
    tags = _extract_tags(lines, name)
    half_life_text = _extract_half_life_text(text)
    route_summary = _extract_route_summary(lines, name)
    cycle_shorthand = _extract_cycle_shorthand(lines, route_summary)
    summary = _extract_summary(text, name)

    dosing_idx = _find_header_index(lines, f"{name} Dosage Guide")
    if dosing_idx is not None:
        dosing_section = _section_text(lines, dosing_idx, all_headers)
        dosing_tiers = _parse_dosing_tiers(_lines(dosing_section))
    else:
        dosing_tiers = []

    cycle = _parse_cycle(lines, all_headers)
    stack_relations = _parse_stack_relations(lines, name, all_headers)
    monitoring_tests = _parse_monitoring_tests(lines, all_headers)
    pk = _parse_pharmacokinetics(lines, all_headers)
    storage = _parse_storage(lines, all_headers)
    cost_estimate_text = _parse_cost_estimate(lines, all_headers)
    legal_status_text = _parse_legal_status(lines, name, all_headers)
    sheet_sections = _parse_sheet_sections(lines, name, all_headers)

    return {
        "name": name,
        "aliases": aliases,
        "tags": tags,
        "half_life_text": half_life_text,
        "route_summary": route_summary,
        "cycle_shorthand": cycle_shorthand,
        "summary": summary,
        "dosing_tiers": dosing_tiers,
        "cycle": cycle,
        "stack_relations": stack_relations,
        "monitoring_tests": monitoring_tests,
        "bioavailability_text": pk["bioavailability_text"],
        "tmax_text": pk["tmax_text"],
        "storage_before_text": storage["storage_before_text"],
        "storage_after_text": storage["storage_after_text"],
        "storage_temperature_text": storage["storage_temperature_text"],
        "legal_status_text": legal_status_text,
        "cost_estimate_text": cost_estimate_text,
        "sheet_sections": sheet_sections,
    }


_SIMPLE_PARAGRAPHS = ("what_is", "how_it_works", "legal")
_SIMPLE_LISTS = ("benefits", "side_effects", "contraindications", "drug_interactions", "who_should_consider", "product_quality")


def simple_sections(sheet_sections: dict) -> dict:
    """The readable sections the library page shows, built from a sheet's own sections: paragraph sections become one paragraph, list sections
    one item per line. For sheets written in plain language (the library-sheet template) these need no separate rewriting."""
    out = {}
    for key in _SIMPLE_PARAGRAPHS:
        text = " ".join(l.strip() for l in (sheet_sections.get(key) or "").split("\n") if l.strip())
        if text:
            out[key] = text
    for key in _SIMPLE_LISTS:
        text = "\n".join(l.strip() for l in (sheet_sections.get(key) or "").split("\n") if l.strip())
        if text:
            out[key] = text
    return out

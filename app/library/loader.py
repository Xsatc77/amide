"""Load imported peptide data (cards.json, and parsed reference sheets) into the library.

`load_cards` writes only the card columns. The owner's own fields (aliases, doses, frequency, notes) and
the goal stacks are never touched, so re-importing is always safe.

`load_sheets` writes a parsed reference sheet's fields (see app.library.sheet_parser.parse_sheet) onto a
peptide matched by name, including `aliases` -- unlike `load_cards`, a sheet's own aliases are treated as
canonical reference data and do overwrite whatever was there before. It also clears any old card fields and
replaces the peptide's dosing tiers, cycle, stack relations and monitoring tests wholesale on every load, so
re-importing the same sheet is always safe.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DosingTierLevel,
    Peptide,
    PeptideCycle,
    PeptideDosingTier,
    PeptideMonitoringTest,
    PeptideSource,
    PeptideStackRelation,
    StackRelation,
    TimeOfDay,
)

# Card keys stored in their own columns (or used only for matching); everything else goes in card_details.
_COLUMN_KEYS = {"class": "card_class", "category": "category", "evidence_level": "evidence_level",
                "status": "status", "image": "card_image"}
_NOT_DETAILS = set(_COLUMN_KEYS) | {"card_number", "name"}


@dataclass
class LoadReport:
    updated: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    # Sheet imports skipped because the name matched an existing STARTER/CUSTOM-sourced peptide --
    # i.e. the user's own data, which load_sheets must never silently overwrite.
    skipped_existing: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{len(self.updated)} updated, {len(self.created)} created, {len(self.mismatched)} mismatched"]
        lines += [f"  created: {n}" for n in self.created]
        lines += [f"  skipped {m}" for m in self.mismatched]
        lines += [f"  skipped {n}: existing user-owned peptide, not overwritten" for n in self.skipped_existing]
        return "\n".join(lines)


def load_cards(session: Session, cards: list[dict]) -> LoadReport:
    report = LoadReport()
    for card in cards:
        number, name = card["card_number"], card["name"].strip()
        peptide = session.scalar(select(Peptide).where(Peptide.card_number == number))
        if peptide is None:
            # Not numbered yet but already in the library under this name (e.g. added by the owner): adopt it.
            peptide = session.scalar(select(Peptide).where(Peptide.name == name, Peptide.card_number.is_(None)))
            if peptide is not None:
                peptide.card_number = number
                peptide.source = PeptideSource.CARD
        if peptide is None:
            peptide = Peptide(name=name, card_number=number, source=PeptideSource.CARD)
            session.add(peptide)
            report.created.append(name)
        elif peptide.name.lower() != name.lower():
            report.mismatched.append(f"#{number}: card says '{name}', library has '{peptide.name}'")
            continue
        else:
            report.updated.append(peptide.name)

        for key, column in _COLUMN_KEYS.items():
            setattr(peptide, column, (card.get(key) or None))
        peptide.card_details = {k: v for k, v in card.items() if k not in _NOT_DETAILS}
    session.commit()
    return report


# Sheet keys written straight onto their same-named Peptide column. `cycle_shorthand` (from
# parse_sheet) has no dedicated Peptide column -- it's redundant with the already-structured
# PeptideCycle.on_weeks/off_weeks, which can regenerate the same "6w on / 4w off" text later if
# needed -- so load_sheets doesn't persist it. `notes` stays the owner's own free-text field and is
# never touched here.
_SHEET_COLUMNS = (
    "half_life_text", "bioavailability_text", "tmax_text", "route_summary",
    "storage_before_text", "storage_after_text", "storage_temperature_text",
    "legal_status_text", "cost_estimate_text", "tags", "summary",
)


def load_sheets(session: Session, sheets: list[dict], force_names: set[str] | None = None) -> LoadReport:
    """Load parsed peptide reference sheets (app.library.sheet_parser.parse_sheet output, plus two
    caller-added keys -- "usage_tips" and "sheet_sections_simple", both hand-curated per file
    rather than mechanically parsed) into the library, matching by name (Peptide.name is
    COLLATE NOCASE, so a plain equality comparison is already case-insensitive).

    Whether matched or newly created, the peptide's old card fields are cleared and every sheet
    field is (re)written, so re-importing is always safe and a card-sourced peptide fully converts
    to a sheet-sourced one. The child rows (dosing_tiers, cycle, stack_relations, monitoring_tests)
    are replaced wholesale from the sheet's own lists/dict on every load.

    A name collision with a STARTER/CUSTOM-sourced peptide (one the user added/edited themselves)
    is skipped by default, never silently overwritten -- see `force_names` to override this for
    specific peptides the user has explicitly asked to have overwritten. `notes` is never touched
    by this function either way, since it's always the owner's own free-text field.
    """
    force_names = force_names or set()
    report = LoadReport()
    for sheet in sheets:
        name = sheet["name"].strip()
        peptide = session.scalar(select(Peptide).where(Peptide.name == name))
        if (
            peptide is not None
            and peptide.source in (PeptideSource.STARTER, PeptideSource.CUSTOM)
            and name not in force_names
        ):
            # A name collision with a peptide the user added/edited themselves. Never overwrite
            # user-owned data -- treat this sheet like the "doesn't match" case and skip it,
            # unless the caller explicitly named this peptide in force_names.
            report.skipped_existing.append(name)
            continue
        if peptide is None:
            peptide = Peptide(name=name)
            session.add(peptide)
            report.created.append(name)
        else:
            report.updated.append(peptide.name)

        peptide.card_class = peptide.category = peptide.evidence_level = None
        peptide.status = peptide.card_details = peptide.card_image = None
        peptide.source = PeptideSource.SHEET

        peptide.aliases = ", ".join(sheet.get("aliases") or []) or None
        for column in _SHEET_COLUMNS:
            setattr(peptide, column, sheet.get(column))
        peptide.usage_tips = sheet.get("usage_tips") or []
        peptide.sheet_sections = sheet.get("sheet_sections") or {}
        peptide.sheet_sections_simple = sheet.get("sheet_sections_simple") or {}

        # Replace child rows: clear and flush first so a replacement using the same natural key
        # (dosing tier level, or the one-per-peptide cycle) never collides with the old row on
        # insert.
        peptide.dosing_tiers.clear()
        peptide.stack_relations.clear()
        peptide.monitoring_tests.clear()
        peptide.cycle = None
        session.flush()

        peptide.dosing_tiers = [
            PeptideDosingTier(
                level=DosingTierLevel(tier["level"]),
                dose_text=tier["dose_text"],
                frequency_text=tier["frequency_text"],
                time_of_day=(TimeOfDay(tier["time_of_day"]) if tier.get("time_of_day") else None),
            )
            for tier in sheet.get("dosing_tiers") or []
        ]

        cycle = sheet.get("cycle")
        peptide.cycle = (
            PeptideCycle(on_weeks=cycle.get("on_weeks"), off_weeks=cycle.get("off_weeks"), note=cycle.get("note"))
            if cycle else None
        )

        peptide.stack_relations = [
            PeptideStackRelation(
                partner_name=relation["partner_name"],
                relation=StackRelation(relation["relation"]),
                note=relation.get("note") or "",
            )
            for relation in sheet.get("stack_relations") or []
        ]

        peptide.monitoring_tests = [
            PeptideMonitoringTest(
                test_name=test["test_name"],
                when_text=test["when_text"],
                why_text=test["why_text"],
                target_text=test.get("target_text"),
            )
            for test in sheet.get("monitoring_tests") or []
        ]
    session.commit()
    return report

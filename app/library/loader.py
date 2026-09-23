"""Load imported peptide cards (cards.json) into the library.

Only the card columns are written. The owner's own fields (aliases, doses, frequency, notes) and the goal
stacks are never touched, so re-importing is always safe.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Peptide, PeptideSource

# Card keys stored in their own columns (or used only for matching); everything else goes in card_details.
_COLUMN_KEYS = {"class": "card_class", "category": "category", "evidence_level": "evidence_level",
                "status": "status", "image": "card_image"}
_NOT_DETAILS = set(_COLUMN_KEYS) | {"card_number", "name"}


@dataclass
class LoadReport:
    updated: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{len(self.updated)} updated, {len(self.created)} created, {len(self.mismatched)} mismatched"]
        lines += [f"  created: {n}" for n in self.created]
        lines += [f"  skipped {m}" for m in self.mismatched]
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

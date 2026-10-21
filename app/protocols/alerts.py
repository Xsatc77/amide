"""Builds the conflict checker's plain data from a saved protocol or a builder form, and runs it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calculator import units as unit_math
from app.library.conflicts import ItemData, MedicineData, check
from app.models import Peptide, Protocol, UserMedicine


_TO_MCG = {"mcg": 1.0, "mg": 1000.0}


def library_range(peptide: Peptide) -> tuple:
    """(low, mid, high, unit) of the card's dose range. Older cards carry their own range; a card written from the dosing template has Beginner /
    Intermediate / Advanced tiers instead, so with no range of its own the plain amounts of those tiers give one (lowest, the Intermediate dose, highest).
    It takes at least two comparable plain amounts: one number is not a range, "0.25 mg/kg" has no plain amount, and IU cannot share a scale with mg."""
    if peptide.dose_unit is not None and (peptide.dose_low is not None or peptide.dose_high is not None):
        return peptide.dose_low, peptide.dose_mid, peptide.dose_high, peptide.dose_unit.value
    plain = [(t.level.value, parsed) for t in peptide.dosing_tiers if (parsed := unit_math.parse_dose_text(t.dose_text))]
    if len(plain) < 2 or any(unit not in _TO_MCG for _, (_, unit) in plain):
        return None, None, None, None
    amounts = {level: amount * _TO_MCG[unit] for level, (amount, unit) in plain}
    return min(amounts.values()), amounts.get("Intermediate"), max(amounts.values()), "mcg"


def item_data(peptide: Peptide, dose, unit: str, time_of_day: str, steps=()) -> ItemData:
    avoid = tuple((r.partner_name, r.note) for r in peptide.stack_relations if r.relation.value == "avoid")
    low, mid, high, lib_unit = library_range(peptide)
    return ItemData(peptide_id=peptide.id, name=peptide.name, aliases=peptide.aliases, dose=dose, unit=unit, time_of_day=time_of_day,
                    steps=tuple(steps), lib_low=low, lib_mid=mid, lib_high=high, lib_unit=lib_unit, avoid=avoid)


def medicines_for(session: Session, uid: int) -> list[MedicineData]:
    rows = session.scalars(select(UserMedicine).where(UserMedicine.owner_id == uid).order_by(UserMedicine.name)).all()
    return [MedicineData(m.name, m.dose_text) for m in rows]


def for_protocol(session: Session, p: Protocol, viewer_id: int):
    """Findings for a saved protocol, using the viewer's own medicines (a shared protocol is checked against the person looking)."""
    items = [item_data(it.peptide, it.dose, it.dose_unit.value, it.time_of_day.value,
                       [s.dose for s in it.steps] if p.titration_enabled else []) for it in p.items]
    return check(items, medicines_for(session, viewer_id))


def for_parsed(session: Session, parsed, viewer_id: int):
    """Findings for a builder form that has parsed cleanly. Items for a peptide that is not in the library yet are skipped."""
    items = []
    for it in parsed.items:
        peptide = session.get(Peptide, it.peptide_id) if it.peptide_id else None
        if peptide is not None:
            items.append(item_data(peptide, it.dose, it.dose_unit.value, it.time_of_day.value,
                                   [s.dose for s in it.steps] if parsed.titration_enabled else []))
    return check(items, medicines_for(session, viewer_id))

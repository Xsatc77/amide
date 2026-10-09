"""Builds the conflict checker's plain data from a saved protocol or a builder form, and runs it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.conflicts import ItemData, MedicineData, check
from app.models import Peptide, Protocol, UserMedicine


def item_data(peptide: Peptide, dose, unit: str, time_of_day: str, steps=()) -> ItemData:
    avoid = tuple((r.partner_name, r.note) for r in peptide.stack_relations if r.relation.value == "avoid")
    return ItemData(peptide_id=peptide.id, name=peptide.name, aliases=peptide.aliases, dose=dose, unit=unit, time_of_day=time_of_day,
                    steps=tuple(steps), lib_low=peptide.dose_low, lib_mid=peptide.dose_mid, lib_high=peptide.dose_high,
                    lib_unit=peptide.dose_unit.value if peptide.dose_unit else None, avoid=avoid)


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

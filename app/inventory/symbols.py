"""Each peptide's periodic-table symbol, kept in the database so no two peptides ever share one and a symbol never changes after labels carry it.

The first time a name needs a symbol it is given the first free one from its own letters: the first letter and the next consonant (Retatrutide is Rt, the
long-standing choice), then the first letter with each later letter, then three letters, then a digit as the last resort. A name that is a library peptide
(or one of its aliases) uses the library name, so "Reta" and "Retatrutide" are one peptide with one symbol."""

import re
from itertools import combinations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.inventory.symbol import symbol as first_choice
from app.models import ElementSymbol, Peptide


def candidates(name: str):
    """Symbols to try for `name`, best first, each starting with a capital letter and following with lowercase ones."""
    letters = [c.lower() for c in name if c.isalpha()]
    if not letters:
        yield "X"
        return
    head, rest = letters[0].upper(), letters[1:]
    yield first_choice(name)
    for c in rest:
        yield head + c
    for a, b in combinations(rest, 2):
        yield head + a + b
    for n in range(2, 100):
        yield f"{head}{n}"


def _canonical(session: Session, name: str) -> str:
    """The library peptide's own name when `name` is it (or one of its aliases), else the name as typed, trimmed."""
    typed = re.sub(r"\s+", " ", name or "").strip()
    pep = session.scalar(select(Peptide).where(func.lower(Peptide.name) == typed.lower()))
    if pep is None:
        for p in session.scalars(select(Peptide).where(Peptide.aliases.is_not(None))):
            if typed.lower() in {a.strip().lower() for a in re.split(r"[;,|/]", p.aliases or "") if a.strip()}:
                pep = p
                break
    return pep.name if pep is not None else typed


def symbol_for(session: Session, name: str) -> str:
    """The stored symbol for `name`, handed out now when it has none. Flushes the new row (the caller commits)."""
    key = _canonical(session, name)
    if not key:
        return "?"
    existing = session.scalar(select(ElementSymbol.symbol).where(ElementSymbol.name == key))
    if existing:
        return existing
    taken = {s.lower() for s in session.scalars(select(ElementSymbol.symbol))}
    chosen = next(c for c in candidates(key) if c.lower() not in taken)
    session.add(ElementSymbol(name=key, symbol=chosen))
    session.flush()
    return chosen

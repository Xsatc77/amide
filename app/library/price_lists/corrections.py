# app/library/price_lists/corrections.py
"""Price correction: the administrator says which library card a vendor's product name is. A correction relinks every stored line with that name and is
remembered, so it also applies to every list imported later. Missing cards can be added from the products that match none."""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.matching import name_key
from app.library.price_lists.analysis import SIZE_UNITS, current_lists
from app.library.price_lists.specs import format_spec, merge_specs
from app.models import Peptide, PeptideSource, PriceAlertIgnore, PriceCorrection, PriceListItem, Vendor

MAX_NAME = 120
_WAREHOUSE = {"us": "US", "china": "China"}


class CorrectionError(ValueError):
    """The submitted corrections cannot be applied; the message says why."""


@dataclass(frozen=True)
class Line:
    vendor: str
    warehouse: str
    size: str
    price: str


@dataclass
class Group:
    key: str
    name: str                       # the most common spelling on the current lists
    peptide_id: int | None          # the card most of its lines are linked to
    lines: list[Line] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    key: str
    name: str
    vendors: tuple[str, ...]
    marked: bool                    # chosen "add" on the corrections page


@dataclass
class ApplyResult:
    saved: int = 0
    to_add: list[str] = field(default_factory=list)       # keys marked "add as a new peptide"


def saved_corrections(session: Session) -> dict[str, int | None]:
    """{product key: the card it was corrected to, or None for "no match"}."""
    return {c.product_key: c.peptide_id for c in session.scalars(select(PriceCorrection))}


def _price_text(item: PriceListItem) -> str:
    if item.pack_price is None:
        return "no price"
    if item.pack_type in ("kit", "box"):
        return f"${item.pack_price:,.2f} ({item.pack_type} of {item.pack_size})"
    return f"${item.pack_price:,.2f}" + (f" (pack of {item.pack_size})" if item.pack_size and item.pack_size > 1 else "")


def product_groups(session: Session) -> list[Group]:
    """Every product on the current lists, once, with each vendor's price lines, unmatched first and then A to Z."""
    current = {p.id: p for p in current_lists(session)}
    vendor_names = {v.id: v.name for v in session.scalars(select(Vendor))}
    items = session.scalars(select(PriceListItem).where(
        PriceListItem.price_list_id.in_(list(current)), PriceListItem.product_name.is_not(None)).order_by(PriceListItem.id)).all()
    spellings: dict[str, Counter] = {}
    matches: dict[str, Counter] = {}
    groups: dict[str, Group] = {}
    for it in items:
        key = name_key(it.product_name)
        if not key:
            continue
        plist = current[it.price_list_id]
        group = groups.setdefault(key, Group(key, it.product_name, None))
        spellings.setdefault(key, Counter())[it.product_name] += 1
        matches.setdefault(key, Counter())[it.peptide_id] += 1
        group.lines.append(Line(vendor_names.get(plist.vendor_id) or plist.vendor_name, _WAREHOUSE.get(plist.warehouse.value, plist.warehouse.value),
                                format_spec(it.vial_amount, it.vial_unit, it.form), _price_text(it)))
    for key, group in groups.items():
        counts = spellings[key]
        group.name = max(counts, key=lambda n: (counts[n], n[:1].isupper(), n))
        group.peptide_id = max(matches[key].items(), key=lambda kv: (kv[1], kv[0] is not None))[0]
    return sorted(groups.values(), key=lambda g: (g.peptide_id is not None, g.name.casefold()))


def peptide_options(session: Session) -> list[dict]:
    """Every library card, for the dropdowns."""
    return [{"id": pid, "name": name} for pid, name in session.execute(select(Peptide.id, Peptide.name).order_by(Peptide.name))]


def _relink(session: Session, key: str, name: str, peptide_id: int | None) -> None:
    """Point every stored line of this product at `peptide_id` (None unlinks) and remember it."""
    for it in session.scalars(select(PriceListItem).where(PriceListItem.product_name.is_not(None))):
        if name_key(it.product_name) == key:
            it.peptide_id = peptide_id
    saved = session.scalar(select(PriceCorrection).where(PriceCorrection.product_key == key))
    if saved is None:
        session.add(PriceCorrection(product_key=key, product_name=name[:300], peptide_id=peptide_id))
    else:
        saved.peptide_id = peptide_id
    session.flush()


def apply_changes(session: Session, changes: dict[str, str]) -> ApplyResult:
    """`changes` maps product key to "p<card id>", "none" (no match) or "new" (add it as a new card). A row left as it was stores nothing; nothing is
    applied unless every row is valid. The caller commits."""
    groups = {g.key: g for g in product_groups(session)}
    cards = set(session.scalars(select(Peptide.id)))
    plan: list[tuple[Group, int | None]] = []
    result = ApplyResult()
    for key, value in changes.items():
        group = groups.get(key)
        value = (value or "").strip()
        if group is None or value in ("", "keep"):
            continue
        if value == "new":
            result.to_add.append(key)
        elif value == "none":
            if group.peptide_id is not None:
                plan.append((group, None))
        elif value[:1] == "p" and value[1:].isdigit():
            if int(value[1:]) not in cards:
                raise CorrectionError(f"{group.name}: that peptide does not exist (any more).")
            if int(value[1:]) != group.peptide_id:
                plan.append((group, int(value[1:])))
        else:
            raise CorrectionError(f"{group.name}: choose a peptide from the list.")
    for group, peptide_id in plan:
        _relink(session, group.key, group.name, peptide_id)
    result.saved = len(plan)
    return result


def add_candidates(session: Session, marked: list[str]) -> list[Candidate]:
    """Products that match no card and may need one: the ones just marked "add" first (ticked), then the rest still unmatched. Products the administrator
    set to "no match" or ignored as not a peptide are left out unless just marked."""
    skipped = {k for k, pid in saved_corrections(session).items() if pid is None} | set(session.scalars(select(PriceAlertIgnore.product_key)))
    out = []
    for group in product_groups(session):
        if group.peptide_id is not None or (group.key in skipped and group.key not in marked):
            continue
        vendors = tuple(sorted({line.vendor for line in group.lines}, key=str.casefold))
        out.append(Candidate(group.key, group.name, vendors, group.key in marked))
    return sorted(out, key=lambda c: (not c.marked, c.name.casefold()))


def add_peptides(session: Session, picks: dict[str, str]) -> list[tuple[str, bool]]:
    """Create a card for each ticked product (named as given), or link it to the card that already has that name, and link every line. Returns
    (card name, created). The caller commits."""
    groups = {g.key: g for g in product_groups(session)}
    done = []
    for key, name in picks.items():
        group = groups.get(key)
        name = " ".join((name or "").split())
        if group is None or not name:
            continue
        if len(name) > MAX_NAME:
            raise CorrectionError(f"{name[:30]}...: keep the name under {MAX_NAME} characters.")
        card = session.scalar(select(Peptide).where(Peptide.name == name))
        created = card is None
        if card is None:
            card = Peptide(name=name, source=PeptideSource.CUSTOM)
            session.add(card)
            session.flush()
        _relink(session, key, group.name, card.id)
        sizes = [format_spec(it.vial_amount, it.vial_unit, it.form) for it in session.scalars(select(PriceListItem).where(
            PriceListItem.peptide_id == card.id, PriceListItem.vial_unit.in_(SIZE_UNITS)))]
        merged = merge_specs(card.library_specifications or "", *sizes)
        if merged and merged != (card.library_specifications or ""):
            card.library_specifications = merged
        done.append((card.name, created))
    return done

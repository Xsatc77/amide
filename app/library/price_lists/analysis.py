"""Read-side helpers over the stored price lists: the current lists, a vendor's price history, a library card's
per-vial price range, and the products that need a library card. Query results in, plain objects out."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.matching import match_name, name_key
from app.library.price_lists.importer import _SOURCE_RANK, vendor_key
from app.models import Peptide, PriceAlertIgnore, PriceList, PriceListItem, Vendor

SIZE_UNITS = ("mg", "mcg", "IU")


def current_lists(session: Session) -> list[PriceList]:
    """The newest-dated list for each (vendor, warehouse): a list is current the moment it is imported."""
    best: dict[tuple, PriceList] = {}
    for plist in session.scalars(select(PriceList)):
        key = (plist.vendor_id if plist.vendor_id is not None else vendor_key(plist.vendor_name), plist.warehouse)
        held = best.get(key)
        if held is None or (plist.list_date, plist.imported_at) > (held.list_date, held.imported_at):
            best[key] = plist
    return list(best.values())


# ---------------------------------------------------------------- price range on a library card

@dataclass(frozen=True)
class PriceRange:
    amount: float
    unit: str
    low: float   # price per vial
    high: float
    lists: int   # current lists that carry this size


def price_range(session: Session, peptide_id: int) -> PriceRange | None:
    """Per-vial price range over the current lists, for the card's most commonly listed vial size (so sizes are
    never mixed). Only mg / mcg / IU packs with a stated pack size and a price count."""
    current = [p.id for p in current_lists(session)]
    items = session.scalars(select(PriceListItem).where(
        PriceListItem.peptide_id == peptide_id, PriceListItem.price_list_id.in_(current),
        PriceListItem.vial_unit.in_(SIZE_UNITS), PriceListItem.pack_price.is_not(None),
        PriceListItem.pack_size.is_not(None), PriceListItem.pack_size > 0)).all()
    by_size: dict[tuple[float, str], list[PriceListItem]] = defaultdict(list)
    for it in items:
        by_size[(it.vial_amount, it.vial_unit)].append(it)
    if not by_size:
        return None
    (amount, unit), chosen = max(
        by_size.items(), key=lambda kv: (len({i.price_list_id for i in kv[1]}), len(kv[1]), -kv[0][0]))
    per_vial = [i.pack_price / i.pack_size for i in chosen]
    return PriceRange(amount, unit, min(per_vial), max(per_vial), len({i.price_list_id for i in chosen}))


# ---------------------------------------------------------------- best price per vial, per vial size

@dataclass(frozen=True)
class VendorPrice:
    vendor_id: int | None  # None when the vendor was deleted but its list remains
    vendor_name: str
    warehouse: str         # "us" | "china"
    per_vial: float
    pack_price: float
    pack_size: int
    pack_type: str | None
    list_date: date


@dataclass(frozen=True)
class SizeOption:
    amount: float
    unit: str
    vendors: list[VendorPrice]  # best first, one per vendor


def _rank(price: VendorPrice) -> tuple:
    """Cheapest per vial first (compared to the cent); at the same price the US warehouse wins, then vendor name."""
    return round(price.per_vial, 2), 0 if price.warehouse == "us" else 1, price.vendor_name.casefold()


def best_prices(session: Session, peptide_id: int, limit: int = 5) -> list[SizeOption]:
    """For each vial size a card is listed in on the current lists, the `limit` vendors with the lowest price per vial
    (pack price / vials in the pack). A vendor appears once, at its best warehouse. Lines with no stated pack size or
    price have no per-vial price and are left out."""
    current = {p.id: p for p in current_lists(session)}
    items = session.scalars(select(PriceListItem).where(
        PriceListItem.peptide_id == peptide_id, PriceListItem.price_list_id.in_(list(current)),
        PriceListItem.vial_unit.in_(SIZE_UNITS), PriceListItem.pack_price.is_not(None),
        PriceListItem.pack_size.is_not(None), PriceListItem.pack_size > 0)).all()
    best: dict[tuple[float, str], dict] = defaultdict(dict)
    for it in items:
        plist = current[it.price_list_id]
        price = VendorPrice(plist.vendor_id, plist.vendor_name, plist.warehouse.value, it.pack_price / it.pack_size,
                            it.pack_price, it.pack_size, it.pack_type, plist.list_date)
        vendor = plist.vendor_id if plist.vendor_id is not None else vendor_key(plist.vendor_name)
        held = best[(it.vial_amount, it.vial_unit)].get(vendor)
        if held is None or _rank(price) < _rank(held):
            best[(it.vial_amount, it.vial_unit)][vendor] = price
    options = [SizeOption(amount, unit, sorted(by_vendor.values(), key=_rank)[:limit])
               for (amount, unit), by_vendor in best.items()]
    return sorted(options, key=lambda o: (SIZE_UNITS.index(o.unit), o.amount))


# ---------------------------------------------------------------- a vendor's price history

@dataclass(frozen=True)
class PricePoint:
    list_date: date
    pack_price: float
    pack_size: int | None
    pack_type: str | None

    @property
    def per_vial(self) -> float | None:
        return self.pack_price / self.pack_size if self.pack_size else None


@dataclass
class ProductHistory:
    key: str
    label: str
    peptide_id: int | None
    series: dict[tuple[float, str, str], list[PricePoint]]  # (vial amount, unit, warehouse) -> points by date


def _cost(point: PricePoint) -> float:
    """What a point is compared on when two share a date: per vial when known, so a kit and a box compare fairly."""
    return point.per_vial if point.per_vial is not None else float("inf")


def vendor_price_history(session: Session, vendor_id: int) -> list[ProductHistory]:
    """Every priced product a vendor has listed, with each vial size's dated prices (a China and a USA list are
    separate series). Products are keyed by their library card, or by name when no card matches."""
    rows = session.execute(
        select(PriceListItem, PriceList.list_date, PriceList.warehouse, Peptide.name)
        .join(PriceList, PriceListItem.price_list_id == PriceList.id)
        .outerjoin(Peptide, PriceListItem.peptide_id == Peptide.id)
        .where(PriceList.vendor_id == vendor_id, PriceListItem.pack_price.is_not(None),
               PriceListItem.product_name.is_not(None))).all()
    products: dict[str, ProductHistory] = {}
    spellings: dict[str, Counter] = defaultdict(Counter)
    for it, list_date, warehouse, card_name in rows:
        key = f"p{it.peptide_id}" if it.peptide_id is not None else f"n:{name_key(it.product_name)}"
        spellings[key][it.product_name] += 1
        product = products.setdefault(key, ProductHistory(key, card_name or it.product_name, it.peptide_id, {}))
        points = product.series.setdefault((it.vial_amount, it.vial_unit, warehouse.value), [])
        point = PricePoint(list_date, it.pack_price, it.pack_size, it.pack_type)
        same_day = next((i for i, p in enumerate(points) if p.list_date == list_date), None)
        if same_day is None:
            points.append(point)
        elif _cost(point) < _cost(points[same_day]):
            points[same_day] = point
    for key, product in products.items():
        if product.peptide_id is None:
            product.label = max(spellings[key], key=lambda n: (spellings[key][n], n[:1].isupper(), n))
        for points in product.series.values():
            points.sort(key=lambda p: p.list_date)
    return sorted(products.values(), key=lambda p: p.label.casefold())


# ---------------------------------------------------------------- new peptides

@dataclass(frozen=True)
class NewPeptide:
    key: str
    name: str
    vendors: tuple[str, ...]


def new_peptides(session: Session) -> list[NewPeptide]:
    """Products in the current lists that match no library card (checked live, so adding a card or an alias
    clears one), have a mg / mcg / IU size, and have not been ignored, each with the vendors that list it."""
    current = {p.id: p for p in current_lists(session)}
    cards = session.scalars(select(Peptide)).all()
    ignored = set(session.scalars(select(PriceAlertIgnore.product_key)))
    vendor_names = {v.id: v.name for v in session.scalars(select(Vendor))}
    items = session.scalars(select(PriceListItem).where(
        PriceListItem.price_list_id.in_(list(current)), PriceListItem.peptide_id.is_(None),
        PriceListItem.product_name.is_not(None), PriceListItem.vial_unit.in_(SIZE_UNITS)))
    found: dict[str, tuple[Counter, set[str]]] = {}
    for it in items:
        key = name_key(it.product_name)
        if not key or key in ignored:
            continue
        names, vendors = found.setdefault(key, (Counter(), set()))
        names[it.product_name] += 1
        plist = current[it.price_list_id]
        vendors.add(vendor_names.get(plist.vendor_id) or plist.vendor_name)
    out = []
    for key, (names, vendors) in found.items():
        name = max(names, key=lambda n: (names[n], n[:1].isupper(), n))
        if match_name(name, cards) is None:
            out.append(NewPeptide(key, name, tuple(sorted(vendors, key=str.casefold))))
    return sorted(out, key=lambda n: n.name.casefold())


def rematch_items(session: Session) -> int:
    """Link stored items that matched no card at import time to the cards that exist now (a card or alias added
    since). Returns how many were linked; the caller commits."""
    cards = session.scalars(select(Peptide)).all()
    items = session.scalars(select(PriceListItem).where(
        PriceListItem.peptide_id.is_(None), PriceListItem.product_name.is_not(None))).all()
    linked = 0
    resolved: dict[str, int | None] = {}
    for it in items:
        if it.product_name not in resolved:
            match = match_name(it.product_name, cards)
            resolved[it.product_name] = (
                min(match.cards, key=lambda c: (_SOURCE_RANK.get(c.source, 9), c.id)).id if match else None)
        if resolved[it.product_name] is not None:
            it.peptide_id = resolved[it.product_name]
            linked += 1
    return linked


def ignore_product(session: Session, name: str) -> None:
    """Mark a product as not a peptide. Idempotent; the caller commits."""
    key = name_key(name)
    if key and session.scalar(select(PriceAlertIgnore).where(PriceAlertIgnore.product_key == key)) is None:
        session.add(PriceAlertIgnore(product_key=key, product_name=name.strip()[:300]))

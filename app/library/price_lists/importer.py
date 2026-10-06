# app/library/price_lists/importer.py
"""Store a parsed price list: its vendor, warehouse, product lines, and the sizes added to library cards."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.matching import match_name, name_key
from app.library.price_lists.filename import parse_filename
from app.library.price_lists.names import PrefixName, propagate_names, repair_names
from app.library.price_lists.reader import PriceListData
from app.library.price_lists.rows import pack_type
from app.library.price_lists.specs import format_spec, merge_specs
from app.models import (
    Peptide, PeptideSource, PriceList, PriceListItem, Vendor, Warehouse, WarehouseSource,
)

_GENERIC_VENDOR_WORDS = {"peptide", "peptides"}
_SOURCE_RANK = {PeptideSource.CARD: 0, PeptideSource.STARTER: 1, PeptideSource.SHEET: 2, PeptideSource.CUSTOM: 3}
_SPEC_UNITS = {"mg", "mcg", "IU"}


@dataclass
class ImportReport:
    filename: str
    vendor_name: str = ""
    vendor_created: bool = False
    warehouse: str = ""
    warehouse_source: str = ""
    rows: int = 0
    matched: int = 0
    specs_added: int = 0  # library cards whose sizes changed
    flagged: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    skipped: str | None = None


def vendor_key(name: str) -> str:
    """A vendor's comparison form: letters and digits only, without the words "peptide"/"peptides"."""
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", name).casefold())
    return "".join(w for w in words if w not in _GENERIC_VENDOR_WORDS) or name_key(name)


def resolve_vendor(session: Session, vendor_name: str, list_date: date) -> tuple[Vendor, bool]:
    key = vendor_key(vendor_name)
    vendor = next((v for v in session.scalars(select(Vendor)) if vendor_key(v.name) == key), None)
    created = vendor is None
    if created:
        vendor = Vendor(name=vendor_name, created_by_id=None)
        session.add(vendor)
    if vendor.price_list_updated_at is None or vendor.price_list_updated_at < list_date:
        vendor.price_list_updated_at = list_date
    session.flush()
    return vendor, created


def decide_warehouse(from_filename: str | None, from_text: str | None,
                     override: str | None) -> tuple[Warehouse, WarehouseSource]:
    if override:
        return Warehouse(override), WarehouseSource.MANUAL
    if from_filename:
        return Warehouse(from_filename), WarehouseSource.FILENAME
    if from_text:
        return Warehouse(from_text), WarehouseSource.TEXT
    return Warehouse.CHINA, WarehouseSource.ASSUMED


def store_price_list(session: Session, filename: str, data: PriceListData, *,
                     prefix_table: dict[str, PrefixName], warehouse_override: str | None = None,
                     dry_run: bool = False) -> ImportReport:
    report = ImportReport(filename=filename, rows=len(data.rows))
    try:
        info = parse_filename(filename)
    except ValueError as exc:
        report.skipped = str(exc)
        return report
    if not data.rows:
        report.skipped = "no price rows found (a scanned PDF needs OCR)"
        return report

    cards = session.scalars(select(Peptide)).all()
    rows = data.rows
    propagate_names(rows)
    repair_names(rows, prefix_table, lambda name: match_name(name, cards) is not None)

    vendor, report.vendor_created = resolve_vendor(session, info.vendor, info.list_date)
    warehouse, source = decide_warehouse(info.warehouse, data.warehouse_hint, warehouse_override)
    report.vendor_name, report.warehouse, report.warehouse_source = info.vendor, warehouse.value, source.value

    existing = session.scalar(select(PriceList).where(PriceList.source_filename == filename))
    if existing is not None:
        session.delete(existing)
        session.flush()
    plist = PriceList(vendor_name=info.vendor, vendor_id=vendor.id, warehouse=warehouse, warehouse_source=source,
                      list_date=info.list_date, source_filename=filename, shipping_note=data.shipping_note)
    session.add(plist)

    sizes: dict[int, tuple[Peptide, list[str]]] = {}
    unmatched: dict[str, None] = {}
    for row in rows:
        match = match_name(row.name, cards) if row.name else None
        peptide = None
        if match is not None:
            peptide = min(match.cards, key=lambda c: (_SOURCE_RANK.get(c.source, 9), c.id))
            report.matched += 1
            if row.spec.unit in _SPEC_UNITS:
                for card in match.cards:
                    sizes.setdefault(card.id, (card, []))[1].append(format_spec(row.spec.amount, row.spec.unit))
        elif row.name:
            unmatched[row.name] = None
        if not row.name:
            row.flags.append("no-name")
        if row.flags:
            report.flagged.append(f"{row.code or '-'} {row.name or '-'}: {', '.join(row.flags)}")
        plist.items.append(PriceListItem(
            code=row.code[:30] if row.code else None, product_name=row.name[:300] if row.name else None,
            peptide_id=peptide.id if peptide else None, vial_amount=row.spec.amount, vial_unit=row.spec.unit,
            pack_size=row.spec.pack_size, pack_price=row.pack_price, pack_type=pack_type(row.spec.pack_size),
            extra_prices=row.extra_prices or None, flags=row.flags or None))
    report.unmatched = list(unmatched)

    for card, specs in sizes.values():
        before = card.library_specifications or ""
        merged = merge_specs(before, *specs)
        if merged != before:
            card.library_specifications = merged
            report.specs_added += 1

    session.flush()
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return report

# app/library/price_lists/importer.py
"""Store a parsed price list: its vendor, warehouse, product lines, and the sizes added to library cards."""

import re
import unicodedata
from dataclasses import dataclass, field
from collections.abc import Callable
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.matching import match_name, name_key
from app.library.price_lists.filename import FileInfo, parse_filename
from app.library.price_lists.names import PrefixName, learn_prefixes, propagate_names, repair_names
from app.library.price_lists.reader import PriceListData, read_pdf
from app.library.price_lists.rows import code_prefix, pack_type
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
    unread_spec_lines: int = 0  # product-looking text lines the reader could not turn into a row
    skipped: str | None = None
    list_id: int | None = None  # the stored list, once it is written


def vendor_key(name: str) -> str:
    """A vendor's comparison form: letters and digits only, without the words "peptide"/"peptides". A vendor
    named only that keeps the word, singular, so "Peptide" and "Peptides" are one vendor."""
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", name).casefold())
    kept = [w for w in words if w not in _GENERIC_VENDOR_WORDS]
    return "".join(kept or ["peptide" if w in _GENERIC_VENDOR_WORDS else w for w in words]) or name_key(name)


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
    # Lists orphaned when an earlier vendor of this name was deleted belong to this vendor again.
    for orphan in session.scalars(select(PriceList).where(PriceList.vendor_id.is_(None))):
        if vendor_key(orphan.vendor_name) == key:
            orphan.vendor_id = vendor.id
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
                     dry_run: bool = False, vendor: Vendor | None = None,
                     list_date: date | None = None) -> ImportReport:
    """Store one parsed list. The vendor, warehouse and date come from the filename unless a `vendor` and `list_date`
    are given (a list uploaded on that vendor's page), in which case the filename is only a unique label."""
    report = ImportReport(filename=filename, rows=len(data.rows), unread_spec_lines=data.unread_spec_lines)
    if vendor is not None:
        info = FileInfo(vendor.name, None, list_date)
    else:
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

    if vendor is None:
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
    report.list_id = plist.id
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return report


def import_for_vendor(session: Session, vendor: Vendor, data: PriceListData, *, warehouse: str,
                      list_date: date) -> ImportReport:
    """Store a list read from a file uploaded on `vendor`'s page, for the stated warehouse ("us" or "china") and date.
    Importing the same vendor, warehouse and date again replaces the earlier import."""
    filename = f"{vendor.name} - {'US' if warehouse == 'us' else 'China'} Price List - {list_date.isoformat()}.pdf"
    propagate_names(data.rows)
    batch = [(vendor_key(vendor.name), code_prefix(r.code), r.name) for r in data.rows if r.name and code_prefix(r.code)]
    table = learn_prefixes(observations_from_db(session, (filename,)) + batch)
    return store_price_list(session, filename, data, prefix_table=table, warehouse_override=warehouse,
                            vendor=vendor, list_date=list_date)


def summarize_list(plist: PriceList) -> dict:
    """What an import stored, for the page that follows it: counts, and the product names no library card matched."""
    unmatched = list(dict.fromkeys(i.product_name for i in plist.items if i.product_name and i.peptide_id is None))
    return {"warehouse": plist.warehouse.value, "list_date": plist.list_date, "products": len(plist.items),
            "matched": sum(1 for i in plist.items if i.peptide_id is not None), "unmatched": unmatched,
            "flagged": sum(1 for i in plist.items if set(i.flags or ()) - {"no-code"})}  # a list with no code column is normal


def observations_from_db(session: Session, exclude_filenames=()) -> list[tuple[str, str, str]]:
    """(vendor_key, code_prefix, product_name) from lists already stored, for learning what a code means."""
    stmt = (select(PriceList.vendor_name, PriceListItem.code, PriceListItem.product_name, PriceListItem.flags)
            .join(PriceListItem, PriceListItem.price_list_id == PriceList.id)
            .where(PriceListItem.code.is_not(None), PriceListItem.product_name.is_not(None),
                   PriceList.source_filename.not_in(list(exclude_filenames))))
    return [(vendor_key(vendor), code_prefix(code), name)
            for vendor, code, name, flags in session.execute(stmt)
            if code_prefix(code) and "name-from-code" not in (flags or [])]  # a repaired name must not confirm itself


def _files(paths) -> list[Path]:
    files: list[Path] = []
    for path in map(Path, paths):
        files.extend(sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path])
    return files


def run_import(paths, session_factory, *, warehouse: str | None = None, dry_run: bool = False,
               reader: Callable[[Path], PriceListData] = read_pdf) -> list[ImportReport]:
    """Import PDFs (or the PDFs in a folder). Every file is read first so what a short code means can be learned
    across vendors, then each file is stored in its own transaction; one bad file never stops the others."""
    reports: list[ImportReport] = []
    parsed: dict[str, PriceListData] = {}
    for path in _files(paths):
        if path.suffix.lower() != ".pdf":
            reports.append(ImportReport(
                filename=path.name, skipped="not a PDF (scans need OCR; spreadsheets are not supported yet)"))
            continue
        try:
            parsed[path.name] = reader(path)
        except Exception as exc:  # a corrupt file is reported, not fatal
            reports.append(ImportReport(filename=path.name, skipped=f"could not read: {exc}"))

    batch: list[tuple[str, str, str]] = []
    for name, data in parsed.items():
        propagate_names(data.rows)
        try:
            vendor = vendor_key(parse_filename(name).vendor)
        except ValueError:
            continue
        batch += [(vendor, code_prefix(r.code), r.name) for r in data.rows if r.name and code_prefix(r.code)]
    with session_factory() as session:
        table = learn_prefixes(observations_from_db(session, parsed) + batch)

    for name, data in parsed.items():
        with session_factory() as session:
            try:
                reports.append(store_price_list(session, name, data, prefix_table=table,
                                                warehouse_override=warehouse, dry_run=dry_run))
            except Exception as exc:
                session.rollback()
                reports.append(ImportReport(filename=name, skipped=f"failed: {exc}"))
    return reports


def format_reports(reports: list[ImportReport], *, dry_run: bool = False) -> str:
    lines = ["DRY RUN - nothing was written", ""] if dry_run else []
    assumed = []
    for r in reports:
        lines.append(r.filename)
        if r.skipped:
            lines += [f"  skipped: {r.skipped}", ""]
            continue
        vendor = f"{r.vendor_name} ({'new' if r.vendor_created else 'existing'})"
        lines.append(f"  vendor: {vendor} | warehouse: {r.warehouse} ({r.warehouse_source}) | rows: {r.rows} | "
                     f"matched: {r.matched} | library cards given new sizes: {r.specs_added}")
        if r.unread_spec_lines:
            lines.append(f"  {r.unread_spec_lines} spec lines were not read (check this PDF)")
        if r.flagged:
            lines.append(f"  flagged ({len(r.flagged)}):")
            lines += [f"    {item}" for item in r.flagged]
        if r.unmatched:
            lines.append(f"  products with no library card ({len(r.unmatched)}): {', '.join(r.unmatched)}")
        lines.append("")
        if r.warehouse_source == "assumed":
            assumed.append(r.filename)
    if assumed:
        lines += ["Warehouse assumed (China) for: " + "; ".join(assumed),
                  "  If any are really US warehouses, re-import that file with --warehouse us."]
    return "\n".join(lines).rstrip() + "\n"

# app/library/price_lists/reader.py
"""Read price-list PDFs into plain rows.

Most vendors publish a ruled table: [code | name | spec | price ...] in some column order, with the name in
a merged cell. Column roles are inferred from what the cells contain, not from header words, so the
different vendor layouts all go through one path. A page with no usable table (a plain text list) falls back to
reading text lines.
"""

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import pdfplumber

from app.library.price_lists.rows import (
    ParsedRow, Spec, check_code_size, check_pack_size, parse_price, parse_spec,
)

_SPEC_CELL = re.compile(r"\d\s*(?:mcg|mg|ug|iu|ml)[a-z]*(?:\s*/\s*ml)?\s*[*x×]\s*\d", re.IGNORECASE)
_PRICE_CELL = re.compile(r"^\s*\$?\s*[\d,]+(?:\.\d+)?(?:\s*/\s*\d+\s*vials?)?\s*$", re.IGNORECASE)
_CODE_CELL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/() ]{0,17}$")
_NAME_CODE = re.compile(r"^([A-Za-z0-9\-]{2,12})\s*\(")
_LINE_SPEC = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mcg|mg|ug|iu|ml)[a-z]*(?:\s*/\s*ml)?\s*[*x×]\s*\d+(?:\s*v[a-z]*)?", re.IGNORECASE)
_LINE_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/()]{1,13}$")
_SHIPPING = re.compile(r"ship|freight|customs|postage", re.IGNORECASE)
_MIN_ROLE_CELLS = 3
_MAX_NOTES = 6


@dataclass(frozen=True)
class Roles:
    spec: int
    prices: tuple[int, ...]
    code: int | None
    name: int | None


@dataclass
class PriceListData:
    rows: list[ParsedRow]
    shipping_note: str | None = None
    warehouse_hint: str | None = None  # "us" | "china" from text such as "Chinese Warehouse"


def _clean(cell) -> str:
    return re.sub(r"\s+", " ", cell or "").strip()


def infer_roles(table) -> Roles | None:
    width = max((len(r) for r in table), default=0)
    cols = [[_clean(r[i]) if i < len(r) else "" for r in table] for i in range(width)]
    spec_hits = [sum(1 for c in col if _SPEC_CELL.search(c)) for col in cols]
    if not spec_hits or max(spec_hits) < _MIN_ROLE_CELLS:
        return None
    spec = spec_hits.index(max(spec_hits))
    prices = tuple(
        i for i in range(spec + 1, width) if sum(1 for c in cols[i] if _PRICE_CELL.match(c)) >= _MIN_ROLE_CELLS)

    left = list(range(spec))
    code_scores = {
        i: sum(1 for c in cols[i] if _CODE_CELL.match(c) and re.search(r"\d", c) and not _SPEC_CELL.search(c))
        for i in left
    }
    code = max(code_scores, key=code_scores.get) if code_scores and max(code_scores.values()) >= _MIN_ROLE_CELLS else None

    def text_cells(i: int) -> int:
        return sum(1 for c in cols[i] if re.search(r"[A-Za-z]{3}", c))

    candidates = [i for i in left if i != code and text_cells(i) >= 1]
    if not candidates:
        name = None
    elif code is None:
        name = max(candidates, key=text_cells)
    else:  # the name sits next to the code (before it or after it); a category column is further away
        name = min(candidates, key=lambda i: (abs(i - code), -text_cells(i)))
    return Roles(spec, prices, code, name)


def _price_labels(table, roles: Roles) -> list[str]:
    labels = []
    for k, i in enumerate(roles.prices):
        header = next(
            (c for raw in table if i < len(raw) and (c := _clean(raw[i])) and not _PRICE_CELL.match(c)), None)
        labels.append(header or f"price_{k + 1}")
    return labels


def rows_from_table(table, page: int = 0) -> list[ParsedRow]:
    roles = infer_roles(table)
    if roles is None:
        return []
    labels = _price_labels(table, roles)
    rows = []
    for raw in table:
        def cell(i):
            return _clean(raw[i]) if i is not None and i < len(raw) else ""

        spec = parse_spec(cell(roles.spec))
        if spec is None:
            continue
        name = cell(roles.name) or None
        code = cell(roles.code)
        code = code if re.search(r"[A-Za-z0-9]", code) else None
        if code is None and name:
            m = _NAME_CODE.match(name)
            if m and re.search(r"\d", m.group(1)):
                code = m.group(1)
        price = None
        if roles.prices:
            price, per_pack = parse_price(cell(roles.prices[0]))
            if per_pack is not None:
                spec = replace(spec, pack_size=per_pack)
        extras = {}
        for label, i in zip(labels[1:], roles.prices[1:]):
            value, _ = parse_price(cell(i))
            if value is not None:
                extras[label] = value
        row = ParsedRow(code=code, name=name, spec=spec, pack_price=price, page=page, extra_prices=extras)
        if code is None:
            row.flags.append("no-code")
        check_code_size(row)
        check_pack_size(row)
        rows.append(row)
    return rows


def rows_from_lines(lines, page: int = 0) -> list[ParsedRow]:
    """Fallback for pages with no table: "<name> <code> <spec> <price>" on one line."""
    rows = []
    for line in lines:
        m = _LINE_SPEC.search(line)
        if m is None:
            continue
        spec = parse_spec(m.group(0))
        if spec is None:
            continue
        before = line[:m.start()].split()
        at = next((i for i in range(len(before) - 1, -1, -1)
                   if _LINE_CODE.match(before[i]) and re.search(r"\d", before[i])), None)
        code = before.pop(at) if at is not None else None
        after = line[m.end():].split()
        price, per_pack = parse_price(after[0]) if after else (None, None)
        if per_pack is not None:
            spec = replace(spec, pack_size=per_pack)
        row = ParsedRow(code=code, name=" ".join(before) or None, spec=spec, pack_price=price, page=page)
        if code is None:
            row.flags.append("no-code")
        check_code_size(row)
        check_pack_size(row)
        rows.append(row)
    return rows


def scan_notes(lines) -> tuple[str | None, str | None]:
    """(shipping note, warehouse hint) from a list's text. Product lines are never notes."""
    notes: list[str] = []
    hint = None
    for raw in lines:
        line = _clean(raw)
        if not line or _LINE_SPEC.search(line):
            continue
        if _SHIPPING.search(line) and line not in notes and len(notes) < _MAX_NOTES:
            notes.append(line[:200])
        if hint is None and re.search(r"warehouse", line, re.IGNORECASE):
            if re.search(r"\b(china|chinese)\b", line, re.IGNORECASE):
                hint = "china"
            elif re.search(r"\b(usa|us|u\.s\.|united states)\b", line, re.IGNORECASE):
                hint = "us"
    return ("\n".join(notes) or None), hint


def read_pdf(path: Path) -> PriceListData:
    rows: list[ParsedRow] = []
    lines: list[str] = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            page_lines = (page.extract_text() or "").splitlines()
            lines.extend(page_lines)
            page_rows = [r for table in page.extract_tables() for r in rows_from_table(table, number)]
            rows.extend(page_rows or rows_from_lines(page_lines, number))
    note, hint = scan_notes(lines)
    return PriceListData(rows=rows, shipping_note=note, warehouse_hint=hint)

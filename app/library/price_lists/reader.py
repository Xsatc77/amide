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

from app.library.price_lists import ocr
from app.library.price_lists.rows import (
    ParsedRow, Spec, check_code_size, check_pack_size, code_number, parse_price, parse_spec,
)

_SPEC_CELL = re.compile(r"\d\s*(?:mcg|mg|ug|iu|ml)[a-z]*(?:\s*/\s*ml)?\s*[*x×]\s*\d", re.IGNORECASE)
_BARE_SPEC_CELL = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:mcg|mg|ug|iu|ml)\s*$", re.IGNORECASE)  # "5mg": the pack is stated in the heading
_PER_KIT = re.compile(r"\bkit\b", re.IGNORECASE)
_PRICE_CELL = re.compile(
    r"^\s*(?:US\$|\$|USD)?\s*[\d,]+(?:\.\d+)?\s*(?:USD)?(?:\s*/\s*\d*\s*vials?)?\s*$", re.IGNORECASE)
_CODE_CELL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/() ]{0,17}$")
_NAME_CODE = re.compile(r"^([A-Za-z0-9\-]{2,12})\s*\(")
_LINE_SPEC = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mcg|mg|ug|iu|ml)[a-z]*(?:\s*/\s*ml)?\s*[*x×]\s*\d+(?:\s*v[a-z]*)?", re.IGNORECASE)
_LINE_CODE = re.compile(  # 5AM, 10AM, 2S10, SM10, SX5-XA5; never a name such as CJC-1295 or 5-amino-1MQ
    r"^(?:\d{1,3}[A-Za-z]{1,6}\d{0,5}|[A-Za-z]{1,6}\d{1,5})(?:-[A-Za-z]{1,6}\d{1,5})?(?:\([^)]*\))?$")
_SHIPPING = re.compile(r"ship|freight|customs|postage", re.IGNORECASE)
_MIN_ROLE_CELLS = 2
_KIT_VIALS = 10
_MIN_CODE_AMOUNT_MATCH = 0.4  # codes embed the dose (RT10 = 10mg); names with digits (BPC-157) rarely do
_NON_PRICE_HEADER = re.compile(r"moq|qty|quantity|stock|min\.? ?order|pcs|pieces|weight", re.IGNORECASE)
_PRICE_HEADER = re.compile(r"price|usd|cost|rate|\$", re.IGNORECASE)
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
    unread_spec_lines: int = 0  # text lines that look like products but produced no row


def _clean(cell) -> str:
    return re.sub(r"\s+", " ", cell or "").strip()


def _price_columns(cols: list[list[str]], spec: int) -> tuple[int, ...]:
    """Numeric columns right of the spec, base price first. A column headed MOQ / stock / weight is not a price;
    a column headed "price" or holding "$" values is the base price when several columns are numeric."""
    def header(i: int) -> str:
        return next((c for c in cols[i] if c and not _PRICE_CELL.match(c)), "")

    numeric = [i for i in range(spec + 1, len(cols))
               if sum(1 for c in cols[i] if _PRICE_CELL.match(c)) >= _MIN_ROLE_CELLS
               and not _NON_PRICE_HEADER.search(header(i))]
    named = [i for i in numeric if _PRICE_HEADER.search(header(i))]
    if named:
        return tuple(named + [i for i in numeric if i not in named])
    dollar = [i for i in numeric if sum(1 for c in cols[i] if "$" in c) * 2 >= sum(1 for c in cols[i] if c)]
    return tuple(dollar or numeric)


def infer_roles(table) -> Roles | None:
    width = max((len(r) for r in table), default=0)
    cols = [[_clean(r[i]) if i < len(r) else "" for r in table] for i in range(width)]
    spec_hits = [sum(1 for c in col if _SPEC_CELL.search(c)) for col in cols]
    if (not spec_hits or max(spec_hits) < _MIN_ROLE_CELLS) and any(_PER_KIT.search(c) for col in cols for c in col):
        spec_hits = [sum(1 for c in col if _BARE_SPEC_CELL.match(c)) for col in cols]  # a "price / kit" list gives bare doses
    if not spec_hits or max(spec_hits) < _MIN_ROLE_CELLS:
        return None
    spec = spec_hits.index(max(spec_hits))
    specs = [parse_spec(c) for c in cols[spec]]
    prices = _price_columns(cols, spec)

    def code_score(i: int) -> int:
        return sum(1 for c in cols[i] if _CODE_CELL.match(c) and re.search(r"\d", c) and not _SPEC_CELL.search(c))

    def dose_match(i: int) -> float:
        pairs = [(code_number(c), sp.amount) for c, sp in zip(cols[i], specs)
                 if sp is not None and code_number(c) is not None]
        return sum(1 for n, amount in pairs if n == amount) / len(pairs) if pairs else 0.0

    left = list(range(spec))
    code_scores = {i: code_score(i) for i in left
                   if code_score(i) >= _MIN_ROLE_CELLS and dose_match(i) >= _MIN_CODE_AMOUNT_MATCH}
    code = max(code_scores, key=code_scores.get) if code_scores else None

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
    per_kit = bool(labels) and _PER_KIT.search(labels[0]) is not None
    rows = []
    for raw in table:
        def cell(i):
            return _clean(raw[i]) if i is not None and i < len(raw) else ""

        spec = parse_spec(cell(roles.spec))
        if spec is None:
            continue
        if per_kit and spec.pack_size is None:
            spec = replace(spec, pack_size=_KIT_VIALS)
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
        if price is None:
            row.flags.append("no-price")
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
        shaped = [i for i, tok in enumerate(before) if _LINE_CODE.match(tok)]
        matching_dose = [i for i in shaped if code_number(before[i]) == spec.amount]
        at = (matching_dose or shaped or [None])[-1]
        code = before.pop(at) if at is not None else None
        after = line[m.end():].split()
        price, per_pack = parse_price(after[0]) if after else (None, None)
        if per_pack is not None:
            spec = replace(spec, pack_size=per_pack)
        row = ParsedRow(code=code, name=" ".join(before) or None, spec=spec, pack_price=price, page=page)
        if code is None:
            row.flags.append("no-code")
        if price is None:
            row.flags.append("no-price")
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


def unread_spec_lines(lines, rows) -> int:
    """Text lines that look like a product (they carry a spec) beyond the rows actually read from the page."""
    return max(0, sum(1 for line in lines if _LINE_SPEC.search(line)) - len(rows))


def read_pdf(path: Path, recognize=None) -> PriceListData:
    """Read a text PDF. A page that is only a picture (a scan) is read by text recognition; `recognize` replaces the
    recognizer (tests)."""
    rows: list[ParsedRow] = []
    lines: list[str] = []
    unread = 0
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            if not page.chars and page.images:
                words = ocr.page_words(path, number - 1, recognize)
                page_lines, page_rows = ocr.rows_from_words(words, number)
            else:
                page_lines = (page.extract_text() or "").splitlines()
                page_rows = [r for table in page.extract_tables() for r in rows_from_table(table, number)]
            lines.extend(page_lines)
            used = page_rows or rows_from_lines(page_lines, number)
            rows.extend(used)
            unread += unread_spec_lines(page_lines, used)
    note, hint = scan_notes(lines)
    return PriceListData(rows=rows, shipping_note=note, warehouse_hint=hint, unread_spec_lines=unread)

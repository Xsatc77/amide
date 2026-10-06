# Vendor Price List Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import vendor price-list PDFs: create each file's vendor from its filename, store every product line (code, product, vial size, pack size, pack price, whether it is a kit or a box, extra price columns) with the warehouse region, and add the vial sizes to matching library cards.

**Architecture:** A new package `app/library/price_lists/` holds small, separately tested units: filename parsing, pure row parsers, a cell-based PDF reader (column roles inferred from cell contents; a line fallback for PDFs without table lines), name repair using short codes learned across vendors, and an importer that writes two new tables. The earlier single-layout table parser and its tool are replaced. Product names go through the existing `app/library/matching.py`.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pdfplumber (already in `requirements.txt`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-price-list-import-design.md`

## Global Constraints

- Run tests with `.venv/Scripts/python.exe -m pytest -q -p no:warnings <paths>` from `C:\tmp\amide`.
- Commit trailer required by this session: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Vendor PDFs are never added to the repository; tests build rows and tables by hand.
- The importer never creates a library `Peptide`. It only reads cards, links rows to them, and adds sizes to `library_specifications`.
- Filename convention: `<Vendor> - [<Warehouse> ]Price List[ NEW] - <YYYY-MM-DD>[-NN].<ext>`; vendor is the first ` - ` segment, date the last, warehouse an optional `China`/`Chinese`/`USA`/`US`/`United States` word in the middle.
- Warehouse is `us` or `china` only. Precedence: `--warehouse` (source `manual`) > filename (`filename`) > list text containing "warehouse" (`text`) > **`china`, source `assumed`**.
- A vendor is reused when `vendor_key` matches: lowercase letters/digits with the words `peptide` and `peptides` removed (`Zephyr` reuses `Zephyr Peptides`). Otherwise it is created with the filename's spelling, `created_by_id=None`. `Vendor.price_list_updated_at` only moves forward.
- Vial unit strings stored: `mg`, `mcg`, `IU`, `ml`, `mg/ml` (concentration, e.g. oils `250mg/ml*1vials`). Library specs are added only for `mg`, `mcg`, `IU`, formatted `f"{amount:g}{unit}"` and merged with `merge_specs`.
- **Kit and box (owner's vocabulary):** a *kit* is always exactly 10 vials; anything smaller is a *box*. The generic word in code is *pack*: `pack_size` (vials in the pack), `pack_price` (what the list charges for it), `pack_type` (`"kit"` for 10, `"box"` for fewer than 10, `None` when the list states no size or states more than 10, which is also flagged `unusual-pack-size`). Per-vial cost is `pack_price / pack_size`.
- `pack_size` comes from the list (`*10vials` -> 10, `*1vials` or `$30/1vial` -> 1, none stated -> `None`); it is never assumed.
- Code prefix = the leading letters after optional leading digits, uppercased (`RT10`->`RT`, `2AD`->`AD`, `HCG5000(GK5)`->`HCG`, `G10K`->`G`).
- Re-importing a file replaces that list's items (keyed by `source_filename`). Vendor deletion leaves the list (`vendor_id` set null, `vendor_name` kept).
- Scans (images, any scanned PDF) and spreadsheets are skipped with a reported reason; nothing is guessed.

## Review Focus

The spec's own list, each pinned by a test named below.

- A multi-line wrapped product name lands on one row, not neighbors (Task 3: `test_wrapped_name_cell_stays_on_one_row`).
- A list with no warehouse in its name or text imports as `china` with source `assumed` (Task 6: `test_decide_warehouse_precedence`, `test_unnamed_warehouse_is_assumed_china`).
- Re-importing the same file leaves one list and one set of items (Task 6: `test_reimport_replaces_the_list_instead_of_duplicating`).
- One vendor's China and USA lists share one vendor and are two lists with different warehouses (Task 6: `test_one_vendor_can_have_a_china_and_a_usa_list`).
- `$30/1vial` stores pack price 30, pack size 1, and type `box` (Task 2: `test_parse_price`; Task 3: `test_per_vial_price_makes_a_one_vial_box`; Task 6: `test_pack_type_is_kit_for_ten_vials_and_box_for_fewer`).
- A pack of more than 10 vials is flagged `unusual-pack-size` and typed `None`, never called a kit (Task 2: `test_unusual_pack_sizes_are_flagged`).
- A code with no name anywhere imports with a null name and a flag, not a crash (Task 4: `test_repair_fills_blank_names`; Task 6: `test_row_with_no_name_is_stored_and_flagged`).
- Water and acetic acid lines are stored, matched to no card, and add no specs (Task 6: `test_liquids_are_stored_but_add_no_specs`).
- `with`/`without` variants never cross-annotate library cards (Task 6: `test_with_and_without_variants_stay_separate`).
- A newer-dated list never moves a vendor's "updated" date backward (Task 6: `test_vendor_date_only_moves_forward`).

## File structure

- Create `app/library/price_lists/__init__.py` (empty).
- Create `app/library/price_lists/filename.py`: `FileInfo`, `parse_filename`.
- Create `app/library/price_lists/specs.py`: `merge_specs`, `format_spec` (moved from the old parser).
- Create `app/library/price_lists/rows.py`: `Spec`, `ParsedRow`, `parse_spec`, `parse_price`, `code_prefix`, `code_number`, `check_code_size`, `pack_type`, `check_pack_size`.
- Create `app/library/price_lists/reader.py`: `Roles`, `infer_roles`, `rows_from_table`, `rows_from_lines`, `scan_notes`, `PriceListData`, `read_pdf`.
- Create `app/library/price_lists/names.py`: `propagate_names`, `PrefixName`, `learn_prefixes`, `repair_names`.
- Create `app/library/price_lists/importer.py`: `vendor_key`, `resolve_vendor`, `decide_warehouse`, `ImportReport`, `store_price_list`, `observations_from_db`, `run_import`, `format_reports`.
- Create `tools/import_price_lists.py`: thin command line.
- Create `migrations/versions/0031_price_lists.py`; modify `app/models.py` (`Warehouse`, `WarehouseSource`, `PriceList`, `PriceListItem`); modify `tests/conftest.py` (cleanup).
- Delete `app/library/price_list_parser.py`, `app/library/populate_library_specs.py`, `tools/populate_price_list.py`, `tests/test_library_populate.py`.
- Tests: `tests/test_price_list_filename.py`, `tests/test_price_list_rows.py`, `tests/test_price_list_reader.py`, `tests/test_price_list_names.py`, `tests/test_price_list_importer.py`.

---

### Task 1: Filename parsing

**Files:**
- Create: `app/library/price_lists/__init__.py`, `app/library/price_lists/filename.py`
- Test: `tests/test_price_list_filename.py`

**Interfaces:**
- Produces: `FileInfo(vendor: str, warehouse: str | None, list_date: date)` (frozen dataclass; `warehouse` is `"us"`, `"china"` or `None` when the filename names none); `parse_filename(name: str) -> FileInfo`, raising `ValueError` when there is no vendor or no date.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_price_list_filename.py
from datetime import date

import pytest

from app.library.price_lists.filename import FileInfo, parse_filename

FILENAME_SHAPES = [  # invented vendors; every shape the owner's real filenames take
    ("Acme Labs - Price List - 2026-08-17-01 .jpg", "Acme Labs", None, date(2026, 8, 17)),  # page suffix, space
    ("Borealis - China Price List - 2026-08-24.pdf", "Borealis", "china", date(2026, 8, 24)),
    ("Borealis - USA Price List - 2026-08-31.pdf", "Borealis", "us", date(2026, 8, 31)),
    ("Cobalt Bio Labs - Price List - 2026-08-31.pdf", "Cobalt Bio Labs", None, date(2026, 8, 31)),
    ("Delta - Price List - 2026-09-15.pdf", "Delta", None, date(2026, 9, 15)),
    ("Delta Peptides - China Price List - 2026-08-18.pdf", "Delta Peptides", "china", date(2026, 8, 18)),
    ("Evergreen - Pricelist - 2026-10-03.pdf", "Evergreen", None, date(2026, 10, 3)),  # "Pricelist", one word
    ("Evergreen - USA Price List - 2026-10-03-01.jpg", "Evergreen", "us", date(2026, 10, 3)),
    ("Evergreen - USA Price List - 2026-10-03-02.jpg.jpg", "Evergreen", "us", date(2026, 10, 3)),  # doubled ext
    ("Fjord Peptide - Price list - 2026-09-10.pdf", "Fjord Peptide", None, date(2026, 9, 10)),
    ("Garnet Biolabs - price list - 2026-09-19.pdf", "Garnet Biolabs", None, date(2026, 9, 19)),
    ("Harbor Peptide - Price List - 2026-09-14.xlsx", "Harbor Peptide", None, date(2026, 9, 14)),
    ("Ivory - Price List NEW - 2026-05-19.pdf", "Ivory", None, date(2026, 5, 19)),
]


@pytest.mark.parametrize("name, vendor, warehouse, when", FILENAME_SHAPES)
def test_every_filename_shape_parses(name, vendor, warehouse, when):
    assert parse_filename(name) == FileInfo(vendor, warehouse, when)


def test_warehouse_words_are_whole_word_and_case_insensitive():
    assert parse_filename("X - china price list - 2026-01-02.pdf").warehouse == "china"
    assert parse_filename("X - Chinese Warehouse Price List - 2026-01-02.pdf").warehouse == "china"
    assert parse_filename("X - United States Price List - 2026-01-02.pdf").warehouse == "us"
    assert parse_filename("X - Focus Price List - 2026-01-02.pdf").warehouse is None  # "us" inside "Focus"


@pytest.mark.parametrize("name", [
    "Price List.pdf",                       # no vendor segment, no date
    "Acme - Price List.pdf",                # no date
    " - Price List - 2026-01-01.pdf",       # empty vendor
    "Acme - Price List - 2026-13-45.pdf",   # not a real date
])
def test_unusable_filenames_are_rejected(name):
    with pytest.raises(ValueError):
        parse_filename(name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_filename.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'app.library.price_lists'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/library/price_lists/__init__.py` as an empty file, then:

```python
# app/library/price_lists/filename.py
"""Vendor, warehouse and date from a price-list filename: "<Vendor> - [<Warehouse> ]Price List - <date>"."""

import re
from dataclasses import dataclass
from datetime import date

_EXTENSIONS = re.compile(r"(\.[A-Za-z0-9]{2,4})+\s*$")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:-\d+)?")  # a trailing -NN is a page suffix
_CHINA = re.compile(r"\b(china|chinese)\b", re.IGNORECASE)
_US = re.compile(r"\b(usa|us|u\.s\.|united states)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FileInfo:
    vendor: str
    warehouse: str | None  # "us" | "china" | None when the filename names none
    list_date: date


def parse_filename(name: str) -> FileInfo:
    stem = _EXTENSIONS.sub("", name)  # not stripped first: a leading " - " must leave an empty vendor
    parts = [part.strip() for part in stem.split(" - ")]
    if len(parts) < 2:
        raise ValueError(f"no date in {name!r}")
    match = _DATE.fullmatch(parts[-1])
    if match is None:
        raise ValueError(f"no date in {name!r}")
    vendor = parts[0]
    if not vendor:
        raise ValueError(f"no vendor in {name!r}")
    list_date = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    middle = " ".join(parts[1:-1])
    warehouse = "china" if _CHINA.search(middle) else "us" if _US.search(middle) else None
    return FileInfo(vendor, warehouse, list_date)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_filename.py`
Expected: all pass (23 tests).

- [ ] **Step 5: Commit**

```bash
git add app/library/price_lists tests/test_price_list_filename.py
git commit -m "feat: parse vendor, warehouse and date from price-list filenames"
```

---

### Task 2: Row primitives and spec strings

**Files:**
- Create: `app/library/price_lists/specs.py`, `app/library/price_lists/rows.py`
- Test: `tests/test_price_list_rows.py`

**Interfaces:**
- Produces (`specs.py`): `merge_specs(*spec_lists: str) -> str` (union of comma-separated specs, sorted by unit then amount; `"5mg, 10mg"`), `format_spec(amount: float, unit: str) -> str`.
- Produces (`rows.py`): `Spec(amount: float, unit: str, pack_size: int | None)` frozen dataclass; `ParsedRow(code, name, spec, pack_price, page=0, extra_prices={}, flags=[])` (mutable dataclass); `parse_spec(text) -> Spec | None`; `parse_price(text) -> tuple[float | None, int | None]` (price, vial count when written `$30/1vial`); `code_prefix(code) -> str | None`; `code_number(code) -> float | None`; `check_code_size(row) -> None` (appends flag `"code-size-mismatch"`); `pack_type(size: int | None) -> str | None` (`"kit"` for 10, `"box"` for 1-9, else `None`); `check_pack_size(row) -> None` (appends flag `"unusual-pack-size"` when the size is above 10).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_price_list_rows.py
import pytest

from app.library.price_lists.rows import (
    ParsedRow, Spec, check_code_size, check_pack_size, code_number, code_prefix, pack_type, parse_price, parse_spec,
)
from app.library.price_lists.specs import format_spec, merge_specs


@pytest.mark.parametrize("text, expected", [
    ("5mg*10vials", Spec(5, "mg", 10)),
    ("10mg x 10 vials", Spec(10, "mg", 10)),
    ("20mg *10vials", Spec(20, "mg", 10)),
    ("10mg *10", Spec(10, "mg", 10)),
    ("100IU*10vi", Spec(100, "IU", 10)),
    ("5000IUal*10via", Spec(5000, "IU", 10)),        # text overlap seen in a real PDF
    ("12iu*2", Spec(12, "IU", 2)),
    ("250mg/ml*1vials", Spec(250, "mg/ml", 1)),     # oil concentration, a one-vial box
    ("600mg/ml 10ml*10 vials", Spec(10, "ml", 10)),  # the sized spec wins over the concentration
    ("2.5mg*10vials", Spec(2.5, "mg", 10)),
    ("500mcg*10vials", Spec(500, "mcg", 10)),
    ("500ug*10vials", Spec(500, "mcg", 10)),
    ("100mg", Spec(100, "mg", None)),               # pack size not stated
    ("60mg *6 vials", Spec(60, "mg", 6)),            # fewer than 10 vials: a box, not a kit
    ("10ml*600mg/ml", Spec(10, "ml", None)),         # "*600mg/ml" is a concentration, not 600 vials
    ("10ml x 600mg/ml/via", Spec(10, "ml", None)),
])
def test_parse_spec(text, expected):
    assert parse_spec(text) == expected


@pytest.mark.parametrize("text", ["", "Retatrutide", "Specification"])
def test_parse_spec_rejects_non_specs(text):
    assert parse_spec(text) is None


@pytest.mark.parametrize("text, expected", [
    ("$45", (45.0, None)),
    ("40", (40.0, None)),
    ("$50.00", (50.0, None)),
    ("$ 65", (65.0, None)),
    ("$1,250", (1250.0, None)),
    ("$30/1vial", (30.0, 1)),
    ("", (None, None)),
    ("Instructions for Use (for reference only)", (None, None)),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("code, prefix, number", [
    ("RT10", "RT", 10.0),
    ("2AD", "AD", 2.0),
    ("HCG5000(GK5)", "HCG", 5000.0),
    ("G10K", "G", 10.0),
    ("BBG70/Glow 70", "BBG", 70.0),
    ("Botox", "BOTOX", None),
    ("*", None, None),
    ("", None, None),
    (None, None, None),
])
def test_code_prefix_and_number(code, prefix, number):
    assert code_prefix(code) == prefix
    assert code_number(code) == number


def row(code, amount, unit="mg"):
    return ParsedRow(code=code, name=None, spec=Spec(amount, unit, 10), pack_price=1.0)


def test_code_digits_matching_the_vial_size_are_not_flagged():
    r = row("RT10", 10)
    check_code_size(r)
    assert r.flags == []


def test_code_digits_differing_from_the_vial_size_are_flagged():
    r = row("RT10", 20)
    check_code_size(r)
    assert r.flags == ["code-size-mismatch"]


@pytest.mark.parametrize("size, expected", [
    (10, "kit"), (1, "box"), (2, "box"), (5, "box"), (6, "box"), (9, "box"),
    (None, None), (11, None), (20, None), (0, None),
])
def test_pack_type_a_kit_is_exactly_ten_vials_and_fewer_is_a_box(size, expected):
    assert pack_type(size) == expected


@pytest.mark.parametrize("size, flags", [(10, []), (5, []), (1, []), (None, []), (11, ["unusual-pack-size"]),
                                         (20, ["unusual-pack-size"])])
def test_unusual_pack_sizes_are_flagged(size, flags):
    r = ParsedRow(code="ZX10", name=None, spec=Spec(10, "mg", size), pack_price=1.0)
    check_pack_size(r)
    assert r.flags == flags


def test_no_code_or_a_liquid_unit_is_never_flagged():
    for r in (row(None, 10), row("AA10", 3, "ml"), row("Botox", 100, "IU")):
        check_code_size(r)
        assert r.flags == []


def test_merge_specs_unions_and_sorts_by_unit_then_amount():
    assert merge_specs("50mg", "5mg, 10mg", "5mg") == "5mg, 10mg, 50mg"
    assert merge_specs("12IU, 500mcg", "10mg") == "10mg, 500mcg, 12IU"
    assert merge_specs("", "") == ""


def test_format_spec_drops_trailing_zeros():
    assert format_spec(10.0, "mg") == "10mg"
    assert format_spec(2.5, "mg") == "2.5mg"
    assert format_spec(12.0, "IU") == "12IU"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_rows.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'app.library.price_lists.rows'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/library/price_lists/specs.py
"""Spec strings such as "5mg, 10mg" kept on a library card."""

import re


def _unit_order(spec: str) -> int:
    return 2 if "IU" in spec else 1 if "mcg" in spec else 0


def _amount(spec: str) -> float:
    match = re.match(r"[\d.]+", spec)
    return float(match.group()) if match else 0.0


def merge_specs(*spec_lists: str) -> str:
    """Union of comma-separated spec strings, sorted by unit (mg, mcg, IU) then amount."""
    specs = {s.strip() for spec_list in spec_lists for s in spec_list.split(",") if s.strip()}
    return ", ".join(sorted(specs, key=lambda s: (_unit_order(s), _amount(s))))


def format_spec(amount: float, unit: str) -> str:
    return f"{amount:g}{unit}"
```

```python
# app/library/price_lists/rows.py
"""Plain row objects and the small pure parsers behind the price-list reader."""

import re
from dataclasses import dataclass, field

_SPEC = re.compile(
    # a number right after "*" is a vial count unless a unit follows it ("10ml*600mg/ml": 600mg/ml is a concentration)
    r"(?P<amt>\d+(?:\.\d+)?)\s*(?P<unit>mcg|mg|ug|iu|ml)[a-z]*(?P<conc>\s*/\s*ml)?"
    r"\s*(?:[*x×]\s*(?P<size>\d+)(?!\d)(?!\s*(?:mcg|mg|ug|iu|ml)))?",
    re.IGNORECASE,
)
_PRICE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:/\s*(\d+)\s*vials?)?", re.IGNORECASE)
_CODE_PREFIX = re.compile(r"\d*([A-Za-z]+)")
_CHECKED_UNITS = {"mg", "mcg", "IU"}
_KIT_VIALS = 10


@dataclass(frozen=True)
class Spec:
    amount: float
    unit: str  # "mg" | "mcg" | "IU" | "ml" | "mg/ml"
    pack_size: int | None


@dataclass
class ParsedRow:
    code: str | None
    name: str | None
    spec: Spec
    pack_price: float | None
    page: int = 0
    extra_prices: dict[str, float] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def parse_spec(text: str) -> Spec | None:
    """"10mg*10vials" -> Spec(10, "mg", 10). A spec that states a pack size wins over one that doesn't."""
    matches = list(_SPEC.finditer(text or ""))
    if not matches:
        return None
    m = next((m for m in matches if m.group("size")), matches[0])
    unit = {"iu": "IU", "ug": "mcg"}.get(m.group("unit").lower(), m.group("unit").lower())
    if m.group("conc"):
        unit += "/ml"
    size = int(m.group("size")) if m.group("size") else None
    return Spec(float(m.group("amt")), unit, size)


def parse_price(text: str) -> tuple[float | None, int | None]:
    """"$45" -> (45.0, None); "$30/1vial" -> (30.0, 1); anything else -> (None, None)."""
    m = _PRICE.fullmatch((text or "").replace(",", "").strip())
    if m is None:
        return None, None
    return float(m.group(1)), (int(m.group(2)) if m.group(2) else None)


def code_prefix(code: str | None) -> str | None:
    """The product part of a code: "RT10" -> "RT", "2AD" -> "AD", "HCG5000(GK5)" -> "HCG"."""
    m = _CODE_PREFIX.match(code or "")
    return m.group(1).upper() if m else None


def code_number(code: str | None) -> float | None:
    m = re.search(r"\d+", code or "")
    return float(m.group()) if m else None


def check_code_size(row: ParsedRow) -> None:
    """Flag a code whose digits disagree with the vial size (RT10 listed as 20mg). A flag, not an error."""
    number = code_number(row.code)
    if number is not None and row.spec.unit in _CHECKED_UNITS and number != row.spec.amount:
        row.flags.append("code-size-mismatch")


def pack_type(size: int | None) -> str | None:
    """A kit is always exactly 10 vials; fewer is a box. No stated size, or more than 10, is neither."""
    if size == _KIT_VIALS:
        return "kit"
    if size is not None and 0 < size < _KIT_VIALS:
        return "box"
    return None


def check_pack_size(row: ParsedRow) -> None:
    """Flag a pack of more than 10 vials: it is not a kit (always 10) and not a box (fewer), so look at it."""
    size = row.spec.pack_size
    if size is not None and size > _KIT_VIALS:
        row.flags.append("unusual-pack-size")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_rows.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/library/price_lists/specs.py app/library/price_lists/rows.py tests/test_price_list_rows.py
git commit -m "feat: price-list row primitives (spec, price, short code) and spec merging"
```

---

### Task 3: PDF reader (cells, lines, notes)

**Files:**
- Create: `app/library/price_lists/reader.py`
- Test: `tests/test_price_list_reader.py`

**Interfaces:**
- Consumes (Task 2): `ParsedRow`, `Spec`, `check_code_size`, `check_pack_size`, `parse_price`, `parse_spec` from `rows.py`.
- Produces: `Roles(spec: int, prices: tuple[int, ...], code: int | None, name: int | None)`; `infer_roles(table) -> Roles | None`; `rows_from_table(table, page=0) -> list[ParsedRow]`; `rows_from_lines(lines, page=0) -> list[ParsedRow]`; `scan_notes(lines) -> tuple[str | None, str | None]` (shipping note, warehouse hint `"us"`/`"china"`/`None`); `PriceListData(rows, shipping_note=None, warehouse_hint=None)`; `read_pdf(path) -> PriceListData`. A `table` is pdfplumber's `list[list[str | None]]`. Row names are left `None` where the cell is empty (name carry-over is Task 4).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_price_list_reader.py
from app.library.price_lists.reader import infer_roles, rows_from_lines, rows_from_table, scan_notes
from app.library.price_lists.rows import Spec

# code | name (centered cell: only one row of the group carries it) | spec | price
CODE_FIRST = [
    ["Product Code Product Name Specification Price per kit(USD)", None, None, None],
    ["SM5", "", "5mg*10vials", "$21"],
    ["SM10", "", "10mg*10 vials", "$32"],
    ["SM15", "Semaglutide", "15mg*10 vials", "$43"],
    ["TR5", "", "5mg*10 vials", "$24"],
    ["TR10", "", "10mg*10 vials", "$35"],
]

# name | code | spec | price
NAME_FIRST = [
    ["Price-List-A", None, None, None],
    ["Procutc", "Cat.No", "Specification", "Price"],
    ["Tirzepatide", "TR10", "10mg*10vials", "$31"],
    [None, "TR15", "15mg*10vials", "$42"],
    [None, "TR20", "20mg*10vials", "$53"],
    ["Retarutide", "RT5", "5mg*10vials", "$37"],
]

# a second price column with its own header
TWO_PRICES = [
    ["Acme Peptides product list", None, None, None, None],
    ["Product name", "Cat NO", "Specification", "Unit Price/kit\nUSD", "Wholesale\nprice(20+Kit)"],
    ["", "TR5", "5mg*10vials", "20", "18"],
    ["", "TR10", "10mg*10vials", "30", "27"],
    ["Tirzepatide", "TR15", "15mg*10vials", "40", "36"],
]

# several price tiers, plus a cartridge row whose code only appears inside its name
TIERS = [
    ["Cat.No.", "Product Name", "Specification", "Price", "10kits+", "50kits+", "100kits+"],
    ["EL5", "Eloralintide", "5mg*10vials", "$100", "$98", "$95", "$90"],
    ["EL10", "", "10mg*10vials", "$190", "$185", "$180", "$175"],
    ["RT5", "Retatrutide", "5mg*10vials", "$40", "$38", "$35", "$30"],
    ["", "RT10 (Double Chamber Cartridge)", "10mg*5vials", "$45",
     "Instructions for Use (for reference only) https://example.test/watch", "", ""],
]

# category | product | code | spec | price
CATEGORY_FIRST = [
    ["", "Mazdutide", "MDT10", "10mg*10vials", "$120.00"],
    ["", "Survodutide", "SUR10", "10mg*10vials", "$150.00"],
    ["Reproductive /\nSex Hormones", "HCG", "HCG1000", "1000iu*10vials", "$40.00"],
    ["", "", "HCG2000", "2000iu*10vials", "$60.00"],
    ["", "", "HCG5000(GK5)", "5000iu*10vials", "$110.00"],
]

# garbled and missing cells (text overlap, an empty code cell, a size with no pack count)
GARBLED = [
    ["G10K", "HCG", "10000IU*10vi", "$70.00"],
    ["G5K", None, "5000IUal*10via", "$45.00"],
    [None, None, "10mg*10vials", "$45.00"],
    ["AU100", None, "100mg", "$35.00"],
    ["RA10", "Ara-290", "10mg*10vials", "$40.00"],
]

OILS = [
    ["TC250", "Testosterone Cypionate", "250mg/ml*1vials", "$22/1vial"],
    ["TE250", "Testosterone Enanthate", "250mg/ml*1vials", "$22/1vial"],
    ["SUS250", "SUS250", "250mg*1vials", "$25/1vial"],
]


def test_roles_are_inferred_from_cell_contents():
    roles = infer_roles(CODE_FIRST)
    assert (roles.code, roles.name, roles.spec, roles.prices) == (0, 1, 2, (3,))
    roles = infer_roles(NAME_FIRST)
    assert (roles.name, roles.code, roles.spec, roles.prices) == (0, 1, 2, (3,))
    assert infer_roles(TIERS).prices == (3, 4, 5, 6)
    assert infer_roles(CATEGORY_FIRST).name == 1  # the product column next to the code, not the category column


def test_a_table_with_no_specs_is_not_a_product_table():
    contacts = [["Contact 3: Sam\nWhatsApp: +10000000000", "x"], ["h t t p s : /", ""]]
    assert infer_roles(contacts) is None
    assert rows_from_table(contacts) == []


def test_rows_carry_code_spec_price_and_leave_empty_names_unset():
    rows = rows_from_table(CODE_FIRST, page=2)
    assert [r.code for r in rows] == ["SM5", "SM10", "SM15", "TR5", "TR10"]
    assert [r.name for r in rows] == [None, None, "Semaglutide", None, None]
    assert rows[0].spec == Spec(5, "mg", 10) and rows[0].pack_price == 21.0 and rows[0].page == 2
    assert rows[1].spec == Spec(10, "mg", 10)  # "10mg*10 vials" with a space


def test_name_before_code_layout():
    rows = rows_from_table(NAME_FIRST)
    assert [(r.code, r.name) for r in rows] == [
        ("TR10", "Tirzepatide"), ("TR15", None), ("TR20", None), ("RT5", "Retarutide")]


def test_extra_price_columns_are_kept_under_their_header():
    rows = rows_from_table(TWO_PRICES)
    assert rows[0].pack_price == 20.0 and rows[0].extra_prices == {"Wholesale price(20+Kit)": 18.0}
    tiers = rows_from_table(TIERS)
    assert tiers[0].pack_price == 100.0
    assert tiers[0].extra_prices == {"10kits+": 98.0, "50kits+": 95.0, "100kits+": 90.0}


def test_cartridge_row_takes_its_code_from_the_name_and_ignores_text_in_price_columns():
    cartridge = rows_from_table(TIERS)[-1]
    assert cartridge.code == "RT10" and cartridge.spec == Spec(10, "mg", 5)
    assert cartridge.pack_price == 45.0 and cartridge.extra_prices == {}


def test_category_column_is_not_mistaken_for_the_name():
    rows = rows_from_table(CATEGORY_FIRST)
    assert [(r.code, r.name) for r in rows] == [
        ("MDT10", "Mazdutide"), ("SUR10", "Survodutide"), ("HCG1000", "HCG"), ("HCG2000", None), ("HCG5000(GK5)", None)]
    assert rows[2].spec == Spec(1000, "IU", 10)


def test_garbled_and_missing_cells_are_kept_and_flagged_not_dropped():
    rows = rows_from_table(GARBLED)
    assert rows[0].spec == Spec(10000, "IU", 10) and rows[1].spec == Spec(5000, "IU", 10)
    assert rows[2].code is None and "no-code" in rows[2].flags
    assert rows[3].spec == Spec(100, "mg", None)  # pack size not stated
    assert "code-size-mismatch" in rows[0].flags  # G10K vs 10000 IU is an expected flag


def test_a_pack_of_more_than_ten_vials_is_flagged_not_called_a_kit():
    table = [["ZX5", "Zorvex", "5mg*12vials", "$50"], ["ZX10", "", "10mg*10vials", "$80"],
             ["ZX20", "", "20mg*10vials", "$90"]]
    rows = rows_from_table(table)
    assert rows[0].spec.pack_size == 12 and "unusual-pack-size" in rows[0].flags
    assert rows[1].flags == []


def test_per_vial_price_makes_a_one_vial_box():
    rows = rows_from_table(OILS)
    assert rows[0].spec == Spec(250, "mg/ml", 1) and rows[0].pack_price == 22.0
    assert rows[2].spec == Spec(250, "mg", 1)


def test_wrapped_name_cell_stays_on_one_row():
    table = [
        ["BBG70", "GHK-CU 50mg + BPC-157 10mg +\nTB-500 10mg", "70mg*10vials", "$60"],
        ["BC5", "BPC157", "5mg*10vials", "$20"],
        ["BC10", "", "10mg*10vials", "$30"],
    ]
    rows = rows_from_table(table)
    assert [r.code for r in rows] == ["BBG70", "BC5", "BC10"]
    assert rows[0].name == "GHK-CU 50mg + BPC-157 10mg + TB-500 10mg"


LINES = [
    "ACME LABS",
    "Customer Price List",
    "Name SKU Dose Price",
    "Semaglutide SM10 10mg x 10 vials $21.00",
    "Tirzepatide TR10 10mg x 10 vials $19.00",
]


def test_line_fallback_reads_name_code_spec_and_price():
    rows = rows_from_lines(LINES, page=1)
    assert [(r.code, r.name, r.spec, r.pack_price) for r in rows] == [
        ("SM10", "Semaglutide", Spec(10, "mg", 10), 21.0),
        ("TR10", "Tirzepatide", Spec(10, "mg", 10), 19.0)]


def test_notes_collect_shipping_lines_and_a_warehouse_hint():
    note, hint = scan_notes([
        "Chinese Warehouse - $20 flat shipping worldwide",
        "Shipping fee: $15 for the first 500g",
        "Semaglutide SM10 10mg x 10 vials $21.00",  # a product line is never a note
        "Customs clearance included",
    ])
    assert hint == "china"
    assert note.splitlines() == [
        "Chinese Warehouse - $20 flat shipping worldwide",
        "Shipping fee: $15 for the first 500g",
        "Customs clearance included"]
    assert scan_notes(["US Warehouse stock"]) == (None, "us")
    assert scan_notes(["nothing here"]) == (None, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_reader.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'app.library.price_lists.reader'`.

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_reader.py`
Expected: all pass. If `test_garbled_and_missing_cells...` fails on its last assertion only, the assertion is intentionally loose (`G10K` vs 10000 IU flags `code-size-mismatch`); read the failure before changing code.

- [ ] **Step 5: Check the reader against real PDFs (read-only, not committed)**

Run: `.venv/Scripts/python.exe -c "import os; from pathlib import Path; from app.library.price_lists.reader import read_pdf; d=Path(os.environ['PRICE_LIST_DIR']); [print(p.name[:34].ljust(36), len(r.rows), 'rows', (r.warehouse_hint or '-')) for p in sorted(d.glob('*.pdf')) for r in [read_pdf(p)]]"` (set `PYTHONPATH=.` and `PRICE_LIST_DIR` to the folder holding the owner's price lists; that path is never written into any repository file)
Expected: every PDF with a text layer reports 75-150 rows; a scanned PDF reports 0. Note any vendor far outside that range and stop to investigate before Task 4.

- [ ] **Step 6: Commit**

```bash
git add app/library/price_lists/reader.py tests/test_price_list_reader.py
git commit -m "feat: read price-list PDFs by cell contents, with a line fallback and shipping notes"
```

---

### Task 4: Names (carry-over, learned codes, repair)

**Files:**
- Create: `app/library/price_lists/names.py`
- Test: `tests/test_price_list_names.py`

**Interfaces:**
- Consumes (Task 2): `ParsedRow`, `code_prefix`. Consumes `name_key` from `app/library/matching.py`.
- Produces: `propagate_names(rows: list[ParsedRow]) -> None`; `PrefixName(key: str, display: str)` (frozen); `learn_prefixes(observations: Iterable[tuple[str, str, str]]) -> dict[str, PrefixName]` (observations are `(vendor_key, code_prefix, product_name)`); `repair_names(rows, table, is_known: Callable[[str], bool]) -> None` (appends flag `"name-from-code"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_price_list_names.py
# tests/test_price_list_names.py
from app.library.price_lists.names import PrefixName, learn_prefixes, propagate_names, repair_names
from app.library.price_lists.rows import ParsedRow, Spec


def r(code, name=None):
    return ParsedRow(code=code, name=name, spec=Spec(5, "mg", 10), pack_price=1.0)


def names_of(rows):
    return [row.name for row in rows]


def test_a_name_in_the_middle_of_a_run_covers_the_whole_run():
    rows = [r("SM5"), r("SM10"), r("SM15", "Semaglutide"), r("SM20"), r("TR5"), r("TR10", "Tirzepatide")]
    propagate_names(rows)
    assert names_of(rows) == ["Semaglutide"] * 4 + ["Tirzepatide"] * 2


def test_a_run_is_the_same_code_prefix_not_the_same_digits():
    rows = [r("HCG1000", "HCG"), r("HCG2000"), r("HCG5000(GK5)"), r("HCG10000(Gk10)")]
    propagate_names(rows)
    assert names_of(rows) == ["HCG"] * 4


def test_rows_with_their_own_names_keep_them():
    rows = [r("BB10", "BPC 5mg + TB 5mg"), r("BB20", "BPC10mg+TB10mg"), r("BB30")]
    propagate_names(rows)
    assert names_of(rows) == ["BPC 5mg + TB 5mg", "BPC10mg+TB10mg", "BPC10mg+TB10mg"]  # an unnamed row takes the nearest name above


def test_products_sharing_a_code_prefix_keep_their_own_names_when_names_sit_on_the_first_row():
    rows = [r("GR2", "GHRP-2"), r("GR2", None), r("GR6", "GHRP-6"), r("GR6", None)]
    propagate_names(rows)
    assert names_of(rows) == ["GHRP-2", "GHRP-2", "GHRP-6", "GHRP-6"]


def test_consecutive_code_less_rows_continue_the_name_above():
    rows = [r(None, "Zorvex"), r(None, None), r("ZX9", None)]
    propagate_names(rows)
    assert names_of(rows) == ["Zorvex", "Zorvex", None]


def test_rows_without_a_code_never_inherit_a_neighbors_name():
    rows = [r("RT5", "Retatrutide"), r(None), r("RT10")]
    propagate_names(rows)
    assert names_of(rows) == ["Retatrutide", None, None]  # the code-less row breaks the run


def test_a_prefix_is_learned_when_two_vendors_agree():
    table = learn_prefixes([
        ("a", "RT", "Retatrutide"), ("a", "RT", "Retatrutide"), ("b", "RT", "retatrutide"), ("c", "RT", "Retarutide")])
    assert table["RT"] == PrefixName("retatrutide", "Retatrutide")


def test_one_vendor_alone_teaches_nothing():
    assert learn_prefixes([("a", "RT", "Retatrutide")] * 5) == {}


def test_a_prefix_with_two_equally_common_meanings_is_not_learned():
    table = learn_prefixes([("a", "LC", "L-Carnitine"), ("b", "LC", "L-Carnitine"),
                            ("c", "LC", "Lipo-C"), ("d", "LC", "Lipo-C")])
    assert "LC" not in table


def test_repair_fills_blank_names_and_flags_them():
    table = {"RT": PrefixName("retatrutide", "Retatrutide")}
    rows = [r("RT10"), r("ZZ9")]
    repair_names(rows, table, is_known=lambda n: True)
    assert rows[0].name == "Retatrutide" and rows[0].flags == ["name-from-code"]
    assert rows[1].name is None and rows[1].flags == []  # nothing learned for ZZ: stays null


def test_repair_fixes_a_misspelling_the_library_does_not_know():
    table = {"RT": PrefixName("retatrutide", "Retatrutide")}
    row = r("RT5", "Retarutide")
    repair_names([row], table, is_known=lambda n: n == "Retatrutide")
    assert row.name == "Retatrutide" and row.flags == ["name-from-code"]


def test_repair_keeps_a_different_product_that_shares_the_prefix():
    table = {"RT": PrefixName("retatrutide", "Retatrutide")}
    cartridge = r("RT10", "RT10 (Double Chamber Cartridge)")
    repair_names([cartridge], table, is_known=lambda n: False)
    assert cartridge.name == "RT10 (Double Chamber Cartridge)" and cartridge.flags == []


def test_repair_keeps_a_name_the_library_already_knows():
    table = {"RT": PrefixName("retatrutide", "Retatrutide")}
    row = r("RT5", "Retatrutide Acetate")
    repair_names([row], table, is_known=lambda n: True)
    assert row.name == "Retatrutide Acetate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_names.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'app.library.price_lists.names'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/library/price_lists/names.py
# app/library/price_lists/names.py
"""Names for price-list rows: carry a group's name down its rows, learn what a short code means, repair."""

import difflib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.library.matching import name_key
from app.library.price_lists.rows import ParsedRow, code_prefix

_TYPO_RATIO = 0.8


def propagate_names(rows: list[ParsedRow]) -> None:
    """Vendors merge the name cell across a group, so only one row of a group carries it, at the top or in the
    middle. A run is a stretch of consecutive rows with the same code prefix (or consecutive rows with no code).
    Rows above a run's first name take that name; every later unnamed row takes the nearest name above it, so
    two products that share a prefix (GR2..., GR6...) keep their own names."""
    i = 0
    while i < len(rows):
        prefix = code_prefix(rows[i].code)
        j = i + 1
        while j < len(rows) and code_prefix(rows[j].code) == prefix:
            j += 1
        run = rows[i:j]
        current = next((row.name for row in run if row.name), None) if prefix is not None else None
        for row in run:
            if row.name:
                current = row.name
            elif current:
                row.name = current
        i = j


@dataclass(frozen=True)
class PrefixName:
    key: str      # name_key of the agreed name
    display: str  # its most common spelling


def learn_prefixes(observations: Iterable[tuple[str, str, str]]) -> dict[str, PrefixName]:
    """(vendor_key, code_prefix, product_name) -> {prefix: name}. A prefix is learned only when at least two
    vendors give the same name and no rival name is as common."""
    vendors: dict[str, dict[str, set[str]]] = {}
    spellings: dict[tuple[str, str], Counter] = {}
    for vendor, prefix, name in observations:
        key = name_key(name)
        if not prefix or not key:
            continue
        vendors.setdefault(prefix, {}).setdefault(key, set()).add(vendor)
        spellings.setdefault((prefix, key), Counter())[name.strip()] += 1
    table: dict[str, PrefixName] = {}
    for prefix, by_key in vendors.items():
        ranked = sorted(by_key.items(), key=lambda item: len(item[1]), reverse=True)
        best_key, best_vendors = ranked[0]
        if len(best_vendors) < 2:
            continue
        if len(ranked) > 1 and len(ranked[1][1]) >= len(best_vendors):
            continue
        counts = spellings[(prefix, best_key)]
        display = max(counts, key=lambda s: (counts[s], s[:1].isupper(), s))
        table[prefix] = PrefixName(best_key, display)
    return table


def repair_names(rows: list[ParsedRow], table: dict[str, PrefixName], is_known: Callable[[str], bool]) -> None:
    """Fill a blank name from the learned code, or fix a misspelling the library does not recognise. A different
    product that merely shares the prefix (a cartridge) is left alone."""
    for row in rows:
        learned = table.get(code_prefix(row.code) or "")
        if learned is None:
            continue
        if not row.name:
            row.name = learned.display
        elif (name_key(row.name) != learned.key and not is_known(row.name)
              and difflib.SequenceMatcher(None, name_key(row.name), learned.key).ratio() >= _TYPO_RATIO):
            row.name = learned.display
        else:
            continue
        row.flags.append("name-from-code")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_names.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/library/price_lists/names.py tests/test_price_list_names.py
git commit -m "feat: carry price-list names down a code run and repair them from learned codes"
```

---

### Task 5: Tables and migration

**Files:**
- Modify: `app/models.py` (after the `VendorFavorite` class), `tests/conftest.py`
- Create: `migrations/versions/0031_price_lists.py`
- Test: `tests/test_price_list_importer.py` (model section; the file grows in Tasks 6 and 7)

**Interfaces:**
- Produces: `Warehouse` (`US`, `CHINA`), `WarehouseSource` (`FILENAME`, `TEXT`, `ASSUMED`, `MANUAL`), `PriceList`, `PriceListItem` in `app.models`, exactly as in the spec's tables. `PriceList.items` cascades deletes; `PriceList.vendor_id` and `PriceListItem.peptide_id` are `ON DELETE SET NULL`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_price_list_importer.py` with this content (later tasks append to it):

```python
# tests/test_price_list_importer.py
from datetime import date

from sqlalchemy import func, select

from app.models import (
    Peptide, PeptideSource, PriceList, PriceListItem, Vendor, Warehouse, WarehouseSource,
)


def make_list(db, vendor=None, filename="Acme - Price List - 2026-09-01.pdf"):
    plist = PriceList(vendor_name="Acme", vendor_id=vendor.id if vendor else None, list_date=date(2026, 9, 1),
                      source_filename=filename, warehouse=Warehouse.CHINA, warehouse_source=WarehouseSource.ASSUMED)
    plist.items.append(PriceListItem(code="ZX10", product_name="Zorvex", vial_amount=10, vial_unit="mg",
                                     pack_size=10, pack_price=50.0, pack_type="kit", extra_prices={"10kits+": 45.0},
                                     flags=["code-size-mismatch"]))
    db.add(plist)
    db.commit()
    return plist


def test_price_list_round_trips_with_json_columns(db):
    make_list(db)
    db.expire_all()
    item = db.scalar(select(PriceListItem))
    assert (item.vial_amount, item.pack_size, item.pack_price, item.pack_type) == (10, 10, 50.0, "kit")
    assert item.extra_prices == {"10kits+": 45.0} and item.flags == ["code-size-mismatch"]
    plist = db.scalar(select(PriceList))
    assert (plist.warehouse, plist.warehouse_source, plist.currency) == (Warehouse.CHINA, WarehouseSource.ASSUMED, "USD")
    assert plist.imported_at is not None


def test_deleting_a_list_deletes_its_items(db):
    plist = make_list(db)
    db.delete(plist)
    db.commit()
    assert db.scalar(select(func.count()).select_from(PriceListItem)) == 0


def test_deleting_the_vendor_leaves_the_list_with_its_name(db):
    vendor = Vendor(name="Acme")
    db.add(vendor)
    db.commit()
    make_list(db, vendor)
    db.delete(vendor)
    db.commit()
    db.expire_all()
    plist = db.scalar(select(PriceList))
    assert plist.vendor_id is None and plist.vendor_name == "Acme"


def test_deleting_a_matched_card_keeps_the_item(db):
    card = Peptide(name="Zorvex Card", source=PeptideSource.CUSTOM)
    db.add(card)
    db.commit()
    plist = make_list(db)
    plist.items[0].peptide_id = card.id
    db.commit()
    db.delete(card)
    db.commit()
    db.expire_all()
    assert db.scalar(select(PriceListItem)).peptide_id is None


def test_source_filename_is_unique(db):
    import pytest
    from sqlalchemy.exc import IntegrityError
    make_list(db)
    with pytest.raises(IntegrityError):
        make_list(db)
    db.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_importer.py`
Expected: collection ERROR, `ImportError: cannot import name 'PriceList' from 'app.models'`.

- [ ] **Step 3: Write minimal implementation**

In `app/models.py`, add after the `VendorFavorite` class (the `UniqueConstraint`, `ForeignKey`, `JSON`, `Date`, `Float`, `Text` imports already exist):

```python
class Warehouse(LabeledEnum):
    US = ("us", "US")
    CHINA = ("china", "China")


class WarehouseSource(LabeledEnum):
    FILENAME = ("filename", "Filename")
    TEXT = ("text", "List text")
    ASSUMED = ("assumed", "Assumed")
    MANUAL = ("manual", "Set manually")


class PriceList(Base):
    """One imported vendor price list. Reference data (like the peptide library), not scoped to a user."""

    __tablename__ = "price_lists"
    __table_args__ = (UniqueConstraint("source_filename", name="uq_price_list_source_filename"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_name: Mapped[str] = mapped_column(String(200))  # kept even if the vendor row is later deleted
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), index=True)
    warehouse: Mapped[Warehouse] = mapped_column(_enum_column(Warehouse), default=Warehouse.CHINA)
    warehouse_source: Mapped[WarehouseSource] = mapped_column(
        _enum_column(WarehouseSource), default=WarehouseSource.ASSUMED)
    list_date: Mapped[date] = mapped_column(Date)
    source_filename: Mapped[str] = mapped_column(String(300))
    shipping_note: Mapped[str | None] = mapped_column(Text)  # the vendor's own wording, not interpreted
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["PriceListItem"]] = relationship(
        back_populates="price_list", cascade="all, delete-orphan")


class PriceListItem(Base):
    """One product line of a price list. A pack is a kit (exactly 10 vials) or a box (fewer). Per-vial cost is
    pack_price / pack_size (computed, not stored)."""

    __tablename__ = "price_list_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    price_list_id: Mapped[int] = mapped_column(ForeignKey("price_lists.id", ondelete="CASCADE"), index=True)
    code: Mapped[str | None] = mapped_column(String(30))
    product_name: Mapped[str | None] = mapped_column(String(300))
    peptide_id: Mapped[int | None] = mapped_column(ForeignKey("peptides.id", ondelete="SET NULL"), index=True)
    vial_amount: Mapped[float] = mapped_column(Float)
    vial_unit: Mapped[str] = mapped_column(String(10))  # mg | mcg | IU | ml | mg/ml
    pack_size: Mapped[int | None] = mapped_column(Integer)  # vials in the pack the price buys
    pack_price: Mapped[float | None] = mapped_column(Float)
    pack_type: Mapped[str | None] = mapped_column(String(10))  # "kit" (exactly 10 vials) | "box" (fewer) | None
    extra_prices: Mapped[dict | None] = mapped_column(JSON)  # other price columns by header, e.g. {"10kits+": 173}
    flags: Mapped[list | None] = mapped_column(JSON)

    price_list: Mapped["PriceList"] = relationship(back_populates="items")
```

Create `migrations/versions/0031_price_lists.py`:

```python
"""price_lists and price_list_items: imported vendor price lists (pack prices, warehouse region)

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0031'
down_revision: Union[str, None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'price_lists',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vendor_name', sa.String(200), nullable=False),
        sa.Column('vendor_id', sa.Integer(), sa.ForeignKey('vendors.id', ondelete='SET NULL')),
        sa.Column('warehouse', sa.Enum('us', 'china', name='warehouse', native_enum=False, length=20),
                  nullable=False),
        sa.Column('warehouse_source',
                  sa.Enum('filename', 'text', 'assumed', 'manual', name='warehousesource', native_enum=False,
                          length=20),
                  nullable=False),
        sa.Column('list_date', sa.Date(), nullable=False),
        sa.Column('source_filename', sa.String(300), nullable=False),
        sa.Column('shipping_note', sa.Text()),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source_filename', name='uq_price_list_source_filename'),
    )
    op.create_index('ix_price_lists_vendor_id', 'price_lists', ['vendor_id'])

    op.create_table(
        'price_list_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('price_list_id', sa.Integer(), sa.ForeignKey('price_lists.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('code', sa.String(30)),
        sa.Column('product_name', sa.String(300)),
        sa.Column('peptide_id', sa.Integer(), sa.ForeignKey('peptides.id', ondelete='SET NULL')),
        sa.Column('vial_amount', sa.Float(), nullable=False),
        sa.Column('vial_unit', sa.String(10), nullable=False),
        sa.Column('pack_size', sa.Integer()),
        sa.Column('pack_price', sa.Float()),
        sa.Column('pack_type', sa.String(10)),
        sa.Column('extra_prices', sa.JSON()),
        sa.Column('flags', sa.JSON()),
    )
    op.create_index('ix_price_list_items_price_list_id', 'price_list_items', ['price_list_id'])
    op.create_index('ix_price_list_items_peptide_id', 'price_list_items', ['peptide_id'])


def downgrade() -> None:
    op.drop_table('price_list_items')
    op.drop_table('price_lists')
```

In `tests/conftest.py`, add `PriceList` to the `from app.models import (...)` list and, inside the `clean` fixture, add `s.query(PriceList).delete()` as the first line after `with SessionLocal() as s:` (before `s.query(Vendor).delete()`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_importer.py`
Expected: 5 passed. Then run `.venv/Scripts/python.exe -m alembic heads` — expected `0031 (head)`.

- [ ] **Step 5: Commit**

```bash
git add app/models.py migrations/versions/0031_price_lists.py tests/conftest.py tests/test_price_list_importer.py
git commit -m "feat: price_lists and price_list_items tables"
```

---

### Task 6: Importer (vendor, warehouse, items, library sizes)

**Files:**
- Create: `app/library/price_lists/importer.py`
- Modify: `tests/test_price_list_importer.py` (append)

**Interfaces:**
- Consumes: `parse_filename` (Task 1), `ParsedRow`/`Spec`/`code_prefix` (Task 2), `merge_specs`/`format_spec` (Task 2), `PriceListData` (Task 3), `propagate_names`/`repair_names`/`PrefixName` (Task 4), the models (Task 5), `match_name` from `app/library/matching.py`.
- Produces: `vendor_key(name) -> str`; `resolve_vendor(session, vendor_name, list_date) -> tuple[Vendor, bool]` (bool = created); `decide_warehouse(from_filename, from_text, override) -> tuple[Warehouse, WarehouseSource]`; `ImportReport` dataclass (`filename`, `vendor_name`, `vendor_created`, `warehouse`, `warehouse_source`, `rows`, `matched`, `specs_added`, `flagged: list[str]`, `unmatched: list[str]`, `skipped: str | None`); `store_price_list(session, filename, data, *, prefix_table, warehouse_override=None, dry_run=False) -> ImportReport` (commits, or rolls back when `dry_run`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_price_list_importer.py` (add the new imports at the top of the file alongside the existing ones):

```python
import pytest

from app.library.price_lists.importer import decide_warehouse, store_price_list, vendor_key
from app.library.price_lists.reader import PriceListData
from app.library.price_lists.rows import ParsedRow, Spec

FILE = "Acme Labs - Price List - 2026-09-01.pdf"


def row(code, name, amount=10, unit="mg", pack=10, price=50.0, **kw):
    return ParsedRow(code=code, name=name, spec=Spec(amount, unit, pack), pack_price=price, **kw)


def data(*rows, shipping=None, hint=None):
    return PriceListData(rows=list(rows), shipping_note=shipping, warehouse_hint=hint)


def add_card(db, name, specs=None):
    db.add(Peptide(name=name, source=PeptideSource.CUSTOM, library_specifications=specs))
    db.commit()


def store(db, filename, d, **kw):
    kw.setdefault("prefix_table", {})
    return store_price_list(db, filename, d, **kw)


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_vendor_key_ignores_case_punctuation_and_the_word_peptide():
    assert vendor_key("Zephyr Peptides") == vendor_key("Zephyr") == "zephyr"
    assert vendor_key("Orchid Peptide") == vendor_key("orchid") == "orchid"
    assert vendor_key("Mid Valley Bio") == "midvalleybio"


def test_decide_warehouse_precedence():
    assert decide_warehouse(None, None, None) == (Warehouse.CHINA, WarehouseSource.ASSUMED)
    assert decide_warehouse(None, "us", None) == (Warehouse.US, WarehouseSource.TEXT)
    assert decide_warehouse("us", "china", None) == (Warehouse.US, WarehouseSource.FILENAME)
    assert decide_warehouse("us", "china", "china") == (Warehouse.CHINA, WarehouseSource.MANUAL)


def test_import_creates_the_vendor_and_the_list(db):
    report = store(db, FILE, data(row("ZX10", "Zorvex 10")))
    vendor = db.scalar(select(Vendor).where(Vendor.name == "Acme Labs"))
    assert report.vendor_created and vendor.created_by_id is None
    assert vendor.price_list_updated_at == date(2026, 9, 1)
    plist = db.scalar(select(PriceList))
    assert (plist.vendor_name, plist.vendor_id, plist.list_date, plist.source_filename) == (
        "Acme Labs", vendor.id, date(2026, 9, 1), FILE)


def test_existing_vendor_is_reused_ignoring_case_and_the_word_peptides(db):
    db.add_all([Vendor(name="Zephyr Peptides"), Vendor(name="Northwind")])
    db.commit()
    store(db, "Zephyr - Price List NEW - 2026-05-19.pdf", data(row("ZX10", "Zorvex")))
    store(db, "northwind - USA Price List - 2026-10-03.pdf", data(row("ZX10", "Zorvex")))
    assert count(db, Vendor) == 2
    assert {p.vendor_name for p in db.scalars(select(PriceList))} == {"Zephyr", "northwind"}
    assert all(p.vendor_id is not None for p in db.scalars(select(PriceList)))


def test_one_vendor_can_have_a_china_and_a_usa_list(db):
    store(db, "Borealis - China Price List - 2026-08-24.pdf", data(row("ZX10", "Zorvex")))
    store(db, "Borealis - USA Price List - 2026-08-31.pdf", data(row("ZX10", "Zorvex")))
    assert db.scalar(select(func.count()).select_from(Vendor).where(Vendor.name == "Borealis")) == 1
    lists = db.scalars(select(PriceList).order_by(PriceList.list_date)).all()
    assert [(l.warehouse, l.warehouse_source) for l in lists] == [
        (Warehouse.CHINA, WarehouseSource.FILENAME), (Warehouse.US, WarehouseSource.FILENAME)]
    assert lists[0].vendor_id == lists[1].vendor_id


def test_unnamed_warehouse_is_assumed_china(db):
    report = store(db, FILE, data(row("ZX10", "Zorvex")))
    plist = db.scalar(select(PriceList))
    assert (plist.warehouse, plist.warehouse_source) == (Warehouse.CHINA, WarehouseSource.ASSUMED)
    assert (report.warehouse, report.warehouse_source) == ("china", "assumed")


def test_warehouse_override_and_text_hint(db):
    store(db, FILE, data(row("ZX10", "Zorvex"), hint="us"))
    assert db.scalar(select(PriceList.warehouse_source)) == WarehouseSource.TEXT
    store(db, FILE, data(row("ZX10", "Zorvex"), hint="us"), warehouse_override="china")
    plist = db.scalar(select(PriceList))
    assert (plist.warehouse, plist.warehouse_source) == (Warehouse.CHINA, WarehouseSource.MANUAL)


def test_reimport_replaces_the_list_instead_of_duplicating(db):
    store(db, FILE, data(row("ZX10", "Zorvex", 10), row("ZX20", "Zorvex", 20)))
    store(db, FILE, data(row("ZX10", "Zorvex", 10)))
    assert count(db, PriceList) == 1 and count(db, PriceListItem) == 1


def test_newer_list_from_the_same_vendor_is_kept_as_history(db):
    store(db, "Acme Labs - Price List - 2026-09-01.pdf", data(row("ZX10", "Zorvex")))
    store(db, "Acme Labs - Price List - 2026-10-01.pdf", data(row("ZX10", "Zorvex")))
    assert count(db, PriceList) == 2 and count(db, Vendor) == 1


def test_vendor_date_only_moves_forward(db):
    store(db, "Acme Labs - Price List - 2026-10-01.pdf", data(row("ZX10", "Zorvex")))
    store(db, "Acme Labs - Price List - 2026-09-01.pdf", data(row("ZX10", "Zorvex")))
    assert db.scalar(select(Vendor.price_list_updated_at).where(Vendor.name == "Acme Labs")) == date(2026, 10, 1)


def test_dry_run_reports_but_writes_nothing(db):
    add_card(db, "Zorvex")
    report = store(db, FILE, data(row("ZX10", "Zorvex", 10)), dry_run=True)
    assert report.rows == 1 and report.matched == 1 and report.vendor_created
    db.expire_all()
    assert count(db, Vendor) == 0 and count(db, PriceList) == 0
    assert db.scalar(select(Peptide.library_specifications).where(Peptide.name == "Zorvex")) is None


def test_never_creates_library_cards(db):
    before = count(db, Peptide)
    report = store(db, FILE, data(row("ZX10", "Unknown Thing")))
    assert count(db, Peptide) == before
    assert db.scalar(select(PriceListItem.peptide_id)) is None
    assert report.unmatched == ["Unknown Thing"] and report.matched == 0


def test_matched_rows_add_vial_sizes_and_keep_the_cards_existing_ones(db):
    add_card(db, "Zorvex", specs="50mg")
    report = store(db, FILE, data(row("ZX5", "Zorvex", 5), row("ZX10", "Zorvex", 10), row("ZX3", "Zorvex", 3, "ml")))
    assert db.scalar(select(Peptide.library_specifications).where(Peptide.name == "Zorvex")) == "5mg, 10mg, 50mg"
    assert report.specs_added == 1
    card = db.scalar(select(Peptide).where(Peptide.name == "Zorvex"))
    assert {i.peptide_id for i in db.scalars(select(PriceListItem))} == {card.id}


def test_liquids_are_stored_but_add_no_specs(db):
    store(db, FILE, data(row("BA10", "Bacteriostatic Water", 10, "ml")))
    item = db.scalar(select(PriceListItem))
    assert (item.vial_unit, item.peptide_id) == ("ml", None)


def test_with_and_without_variants_stay_separate(db):
    add_card(db, "Zorvex with B12")
    add_card(db, "Zorvex without B12")
    store(db, FILE, data(row("Z5", "Zorvex without B12", 5)))
    specs = dict(db.execute(select(Peptide.name, Peptide.library_specifications)).all())
    assert specs["Zorvex without B12"] == "5mg" and specs["Zorvex with B12"] is None


@pytest.mark.parametrize("pack, expected", [(10, "kit"), (5, "box"), (1, "box"), (None, None), (12, None)])
def test_pack_type_is_kit_for_ten_vials_and_box_for_fewer(db, pack, expected):
    store(db, FILE, data(row("ZX10", "Zorvex", pack=pack)))
    item = db.scalar(select(PriceListItem))
    assert (item.pack_size, item.pack_type) == (pack, expected)


def test_pack_size_price_extras_and_flags_are_stored(db):
    store(db, FILE, data(row("TC250", "Zorvex Oil", 250, "mg/ml", 1, 30.0,
                             extra_prices={"10kits+": 25.0}, flags=["code-size-mismatch"])))
    item = db.scalar(select(PriceListItem))
    assert (item.vial_amount, item.vial_unit, item.pack_size, item.pack_price, item.pack_type) == (
        250, "mg/ml", 1, 30.0, "box")
    assert item.extra_prices == {"10kits+": 25.0} and item.flags == ["code-size-mismatch"]


def test_row_with_no_name_is_stored_and_flagged(db):
    report = store(db, FILE, data(row("ZZ9", None)))
    item = db.scalar(select(PriceListItem))
    assert item.product_name is None and item.flags == ["no-name"]
    assert report.flagged == ["ZZ9 -: no-name"]


def test_file_without_a_vendor_or_date_is_skipped(db):
    report = store(db, "Price List.pdf", data(row("ZX10", "Zorvex")))
    assert report.skipped and count(db, PriceList) == 0 and count(db, Vendor) == 0


def test_list_with_no_rows_is_skipped(db):
    report = store(db, FILE, data())
    assert "scanned" in report.skipped and count(db, PriceList) == 0


def test_names_are_repaired_from_the_learned_code_table(db):
    from app.library.price_lists.names import PrefixName
    store(db, FILE, data(row("ZX10", None)), prefix_table={"ZX": PrefixName("zorvex", "Zorvex")})
    item = db.scalar(select(PriceListItem))
    assert item.product_name == "Zorvex" and item.flags == ["name-from-code"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_importer.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'app.library.price_lists.importer'`.

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_importer.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/library/price_lists/importer.py tests/test_price_list_importer.py
git commit -m "feat: store price lists with vendors, warehouse region and library sizes"
```

---

### Task 7: Batch run, report, and the command-line tool

**Files:**
- Modify: `app/library/price_lists/importer.py` (append), `tests/test_price_list_importer.py` (append)
- Create: `tools/import_price_lists.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6, `read_pdf` (Task 3), `learn_prefixes` (Task 4).
- Produces: `observations_from_db(session, exclude_filenames) -> list[tuple[str, str, str]]`; `run_import(paths, session_factory, *, warehouse=None, dry_run=False, reader=read_pdf) -> list[ImportReport]`; `format_reports(reports, *, dry_run=False) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_price_list_importer.py`:

```python
from app.db import SessionLocal
from app.library.price_lists.importer import format_reports, run_import


def test_run_import_reads_folders_learns_codes_and_skips_non_pdfs(tmp_path):
    names = {
        "a": "A - Price List - 2026-01-01.pdf", "b": "B - Price List - 2026-01-02.pdf",
        "c": "C - Price List - 2026-01-03.pdf", "scan": "D - Price List - 2026-01-04-01.jpg",
    }
    for name in names.values():
        (tmp_path / name).write_bytes(b"")
    # fictional product names, so no seeded library card is matched and given sizes
    reading = {
        names["a"]: data(row("ZX5", "Zorvex", 5), row("ZX10", None, 10)),
        names["b"]: data(row("ZX5", "zorvex", 5)),
        names["c"]: data(row("ZX10", None, 10), row("QU5", "Quillamine", 5)),  # ZX10 has no name anywhere
    }
    reports = run_import([tmp_path], SessionLocal, reader=lambda path: reading[path.name])

    by_name = {r.filename: r for r in reports}
    assert "not a PDF" in by_name[names["scan"]].skipped
    with SessionLocal() as s:
        c_items = s.scalars(select(PriceListItem).join(PriceList).where(PriceList.source_filename == names["c"])
                            .order_by(PriceListItem.id)).all()
        assert [(i.code, i.product_name, i.flags) for i in c_items] == [
            ("ZX10", "Zorvex", ["name-from-code"]), ("QU5", "Quillamine", None)]  # QU seen at one vendor: not learned
        a_zx10 = s.scalar(select(PriceListItem).join(PriceList)
                          .where(PriceList.source_filename == names["a"], PriceListItem.code == "ZX10"))
        assert a_zx10.product_name == "Zorvex" and a_zx10.flags is None  # carried down its run, not repaired


def test_run_import_one_bad_file_does_not_stop_the_others(tmp_path):
    good, bad = "A - Price List - 2026-01-01.pdf", "B - Price List - 2026-01-02.pdf"
    for name in (good, bad):
        (tmp_path / name).write_bytes(b"")

    def reader(path):
        if path.name == bad:
            raise RuntimeError("unreadable")
        return data(row("ZX10", "Zorvex"))

    reports = {r.filename: r for r in run_import([tmp_path], SessionLocal, reader=reader)}
    assert "unreadable" in reports[bad].skipped and reports[good].skipped is None
    with SessionLocal() as s:
        assert [p.source_filename for p in s.scalars(select(PriceList))] == [good]


def test_run_import_dry_run_writes_nothing(tmp_path):
    name = "A - Price List - 2026-01-01.pdf"
    (tmp_path / name).write_bytes(b"")
    reports = run_import([tmp_path / name], SessionLocal, dry_run=True, reader=lambda p: data(row("ZX10", "Zorvex")))
    assert reports[0].rows == 1
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(PriceList)) == 0


def test_report_text_lists_flags_unmatched_and_assumed_warehouses(db):
    reports = [store(db, FILE, data(row("ZX10", "Unknown Thing", flags=["code-size-mismatch"])))]
    text = format_reports(reports)
    assert FILE in text and "Acme Labs (new)" in text and "china (assumed)" in text
    assert "ZX10 Unknown Thing: code-size-mismatch" in text and "Unknown Thing" in text
    assert "Warehouse assumed (China) for" in text and "dry run" not in text.lower()
    assert "dry run" in format_reports(reports, dry_run=True).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_importer.py`
Expected: collection ERROR, `ImportError: cannot import name 'run_import' from 'app.library.price_lists.importer'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/library/price_lists/importer.py` (add `from pathlib import Path` and `from collections.abc import Callable, Iterable` to the imports, and change three existing import lines to: `from app.library.price_lists.names import PrefixName, learn_prefixes, propagate_names, repair_names`, `from app.library.price_lists.reader import PriceListData, read_pdf`, `from app.library.price_lists.rows import code_prefix, pack_type`):

```python
def observations_from_db(session: Session, exclude_filenames=()) -> list[tuple[str, str, str]]:
    """(vendor_key, code_prefix, product_name) from lists already stored, for learning what a code means."""
    stmt = (select(PriceList.vendor_name, PriceListItem.code, PriceListItem.product_name)
            .join(PriceListItem, PriceListItem.price_list_id == PriceList.id)
            .where(PriceListItem.code.is_not(None), PriceListItem.product_name.is_not(None),
                   PriceList.source_filename.not_in(list(exclude_filenames))))
    return [(vendor_key(vendor), code_prefix(code), name)
            for vendor, code, name in session.execute(stmt) if code_prefix(code)]


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
```

Create `tools/import_price_lists.py`:

```python
#!/usr/bin/env python3
"""Import vendor price-list PDFs: vendors, pack prices (kits and boxes), warehouse region, and library vial sizes.

Usage: python tools/import_price_lists.py PATH [PATH ...] [--dry-run] [--warehouse us|china]

PATH is a PDF or a folder of them. The vendor, optional warehouse and date come from the filename:
"<Vendor> - [<Warehouse> ]Price List - <YYYY-MM-DD>.pdf". Re-importing a file replaces its rows.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.library.price_lists.importer import format_reports, run_import  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    parser.add_argument("--warehouse", choices=["us", "china"], help="override the detected warehouse (one file only)")
    args = parser.parse_args(argv)
    if args.warehouse and (len(args.paths) != 1 or args.paths[0].is_dir()):
        parser.error("--warehouse needs exactly one file")
    missing = [p for p in args.paths if not p.exists()]
    if missing:
        parser.error(f"not found: {', '.join(map(str, missing))}")
    reports = run_import(args.paths, SessionLocal, warehouse=args.warehouse, dry_run=args.dry_run)
    print(format_reports(reports, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_price_list_importer.py`
Expected: all pass. Then `.venv/Scripts/python.exe tools/import_price_lists.py --help` prints the usage without error.

- [ ] **Step 5: Commit**

```bash
git add app/library/price_lists/importer.py tools/import_price_lists.py tests/test_price_list_importer.py
git commit -m "feat: batch price-list import with learned codes, a report, and a command-line tool"
```

---

### Task 8: Retire the single-layout parser

**Files:**
- Delete: `app/library/price_list_parser.py`, `app/library/populate_library_specs.py`, `tools/populate_price_list.py`, `tests/test_library_populate.py`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Consumes: the new reader and importer. Nothing else imports the deleted modules (verify with the grep below).

- [ ] **Step 1: Prove the new reader still gives the old sample list's specs**

Run (read-only; `PYTHONPATH=.`; set `SAMPLE_LIST` to the path of the sample list the old parser was built for, a file outside the repository):
`.venv/Scripts/python.exe -c "import os; from pathlib import Path; from app.library.price_list_parser import parse_price_list_pdf as old; from app.library.price_lists.reader import read_pdf; from app.library.price_lists.names import propagate_names; from app.library.price_lists.specs import format_spec; p=Path(os.environ['SAMPLE_LIST']); d=read_pdf(p); propagate_names(d.rows); new={}; [new.setdefault(r.name,set()).add(format_spec(r.spec.amount,r.spec.unit)) for r in d.rows if r.name and r.spec.unit in ('mg','mcg','IU')]; o={k:set(v.split(', ')) for k,v in old(p).items()}; print('old names', len(o), 'missing in new:', [k for k in o if k not in new]); print('different:', {k:(sorted(o[k]),sorted(new[k])) for k in o if k in new and o[k]!=new[k]})"`

Expected: `missing in new: []`; `different` lists exactly one name: `Mazdutide` (old `['10mg', '5mg']`, new `['10mg']`: the old parser wrongly gave Adipotide's 5mg row to Mazdutide). Any other difference: stop and investigate (an earlier draft that gave a whole code run its first name mislabeled products sharing a prefix, e.g. two products whose codes both start `GR`; names now fill forward from the nearest name above).

- [ ] **Step 2: Remove the old modules and their test**

```bash
git rm app/library/price_list_parser.py app/library/populate_library_specs.py tools/populate_price_list.py tests/test_library_populate.py
```

- [ ] **Step 3: Confirm nothing still imports them**

Use Grep for `price_list_parser|populate_library_specs|populate_price_list` over `app`, `tools`, `tests`. Expected: no matches (documentation under `docs/` is updated in the next step).

- [ ] **Step 4: Update the roadmap**

In `docs/ROADMAP.md`, replace the sentence in the "Library price-list import: vendor naming" paragraph that begins ``Vendors spell one compound many ways`` so it names the new tool: change `` `tools/populate_price_list.py` now matches `` to `` `tools/import_price_lists.py` now matches ``. In the Phase 9 section add one bullet after the integrations list:

```markdown
- **Price analyzer.** Vendor price lists are now imported and stored (`tools/import_price_lists.py`: vendors, pack prices (a kit is exactly 10 vials; fewer is a box), extra price tiers, and a US/China warehouse region, assumed China when a list names none). The analyzer itself (compare vendors by per-vial cost, weigh shipping, fees, and the customs risk of China warehouses) is not built. Not yet read: scanned PDFs, image price lists (need OCR), and spreadsheets.
```

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings`
Expected: all pass (the previous 914, minus the 6 removed `test_library_populate` tests, plus the new price-list tests).

- [ ] **Step 6: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "refactor: replace the single-layout price-list parser with the multi-vendor importer"
```

---

### Task 9: Import the real folder and verify

This task changes the owner's live database, so it follows the same safety steps as the earlier library cleanup. It adds no repository code.

**Files:**
- Modify: `docs/ROADMAP.md` (record the result)

- [ ] **Step 1: Back up and dry-run**

Write `backup.py` and `verify.py` in the session scratchpad directory (never in the repository):

```python
# backup.py  -- usage: python backup.py data/amide.db.bak-price-lists
import sqlite3
import sys

src = sqlite3.connect("file:data/amide.db?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)
print("integrity:", dst.execute("pragma integrity_check").fetchone()[0],
      "| vendors:", dst.execute("select count(*) from vendors").fetchone()[0],
      "| peptides:", dst.execute("select count(*) from peptides").fetchone()[0])
```

```python
# verify.py  -- read-only summary of what the import stored
import sqlite3

c = sqlite3.connect("file:data/amide.db?mode=ro", uri=True)
rows = c.execute("""
    select coalesce(v.name, p.vendor_name), p.warehouse, p.warehouse_source, p.list_date, count(i.id)
    from price_lists p left join vendors v on v.id = p.vendor_id
    left join price_list_items i on i.price_list_id = p.id
    group by p.id order by 1, p.list_date""").fetchall()
for r in rows:
    print(r)
print("lists:", len(rows), "| items:", c.execute("select count(*) from price_list_items").fetchone()[0])
print("vendors:", [r[0] for r in c.execute("select name from vendors order by name")])
```

From `C:\tmp\amide`, with `PYTHONIOENCODING=utf-8 PYTHONPATH=.` set:

```bash
.venv/Scripts/python.exe <scratchpad>/backup.py data/amide.db.bak-price-lists
.venv/Scripts/python.exe tools/import_price_lists.py "$PRICE_LIST_DIR" --dry-run
```

Expected: every PDF with a text layer reports rows (about 75-150 each); a scanned PDF reports `skipped: no price rows found (a scanned PDF needs OCR)`; images and spreadsheets report `not a PDF`. Warehouses: a filename that names China or USA shows `(filename)`, a list whose own text names a warehouse shows `(text)`, and every other list shows `china (assumed)` and is listed at the end. Read the flagged and unmatched lists. Stop and show the owner the report before applying if any vendor's row count is unexpectedly low, or if many rows are flagged `no-code`.

- [ ] **Step 2: Apply**

Run the import command again without `--dry-run`, then `verify.py`. Expected: one list per text-layer PDF (a scanned PDF stores none); a vendor is created for each new filename vendor, and a vendor that already existed (compared ignoring case and the word "Peptides") is reused, never duplicated. Run the import a second time and `verify.py` again: the list and item counts must be identical (idempotent).

- [ ] **Step 3: Correct the one known stale value**

The earlier import of the old sample list left Mazdutide with a wrong `5mg` (see Task 8, Step 1; the true size on that list is `10mg`). Write `fix_mazdutide.py` in the scratchpad and run it with `PYTHONPATH=.`:

```python
# fix_mazdutide.py
from sqlalchemy import select

from app.db import SessionLocal
from app.library.price_lists.specs import format_spec, merge_specs
from app.models import Peptide, PriceListItem

with SessionLocal() as s:
    card = s.scalar(select(Peptide).where(Peptide.name == "Mazdutide"))
    seen = [format_spec(a, u) for a, u in s.execute(
        select(PriceListItem.vial_amount, PriceListItem.vial_unit)
        .where(PriceListItem.peptide_id == card.id, PriceListItem.vial_unit.in_(["mg", "mcg", "IU"]))).all()]
    print("before:", card.library_specifications, "| sizes in the imported lists:", merge_specs(*seen))
    if "5mg" not in seen:
        card.library_specifications = merge_specs("10mg", *seen)
        s.commit()
        print("after:", card.library_specifications)
```

Report the change to the owner. If `5mg` does appear in an imported list, it is a real size: leave the card alone.

- [ ] **Step 4: Record the result and commit**

Add to the "Library price-list import" part of `docs/ROADMAP.md` a short dated note: the folder imported, how many lists and items, which files were skipped and why, and which warehouses were assumed. Then:

```bash
git add docs/ROADMAP.md
git commit -m "docs: record the first price-list import"
```

- [ ] **Step 5: Final whole-branch review**

Run `.venv/Scripts/python.exe -m pytest -q -p no:warnings` once more. Then follow the executing-plans "Final Review" section, passing the reviewer this plan's Review Focus verbatim.

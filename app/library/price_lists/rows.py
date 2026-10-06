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
_PRICE = re.compile(
    r"(?:US\$|\$|USD)?\s*(?P<price>\d+(?:\.\d+)?)\s*(?:USD)?\s*(?:/\s*(?P<per>\d*)\s*vials?)?", re.IGNORECASE)
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
    """"$45" -> (45.0, None); "$30/1vial" and "$30/vial" -> (30.0, 1); "30 USD" -> (30.0, None); else (None, None)."""
    m = _PRICE.fullmatch((text or "").replace(",", "").strip())
    if m is None:
        return None, None
    per = None
    if m.group("per") is not None:
        per = int(m.group("per")) if m.group("per") else 1
    return float(m.group("price")), per


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

"""Dose and vial units: mg, mcg and IU, and converting between them.

Mass units convert exactly. IU and mass need a factor (IU per mg), which depends on the product: for somatropin (HGH) it is
about 3 IU per mg, so a 500 mcg dose is 1.5 IU. Nothing here guesses a factor except the one well-known HGH default."""

import math
import re

_TO_MG = {"mg": 1.0, "mcg": 0.001}
UNITS = ("mg", "mcg", "IU")
HGH_IU_PER_MG = 3.0
_HGH = re.compile(r"(?<![a-z])(hgh|somatropin|growth hormone)", re.IGNORECASE)      # also "HGH191AA"
_DOSE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mg|mcg|µg|μg|ug|iu)\s*$", re.IGNORECASE)


def _canon(unit) -> str | None:
    text = str(unit or "").strip()
    if text.lower() == "iu":
        return "IU"
    return text.lower() if text.lower() in _TO_MG else None


def _factor(iu_per_mg) -> float | None:
    try:
        value = float(iu_per_mg)
    except (TypeError, ValueError):
        return None
    return value if value > 0 and math.isfinite(value) else None


def convert(amount, from_unit, to_unit, iu_per_mg=None) -> float | None:
    """`amount` in `from_unit` expressed in `to_unit`, or None when it cannot be done (an unknown unit, or IU against mass
    without a usable IU-per-mg factor)."""
    src, dst = _canon(from_unit), _canon(to_unit)
    if src is None or dst is None:
        return None
    if src == dst:
        return amount
    if src != "IU" and dst != "IU":
        return amount * _TO_MG[src] / _TO_MG[dst]
    factor = _factor(iu_per_mg)
    if factor is None:
        return None
    if src == "IU":                               # IU -> mg -> the mass unit
        return amount / factor / _TO_MG[dst]
    return amount * _TO_MG[src] * factor          # mass -> mg -> IU


def default_iu_per_mg(name) -> float | None:
    """3 IU per mg for HGH-type products; nothing for anything else."""
    return HGH_IU_PER_MG if name and _HGH.search(str(name)) else None


def parse_dose_text(text) -> tuple[float, str] | None:
    """A library dose such as "500mcg" or "3 IU" as (amount, unit); None for anything else (weight-based, volumes, blanks)."""
    match = _DOSE.match(text or "")
    if not match:
        return None
    unit = match.group(2).lower()
    return float(match.group(1)), ("mcg" if unit in ("µg", "μg", "ug") else "IU" if unit == "iu" else unit)

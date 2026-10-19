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
    return ", ".join(sorted(specs, key=lambda s: (_unit_order(s), _amount(s), s)))


FORM_LABELS = {"dual_chamber": "Dual Chamber Cartridge"}


def format_spec(amount: float, unit: str, form: str | None = None) -> str:
    """"10mg", or "10mg Dual Chamber Cartridge" for a size that comes in a cartridge."""
    label = FORM_LABELS.get(form or "")
    return f"{amount:g}{unit}" + (f" {label}" if label else "")

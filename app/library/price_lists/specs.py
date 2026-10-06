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

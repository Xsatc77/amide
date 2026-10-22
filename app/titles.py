"""Title Case for the card titles Amide builds from labels held in code ("Walking and running distance (mi)" -> "Walking And Running Distance (mi)").

The house style is every word capitalised ("Items In Shipment"). Short units and names in parentheses stay as written, and a word after an apostrophe is not
capitalised ("Today's"). Names a person typed (a peptide, a plan, a vendor) are never passed through this."""

import re

_UNITS = {"mg", "mcg", "ml", "mg/ml", "kg", "lb", "lbs", "oz", "mi", "km", "kcal", "iu", "cm", "in", "ft", "mph", "mi/hr", "ntfy", "hplc", "g", "%", "bpm", "mmhg"}
_PARENTHESIS = re.compile(r"(\([^)]*\))")
_LOWER_START = re.compile(r"(?<![A-Za-z0-9'’])([a-z])")


def _capitalise(text: str) -> str:
    return _LOWER_START.sub(lambda m: m.group(1).upper(), text)


def title_case(text) -> str:
    """Every word starts with a capital; a parenthesis holding a unit stays lowercase, any other short parenthesis is capitalised too."""
    parts = _PARENTHESIS.split(str(text or ""))
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            inner = part[1:-1]
            out.append(part if inner.strip().lower() in _UNITS or len(inner.split()) > 2 else "(" + _capitalise(inner) + ")")
        else:
            out.append(_capitalise(part))
    return "".join(out)

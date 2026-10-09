"""Dose- and schedule-aware cautions for a protocol: dose against the library's range, peptide against peptide, medicine cautions that
grow with the dose, and timing. Informational only: a finding means "worth asking about", never "safe" or "unsafe", and no finding does
not mean there is no interaction. Pure functions: no database, no web."""

from dataclasses import dataclass

from app.library.interactions import GLP1, MELANO, SEDATING, SEDATIVE, _has, cautions_for

DISCLAIMER = "Worth asking your prescriber or pharmacist. Informational only, not medical advice."

GH_SECRETAGOGUES = ("cjc", "ipamorelin", "sermorelin", "tesamorelin", "mk-677", "ibutamoren", "ghrp", "hexarelin")
CLASSES = (("GLP-1 medicines", GLP1), ("growth hormone secretagogues", GH_SECRETAGOGUES), ("melanocortin peptides", MELANO))
DAYTIME = {"fasting", "waking", "am", "pre_workout", "post_workout"}
_TO_MCG = {"mcg": 1.0, "mg": 1000.0}
_ORDER = {"caution": 0, "note": 1}


@dataclass(frozen=True)
class ItemData:
    peptide_id: int
    name: str
    aliases: str | None = None
    dose: float | None = None
    unit: str = "mg"                       # mg, mcg or IU
    time_of_day: str = "any"               # a TimeOfDay value
    steps: tuple = ()                      # titration doses in week order
    lib_low: float | None = None
    lib_mid: float | None = None
    lib_high: float | None = None
    lib_unit: str | None = None
    avoid: tuple = ()                      # (partner name, library note) pairs from the card's Avoid list


@dataclass(frozen=True)
class MedicineData:
    name: str
    dose_text: str | None = None           # quoted back, never parsed


@dataclass(frozen=True)
class Finding:
    severity: str                          # "caution" or "note"
    check: str                             # "dose", "stack", "medicine" or "timing"
    peptide: str
    message: str
    peptide_id: int | None = None
    other: str | None = None

    def as_dict(self) -> dict:
        return {"severity": self.severity, "check": self.check, "peptide": self.peptide, "other": self.other,
                "message": self.message, "peptide_id": self.peptide_id}


def _fmt(value: float) -> str:
    return f"{value:g}"


def _convert(value: float, unit: str, to_unit: str | None) -> float | None:
    if unit == to_unit:
        return value
    if unit in _TO_MCG and to_unit in _TO_MCG:
        return value * _TO_MCG[unit] / _TO_MCG[to_unit]
    return None


def _doses(item: ItemData) -> list[float]:
    return [d for d in (item.dose, *item.steps) if d]


def _check_dose(item: ItemData) -> list[Finding]:
    doses = _doses(item)
    if not doses:
        return []
    out = []

    def add(severity, message):
        out.append(Finding(severity, "dose", item.name, message, item.peptide_id))

    peak, floor = max(doses), min(doses)
    if item.lib_unit is None or (item.lib_low is None and item.lib_high is None):
        add("note", f"{item.name}: the library has no dose range to compare your dose with.")
    else:
        peak_c, floor_c = _convert(peak, item.unit, item.lib_unit), _convert(floor, item.unit, item.lib_unit)
        if peak_c is None:
            add("note", f"{item.name}: your dose is in {item.unit} and the library's range is in {item.lib_unit}, so they cannot be compared.")
        else:
            if item.lib_high is not None and peak_c > item.lib_high:
                add("caution", f"{item.name}: {_fmt(peak)} {item.unit} is above the library's high dose of {_fmt(item.lib_high)} {item.lib_unit}.")
            if item.lib_low is not None and floor_c < item.lib_low / 2:
                add("note", f"{item.name}: {_fmt(floor)} {item.unit} is well below the library's low dose of {_fmt(item.lib_low)} {item.lib_unit}.")
    for before, after in zip(item.steps, item.steps[1:]):
        if before and after > 2 * before:
            add("caution", f"{item.name}: the titration goes from {_fmt(before)} to {_fmt(after)} {item.unit}, more than double the previous step.")
    return out


def _names_match(partner: str, item: ItemData) -> bool:
    partner = partner.casefold().strip()
    text = f"{item.name} {item.aliases or ''}".casefold()
    return len(partner) >= 3 and (partner in text or item.name.casefold() in partner)


def _check_stack(items: list[ItemData]) -> list[Finding]:
    out, seen = [], set()
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a.peptide_id == b.peptide_id:
                continue
            same_slot = a.time_of_day == b.time_of_day
            for label, words in CLASSES:
                if _has(f"{a.name} {a.aliases or ''}", words) and _has(f"{b.name} {b.aliases or ''}", words):
                    when = "in the same time slot" if same_slot else "in different time slots"
                    out.append(Finding("caution" if same_slot else "note", "stack", a.name,
                                       f"{a.name} and {b.name} are both {label}, scheduled {when}. Using two together has little research.",
                                       a.peptide_id, b.name))
            for first, second in ((a, b), (b, a)):
                for partner, note in first.avoid:
                    key = (frozenset((first.peptide_id, second.peptide_id)), note)
                    if _names_match(partner, second) and key not in seen:
                        seen.add(key)
                        out.append(Finding("caution" if same_slot else "note", "stack", first.name,
                                           f"{first.name} with {second.name}: {note}", first.peptide_id, second.name))
    return out


def _check_medicines(items: list[ItemData], medicines: list[MedicineData]) -> list[Finding]:
    out = []
    by_name = {m.name: m for m in medicines}
    for item in items:
        doses = _doses(item)
        peak = _convert(max(doses), item.unit, item.lib_unit) if doses and item.lib_unit else None
        raised = peak is not None and item.lib_mid is not None and peak > item.lib_mid
        for found in cautions_for(item.name, item.aliases, [m.name for m in medicines]):
            med = by_name[found["medicine"]]
            label = f"{med.name} ({med.dose_text})" if med.dose_text else med.name
            text = f"{item.name} with {label}: {found['note']}"
            if raised:
                text += f" At the dose in your protocol ({_fmt(max(doses))} {item.unit}) this matters more."
            out.append(Finding("caution" if raised else "note", "medicine", item.name, text, item.peptide_id, med.name))
    return out


def _check_timing(items: list[ItemData], medicines: list[MedicineData]) -> list[Finding]:
    sedative = next((m for m in medicines if _has(m.name, SEDATIVE)), None)
    if sedative is None:
        return []
    return [Finding("caution", "timing", item.name,
                    f"{item.name} is sedating and is scheduled in a daytime slot while you list {sedative.name}. Drowsiness could add up when you are driving or working.",
                    item.peptide_id, sedative.name)
            for item in items if item.time_of_day in DAYTIME and _has(f"{item.name} {item.aliases or ''}", SEDATING)]


def check(items: list[ItemData], medicines: list[MedicineData]) -> list[Finding]:
    """Every finding for these items and medicines, cautions first (each group keeps the protocol's order)."""
    found = []
    for item in items:
        found += _check_dose(item)
    found += _check_stack(items) + _check_medicines(items, medicines) + _check_timing(items, medicines)
    return sorted(found, key=lambda f: _ORDER[f.severity])

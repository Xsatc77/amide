"""Which fields are required on an inventory item, per medium. Pure, no database access.

Both the server-side form validation and the form's JavaScript (which shows/hides and marks fields
required as the medium changes) read this same table, so the two can never disagree.
"""

from app.models import Medium

REQUIRED_FIELDS: dict[Medium, set[str]] = {
    Medium.LYOPHILIZED: {"vial_size_mg"},
    Medium.LIQUID: {"vial_size_mg", "volume_ml"},
    Medium.AUTOINJECTOR: {"units_per_package"},
    Medium.INHALER: {"vial_size_mg"},
    Medium.PILL: {"vial_size_mg", "units_per_package"},
    Medium.DROPS: {"vial_size_mg"},
    Medium.SALVE: {"vial_size_mg"},
}

# Shown next to the field, and used in "X is required for <medium>" error messages.
FIELD_LABELS: dict[str, str] = {
    "vial_size_mg": "Amount",
    "volume_ml": "Volume (mL)",
    "units_per_package": "Units per package",
}

# Per-medium override of a field's label, purely cosmetic (e.g. "Pills per bottle" reads better than
# the generic "Units per package" on a Pill item). Falls back to FIELD_LABELS when a medium has none.
FIELD_LABEL_OVERRIDES: dict[Medium, dict[str, str]] = {
    Medium.AUTOINJECTOR: {"units_per_package": "Doses per pen"},
    Medium.PILL: {"vial_size_mg": "Amount per pill", "units_per_package": "Pills per bottle"},
}


def required_fields_for(medium: Medium | None) -> set[str]:
    return REQUIRED_FIELDS.get(medium, set()) if medium is not None else set()


def field_label(field: str, medium: Medium | None) -> str:
    if medium is not None and field in FIELD_LABEL_OVERRIDES.get(medium, {}):
        return FIELD_LABEL_OVERRIDES[medium][field]
    return FIELD_LABELS[field]

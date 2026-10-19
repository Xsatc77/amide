"""How a scale's readings are shown on the Measurements page: a chart for each number, and a card with the latest reading."""

from app.measurements.calculations import body_fat_pct

# (field, label, unit text). "(lbs)" in a label is swapped for the person's weight unit; the % / kcal ones are never converted.
SCALE_FIELDS = (
    ("scale_body_fat_pct", "Scale: body fat %", "%"),
    ("scale_fat_mass_lb", "Scale: fat mass (lbs)", "mass"),
    ("scale_subcutaneous_fat_pct", "Scale: subcutaneous fat %", "%"),
    ("scale_visceral_fat", "Scale: visceral fat (rating)", ""),
    ("scale_muscle_pct", "Scale: muscle %", "%"),
    ("scale_muscle_mass_lb", "Scale: muscle mass (lbs)", "mass"),
    ("scale_skeletal_muscle_pct", "Scale: skeletal muscle %", "%"),
    ("scale_skeletal_muscle_mass_lb", "Scale: skeletal muscle mass (lbs)", "mass"),
    ("scale_fat_free_mass_lb", "Scale: fat-free mass (lbs)", "mass"),
    ("scale_water_pct", "Scale: body water %", "%"),
    ("scale_water_mass_lb", "Scale: body water mass (lbs)", "mass"),
    ("scale_protein_pct", "Scale: protein %", "%"),
    ("scale_protein_mass_lb", "Scale: protein mass (lbs)", "mass"),
    ("scale_bone_pct", "Scale: bone %", "%"),
    ("scale_bone_mass_lb", "Scale: bone mass (lbs)", "mass"),
    ("scale_bmi", "Scale: BMI", ""),
    ("scale_bmr_kcal", "Scale: BMR (kcal)", "kcal"),
    ("scale_metabolic_age", "Scale: metabolic age", "years"),
    ("scale_whr", "Scale: waist-to-hip ratio", ""),
)
MASS_FIELDS = tuple(field for field, _, kind in SCALE_FIELDS if kind == "mass")
PERCENT_FIELDS = tuple(field for field, _, kind in SCALE_FIELDS if kind == "%")


def summary(entry) -> str:
    """A short line for the entries table: the scale's body fat and muscle mass when it gave them."""
    parts = []
    if entry.scale_body_fat_pct is not None:
        parts.append(f"{entry.scale_body_fat_pct:g}% body fat")
    if entry.scale_muscle_mass_lb is not None:
        parts.append(f"{entry.scale_muscle_mass_lb:g} muscle")
    return " · ".join(parts)


def latest_card(entries, shown, u, user) -> dict | None:
    """`entries` newest first (stored values); `shown` the same entries in the person's units. The newest reading with any scale number, each value with its
    change from the reading before, and the tape (Navy) body-fat estimate next to the scale's when both exist."""
    indexes = [i for i, e in enumerate(entries) if any(getattr(e, f) is not None for f, _, _ in SCALE_FIELDS)]
    if not indexes:
        return None
    newest = shown[indexes[0]]
    rows = []
    for field, label, kind in SCALE_FIELDS:
        value = getattr(newest, field)
        if value is None:
            continue
        prior = next((getattr(shown[i], field) for i in indexes[1:] if getattr(shown[i], field) is not None), None)
        unit = u.weight_label if kind == "mass" else kind
        short = label.removeprefix("Scale: ").replace(" (lbs)", "")
        short = short[:1].upper() + short[1:]
        rows.append({"label": short.rstrip(), "value": round(value, 1), "unit": unit,
                     "delta": None if prior is None else round(value - prior, 1)})
    tape = None
    scale_fat = getattr(newest, "scale_body_fat_pct")
    if user is not None and user.height_in is not None and user.sex is not None:
        row = next((e for e in entries if e.neck_in is not None and e.waist_in is not None), None)
        if row is not None:
            tape = body_fat_pct(user.sex, user.height_in, row.neck_in, row.waist_in, row.hips_in)
            tape = None if tape is None else round(tape, 1)
    return {"id": entries[indexes[0]].id, "date": newest.measured_at, "rows": rows, "tape_fat": tape, "scale_fat": scale_fat}

"""Premixed products: a liquid listed with a strength per mL ("B12 1mg/ml", 10 mL vials) arrives ready to use, so it is a different product from the
lyophilized powder sold by weight. Its size is a volume, its strength comes from the name, and what is in a vial is the two multiplied."""

from app.library.matching import CONCENTRATION

_TO_MG = {"mg": 1.0, "mcg": 0.001, "ug": 0.001, "µg": 0.001, "μg": 0.001}
PREFIX = "premixed:"


def strength_mg_per_ml(text) -> float | None:
    """mg per mL written in a name ("1mg/ml", "1000mcg/ml", "2.5 mg / mL"), or None when it has no mass strength (IU per mL is not one)."""
    match = CONCENTRATION.search(text or "")
    return float(match.group(1)) * _TO_MG[match.group(2).lower()] if match else None


def premixed_form(item) -> str | None:
    """The form key "premixed:<mg per mL>" of a price line sold by volume whose name states a strength; None for anything else."""
    if item.vial_unit != "ml":
        return None
    strength = strength_mg_per_ml(item.product_name)
    return f"{PREFIX}{strength:g}" if strength else None


def vial_mg(item) -> float | None:
    """mg in one vial of a premixed line (volume times strength), or None when the line is not premixed."""
    strength = strength_mg_per_ml(item.product_name) if item.vial_unit == "ml" else None
    return item.vial_amount * strength if strength and item.vial_amount else None


def strength_of_form(form: str | None) -> float | None:
    return float(form[len(PREFIX):]) if form and form.startswith(PREFIX) else None

"""The arithmetic behind the syringe worked examples on the Learn page, in one place so the numbers shown are computed, not typed.

A U-100 insulin syringe holds 100 units to the millilitre, so units measure volume. How much peptide those units hold depends on the concentration of the mix."""

from dataclasses import dataclass

UNITS_PER_ML = 100


@dataclass(frozen=True)
class Draw:
    concentration_mg_ml: float
    volume_ml: float
    mg: float

    @property
    def mcg(self) -> float:
        return self.mg * 1000


def drawn(units: float, vial_mg: float, water_ml: float) -> Draw:
    """What `units` on a U-100 syringe holds, from a vial of `vial_mg` mixed with `water_ml` of water."""
    if units < 0 or vial_mg <= 0 or water_ml <= 0:
        raise ValueError("units cannot be negative, and the vial amount and water must be above zero")
    concentration = vial_mg / water_ml
    volume = units / UNITS_PER_ML
    return Draw(concentration, volume, volume * concentration)


def units_for(mg_wanted: float, vial_mg: float, water_ml: float) -> float:
    """The units to draw to get `mg_wanted`, working backward from the concentration."""
    if mg_wanted <= 0 or vial_mg <= 0 or water_ml <= 0:
        raise ValueError("every amount must be above zero")
    return mg_wanted / (vial_mg / water_ml) * UNITS_PER_ML


def num(x: float, places: int = 3) -> str:
    """A number for the page: rounded, with no trailing zeros."""
    text = f"{x:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text

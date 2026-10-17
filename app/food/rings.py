"""Donut rings of what was eaten, split by where the calories came from (4 kcal per gram of protein and carbs, 9 per gram of fat)."""

from app.workouts import charts

MACRO_COLORS = {"Protein": "#2563eb", "Carbs": "#ca8a04", "Fat": "#db2777"}

KCAL_PER_GRAM = {"Protein": 4, "Carbs": 4, "Fat": 9}


def macro_ring(protein_g: float, carb_g: float, fat_g: float) -> dict | None:
    """{ring, legend, kcal, colors} for the grams given, or None when nothing was eaten. A macro with no grams is left out of the ring and kept in the legend."""
    grams = {"Protein": protein_g or 0, "Carbs": carb_g or 0, "Fat": fat_g or 0}
    kcal = {name: g * KCAL_PER_GRAM[name] for name, g in grams.items()}
    total = sum(kcal.values())
    if total <= 0:
        return None
    legend = [{"name": n, "grams": round(g), "pct": round(kcal[n] / total * 100), "color": MACRO_COLORS[n]} for n, g in grams.items()]
    return {"ring": charts.ring(list(kcal.items()), size=120, outer=54, inner=34), "legend": legend, "kcal": round(total), "colors": MACRO_COLORS}

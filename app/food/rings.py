"""Donut rings of what was eaten, sized by the grams logged: protein, carbs, fat and fiber exactly as tracked, with nothing converted or subtracted."""

from app.workouts import charts

MACRO_COLORS = {"Protein": "#2563eb", "Carbs": "#ca8a04", "Fat": "#db2777", "Fiber": "#16a34a"}


def macro_ring(protein_g: float, carb_g: float, fat_g: float, fiber_g: float = 0) -> dict | None:
    """{ring, legend, grams, colors} for the grams given, or None when nothing was eaten. A macro with no grams is left out of the ring and kept in the legend."""
    grams = {"Protein": protein_g or 0, "Carbs": carb_g or 0, "Fat": fat_g or 0, "Fiber": fiber_g or 0}
    total = sum(grams.values())
    if total <= 0:
        return None
    legend = [{"name": n, "grams": round(g), "pct": round(g / total * 100), "color": MACRO_COLORS[n]} for n, g in grams.items()]
    return {"ring": charts.ring(list(grams.items()), size=120, outer=54, inner=34), "legend": legend, "total_g": round(total), "colors": MACRO_COLORS}

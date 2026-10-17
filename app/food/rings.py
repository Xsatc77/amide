"""Donut rings of what was eaten, split by where the calories came from: protein and carbs at 4 kcal a gram, fat at 9, fiber at about 2.
Carb totals on a food label include the fiber, so fiber is taken out of the carbs and shown as its own slice rather than counted twice."""

from app.workouts import charts

MACRO_COLORS = {"Protein": "#2563eb", "Carbs": "#ca8a04", "Fat": "#db2777", "Fiber": "#16a34a"}
KCAL_PER_GRAM = {"Protein": 4, "Carbs": 4, "Fat": 9, "Fiber": 2}


def macro_ring(protein_g: float, carb_g: float, fat_g: float, fiber_g: float = 0, extra_fiber_g: float = 0) -> dict | None:
    """{ring, legend, kcal, colors} for the grams given, or None when nothing was eaten. `carb_g` includes the `fiber_g` that came with the food;
    `extra_fiber_g` is fiber that did not (a fiber pill), so it adds to the fiber slice without coming out of the carbs. A macro with no grams is
    left out of the ring and kept in the legend."""
    fiber_g, carb_g = fiber_g or 0, carb_g or 0
    grams = {"Protein": protein_g or 0, "Carbs": max(carb_g - fiber_g, 0), "Fat": fat_g or 0, "Fiber": fiber_g + (extra_fiber_g or 0)}
    kcal = {name: g * KCAL_PER_GRAM[name] for name, g in grams.items()}
    total = sum(kcal.values())
    if total <= 0:
        return None
    legend = [{"name": n, "grams": round(g), "pct": round(kcal[n] / total * 100), "color": MACRO_COLORS[n]} for n, g in grams.items()]
    return {"ring": charts.ring(list(kcal.items()), size=120, outer=54, inner=34), "legend": legend, "kcal": round(total), "colors": MACRO_COLORS}

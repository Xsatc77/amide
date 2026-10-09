"""Suggest foods that fill what is left of today's protein, carbs or fiber with as few carbs as possible. Pure: no database, no web."""

NUTRIENTS = {"protein": "protein_g", "carb": "carb_g", "fiber": "fiber_g"}
SERVINGS = (0.5, 1, 1.5, 2, 3)
CLOSE = 0.15            # within 15% of what is left counts as "close"; among those the lowest-carb one wins
LIMIT = 5


def recommend(foods, nutrient: str, remaining: float, calories_left: float | None = None) -> list[dict]:
    """Up to five (food, servings) picks. Each stays inside the calories left (when known); among the ones within 15% of the amount
    left, the fewest carbs come first; when none are that close, the closest come first. A food appears once, at its best serving."""
    attr = NUTRIENTS[nutrient]
    if remaining <= 0:
        return []
    best: dict[int, dict] = {}
    for food in foods:
        per = getattr(food, attr)
        if per <= 0:
            continue
        for n in SERVINGS:
            if calories_left is not None and food.calories * n > calories_left:
                continue
            gap = abs(per * n - remaining) / remaining
            carbs = food.carb_g * n
            key = (gap > CLOSE, carbs if gap <= CLOSE else 0, gap)
            if food.id not in best or key < best[food.id]["key"]:
                best[food.id] = {"key": key, "food": food, "servings": n, "gap": gap}
    picked = sorted(best.values(), key=lambda p: p["key"])[:LIMIT]
    return [{"id": p["food"].id, "name": p["food"].name, "serving": p["food"].serving, "servings": p["servings"],
             "calories": round(p["food"].calories * p["servings"]), "protein_g": round(p["food"].protein_g * p["servings"], 1),
             "carb_g": round(p["food"].carb_g * p["servings"], 1), "fiber_g": round(p["food"].fiber_g * p["servings"], 1)}
            for p in picked]

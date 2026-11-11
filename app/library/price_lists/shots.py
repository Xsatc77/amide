"""The "vial costs" popup's shot plan: given the size of one shot, which vial size is best, how many shots a vial gives, how much of it is wasted when it
would expire before the last shot, and the real cost per shot with that waste counted. Pure arithmetic on the popup's own data."""

import math

WINDOW_DAYS = 30                    # a mixed vial is used within this many days; what is left after that is thrown away
MG_PER = {"mg": 1.0, "mcg": 0.001}
UNITS = ("mcg", "mg", "IU")
_EPS = 1e-9


def content_in(size: dict, unit: str) -> float | None:
    """What one vial of `size` holds, in the shot's unit; None when they cannot be compared (an IU shot against a vial listed in mg, or a liquid)."""
    if unit in MG_PER:
        return None if size.get("mg") is None else size["mg"] / MG_PER[unit]
    return size.get("iu") if unit == "IU" else None


def _round(value: float) -> float:
    return float(f"{value:.3g}")


def shot_plan(sizes: list[dict], amount: float, unit: str, per_week: float = 7.0, window: int = WINDOW_DAYS) -> dict:
    """One entry per size, in order: `shots` (a whole vial's worth), `used` (inside the window), `expires`, `wasted` (the amount thrown away, in the shot's unit)
    and `wasted_pct`, and `per_shot` (each vendor's price divided by the shots actually used, so waste raises it). `best` is the index of the size with the
    lowest cost per shot, None when no size can be compared."""
    in_window = max(1, math.floor(window * per_week / 7 + _EPS))
    out, best, best_cost = [], None, None
    for i, size in enumerate(sizes):
        content = content_in(size, unit)
        if content is None or content < amount:
            out.append({"shots": 0 if content is not None else None, "used": 0, "expires": False, "wasted": 0, "wasted_pct": 0, "per_shot": []})
            continue
        shots = math.floor(content / amount + _EPS)
        used = min(shots, in_window)
        expires = shots > in_window
        wasted = (content - used * amount) if expires else 0.0
        per_shot = [round(v["price"] / used, 2) for v in size["vendors"]]
        out.append({"shots": shots, "used": used, "expires": expires, "wasted": _round(wasted), "wasted_pct": round(wasted / content * 100) if expires else 0,
                    "per_shot": per_shot})
        if per_shot and (best_cost is None or min(per_shot) < best_cost):
            best, best_cost = i, min(per_shot)
    return {"sizes": out, "best": best, "window": window, "per_week": per_week, "unit": unit, "amount": amount}

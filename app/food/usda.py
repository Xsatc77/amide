"""Live food search in the USDA FoodData Central (https://fdc.nal.usda.gov). Opt-in: it needs a free API key in AMIDE_USDA_API_KEY and only
calls out when someone presses Search USDA in the Add food dialog; without a key nothing here is reachable."""

import json
import urllib.parse
import urllib.request

ENDPOINT = "https://api.nal.usda.gov/fdc/v1/foods/search"
PAGE_SIZE = 15

_NUTRIENTS = {"calories": ("Energy", "KCAL"), "protein_g": ("Protein", "G"), "carb_g": ("Carbohydrate, by difference", "G"),
              "fat_g": ("Total lipid (fat)", "G"), "fiber_g": ("Fiber, total dietary", "G")}


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:                # the key is in the address, so this is never logged here
        return json.loads(response.read().decode("utf-8"))


def _title(text: str) -> str:
    text = " ".join((text or "").split())
    return (text[:1].upper() + text[1:].lower()) if text.isupper() else text


def parse_results(payload: dict) -> list[dict]:
    """The foods in a search response as Amide foods: per 100 g, or per labelled serving (in grams) for branded products. A result with no
    calories is left out."""
    out = []
    for item in payload.get("foods", []) if isinstance(payload, dict) else []:
        per_100 = {}
        for field, (name, unit) in _NUTRIENTS.items():
            for n in item.get("foodNutrients", []):
                if n.get("nutrientName") == name and str(n.get("unitName", "")).upper() == unit and isinstance(n.get("value"), (int, float)):
                    per_100[field] = float(n["value"])
                    break
        if "calories" not in per_100:
            continue
        serving_g, serving = 100.0, "100 g"
        size, unit = item.get("servingSize"), str(item.get("servingSizeUnit", "")).lower()
        if isinstance(size, (int, float)) and size > 0 and unit == "g":
            serving_g = float(size)
            household = (item.get("householdServingFullText") or "").strip()
            serving = f"{household} ({size:g} g)" if household else f"{size:g} g"
        scale = serving_g / 100
        name = _title(item.get("description", ""))
        if item.get("brandOwner"):
            name = f"{name} ({_title(item['brandOwner'])})"
        out.append({"fdc_id": item.get("fdcId"), "name": name[:120], "serving": serving[:60], "serving_g": serving_g,
                    **{field: round(per_100.get(field, 0.0) * scale, 1) for field in _NUTRIENTS}})
    return out


def search(query: str, key: str, fetch=None) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "pageSize": PAGE_SIZE, "api_key": key, "dataType": "Foundation,SR Legacy,Branded"})
    return parse_results((fetch or fetch_json)(url))

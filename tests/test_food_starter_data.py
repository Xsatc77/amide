import json
import re
from pathlib import Path

from app.food.foods import STARTER_PATH, load_starter, parse_food

ROWS = json.loads(Path(STARTER_PATH).read_text(encoding="utf-8"))
ALCOHOL = ("beer", "wine")


def test_the_starter_list_is_a_few_hundred_foods():
    assert 250 <= len(ROWS) <= 400


def test_every_row_is_valid_and_names_are_unique_per_serving():
    seen = set()
    for row in ROWS:
        values, errors = parse_food(row)
        assert errors == {}, (row["name"], errors)
        key = (values["name"].casefold(), values["serving"].casefold())
        assert key not in seen, key
        seen.add(key)


def test_numbers_are_plausible_by_the_atwater_rule():
    for row in ROWS:
        if any(word in row["name"].lower() for word in ALCOHOL):      # alcohol energy is not in protein, carbs or fat
            continue
        energy = 4 * row["protein_g"] + 4 * row["carb_g"] + 9 * row["fat_g"]
        if row["calories"] >= 20:                      # tiny values drift with rounding and fiber handling
            assert 0.75 * row["calories"] <= energy <= 1.3 * row["calories"] + 15, (row["name"], row["calories"], energy)
        assert row["fiber_g"] <= row["carb_g"] + 0.5, row["name"]


def test_the_list_is_simple_everyday_food_with_sensible_servings():
    names = " ".join(r["name"].lower() for r in ROWS)
    for staple in ("egg", "chicken", "rice", "oat", "banana", "peanut butter", "tuna", "yogurt", "potato", "bread", "milk"):
        assert staple in names, staple
    assert all(re.search(r"\d", r["serving"]) for r in ROWS), [r["name"] for r in ROWS if not re.search(r"\d", r["serving"])]


def test_the_loader_accepts_the_whole_shipped_file_idempotently(db):
    load_starter(db)
    assert load_starter(db) == {"added": 0, "updated": 0}


def test_no_brand_names_ship_in_the_data():
    text = json.dumps(ROWS).lower()
    for word in ("mcdonald", "chobani", "kellogg", "ralston"):
        assert word not in text

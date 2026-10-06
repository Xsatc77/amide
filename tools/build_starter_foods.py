"""Build app/food/starter_foods.json from USDA FoodData Central, SR Legacy (public domain).

    python tools/build_starter_foods.py --zip FoodData_Central_sr_legacy_food_csv_2018-04.zip [--review]
    python tools/build_starter_foods.py --zip ... --find "chicken breast cooked"     (list candidate USDA descriptions)

Each line of tools/starter_food_list.txt is:  Display name | serving text | serving grams | match words
The USDA description chosen for a line is the shortest one that contains every match word (case-insensitive, words
separated by spaces; a leading ! on a word means "must not contain"). Per-100 g values are scaled by grams / 100.
A line with no match, or numbers that fail the Atwater plausibility check, is reported and skipped (non-zero exit)."""

import argparse
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "tools" / "starter_food_list.txt"
OUT = ROOT / "app" / "food" / "starter_foods.json"
NUTRIENTS = {1008: "calories", 1003: "protein_g", 1004: "fat_g", 1005: "carb_g", 1079: "fiber_g"}   # kcal, protein, fat, carbs, fiber


def read_csv(archive: zipfile.ZipFile, suffix: str):
    name = next(n for n in archive.namelist() if n.endswith("/" + suffix))
    with archive.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))


def load(zip_path: Path):
    archive = zipfile.ZipFile(zip_path)
    foods = {int(r["fdc_id"]): r["description"] for r in read_csv(archive, "food.csv")}
    values: dict[int, dict] = {}
    for r in read_csv(archive, "food_nutrient.csv"):
        nid = int(r["nutrient_id"])
        if nid in NUTRIENTS and r["amount"]:
            values.setdefault(int(r["fdc_id"]), {})[NUTRIENTS[nid]] = float(r["amount"])
    return foods, values


def matches(foods: dict[int, str], words: str) -> list[tuple[int, str]]:
    want = [w for w in words.lower().split() if not w.startswith("!")]
    ban = [w[1:] for w in words.lower().split() if w.startswith("!") and len(w) > 1]
    hits = [(i, d) for i, d in foods.items()
            if all(w in d.lower() for w in want) and not any(b in d.lower() for b in ban)]
    return sorted(hits, key=lambda h: (len(h[1]), h[1]))


def plausible(row: dict) -> bool:
    energy = 4 * row["protein_g"] + 4 * row["carb_g"] + 9 * row["fat_g"]
    if row["calories"] >= 20 and not (0.75 * row["calories"] <= energy <= 1.3 * row["calories"] + 15):
        return False
    return row["fiber_g"] <= row["carb_g"] + 0.5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--review", action="store_true", help="print the USDA description matched for every line")
    parser.add_argument("--find", help="list USDA descriptions containing these words and exit")
    args = parser.parse_args()
    foods, values = load(args.zip)
    if args.find:
        for fdc_id, description in matches(foods, args.find)[:25]:
            v = values.get(fdc_id, {})
            print(f"{fdc_id}  {description}  | kcal {v.get('calories')} P {v.get('protein_g')} C {v.get('carb_g')} F {v.get('fat_g')} fiber {v.get('fiber_g')}")
        return 0

    rows, problems = [], []
    for number, line in enumerate(LIST.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            name, serving, grams, words = [part.strip() for part in line.split("|")]
            grams = float(grams)
        except ValueError:
            problems.append(f"line {number}: cannot read {line!r}")
            continue
        found = matches(foods, words)
        found = [h for h in found if h[0] in values and all(k in values[h[0]] for k in ("calories", "protein_g", "fat_g", "carb_g"))]
        if not found:
            problems.append(f"line {number}: no USDA match for {name!r} ({words})")
            continue
        fdc_id, description = found[0]
        per100 = {**{k: 0.0 for k in NUTRIENTS.values()}, **values[fdc_id]}     # a missing fiber value counts as 0
        row = {"name": name, "serving": serving, "serving_g": grams}
        for key in NUTRIENTS.values():
            row[key] = round(per100[key] * grams / 100, 1)
        if not (description.lower().startswith("alcoholic beverage") or plausible(row)):     # alcohol energy is not in P/C/F
            problems.append(f"line {number}: implausible numbers for {name!r} from {description!r}: {row}")
            continue
        if args.review:
            print(f"{name} [{serving}] <- {description}  ({row['calories']} kcal)")
        rows.append(row)
    seen = set()
    for row in rows:
        key = (row["name"].casefold(), row["serving"].casefold())
        if key in seen:
            problems.append(f"duplicate: {row['name']} / {row['serving']}")
        seen.add(key)
    rows.sort(key=lambda r: (r["name"].casefold(), r["serving"].casefold()))
    OUT.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} foods to {OUT.relative_to(ROOT)}")
    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

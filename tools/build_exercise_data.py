"""Builds app/workouts/exercise_data.json from the owner's exercise workbook (an .xlsx file).

    python tools/build_exercise_data.py PATH_TO_WORKBOOK.xlsx

Reads the xlsx with the standard library only (an xlsx is a zip of XML). The Compendium speed tables for
walking and running come from tools/compendium_speed_tables.json, so building needs no network.
"""

import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "workouts" / "exercise_data.json"
SPEED_TABLES = Path(__file__).resolve().parent / "compendium_speed_tables.json"

# exercise name -> the speed table that gives its MET (the workbook's flat 3.5 for these is replaced)
SPEED_TABLE_FOR = {
    "Treadmill Walk": "treadmill_walk",
    "Treadmill Incline Walk": "hill_walk",
    "Treadmill Jog": "run",
    "Treadmill Run": "run",
}


def sheet_rows(book: zipfile.ZipFile, sheet_name: str) -> list[dict[str, str]]:
    """Every row of the named sheet as {column letter: text}. Handles inline strings (no shared-strings part)."""
    names = [s.get("name") for s in ET.fromstring(book.read("xl/workbook.xml")).find(_NS + "sheets")]
    index = names.index(sheet_name) + 1
    root = ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in root.find(_NS + "sheetData").findall(_NS + "row"):
        cells = {}
        for cell in row.findall(_NS + "c"):
            inline, value = cell.find(_NS + "is"), cell.find(_NS + "v")
            if inline is not None:
                text = "".join(t.text or "" for t in inline.iter(_NS + "t"))
            else:
                text = value.text if value is not None else None
            if text is not None:
                cells[cell.get("r").rstrip("0123456789")] = text
        rows.append(cells)
    return rows


def number(text: str | None) -> float | None:
    try:
        return round(float(text), 4) if text not in (None, "") else None
    except ValueError:
        return None


def build(workbook: Path) -> dict:
    with zipfile.ZipFile(workbook) as book:
        exercises = []
        for r in sheet_rows(book, "Exercise Database")[1:]:
            if not r.get("A"):
                continue
            name = r["A"].strip()
            exercises.append({
                "name": name, "equipment": r.get("B"), "area": r.get("C"), "pattern": r.get("D"),
                "code": r.get("F"), "met": number(r.get("G")), "evidence": r.get("H"), "style": r.get("J"),
                "sec_per_rep": number(r.get("K")), "rest_min": number(r.get("L")),
                "model": "duration" if r.get("M") == "Duration-based" else "rep",
                "note": r.get("O"), "speed_table": SPEED_TABLE_FOR.get(name),
            })
        known = {e["name"] for e in exercises}
        aliases = {}
        for r in sheet_rows(book, "Exercise Aliases")[1:]:
            alias, target = (r.get("A") or "").strip(), (r.get("B") or "").strip()
            if alias and target in known:
                aliases.setdefault(alias.casefold(), target)
        styles = {}
        for r in sheet_rows(book, "Estimator Assumptions"):
            if r.get("A") and number(r.get("B")) is not None:  # the style table is the rows with a numeric MET
                styles[r["A"].strip()] = {"met": number(r["B"]), "sec_per_rep": number(r.get("C")),
                                          "rest_min": number(r.get("D")), "use": r.get("F")}
        # the Exercise Database labels 14 cardio/conditioning rows "General": the workbook treats them as the default style
        styles.setdefault("General", dict(styles["Hypertrophy / General"]))
        compendium = [{"code": r["A"], "met": number(r["B"]), "description": r.get("C")}
                      for r in sheet_rows(book, "Compendium Source")[1:] if r.get("A")]
    missing = [n for n in SPEED_TABLE_FOR if n not in known]
    if missing:
        raise SystemExit(f"workbook has no row for: {', '.join(missing)}")
    return {
        "source": "2024 Adult Compendium of Physical Activities (pacompendium.com), via the owner's workbook",
        "exercises": exercises, "aliases": aliases, "styles": styles, "compendium": compendium,
        "speed_tables": json.loads(SPEED_TABLES.read_text(encoding="utf-8")),
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    data = build(Path(sys.argv[1]))
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(data['exercises'])} exercises, {len(data['aliases'])} aliases, {len(data['styles'])} styles -> {OUT}")


if __name__ == "__main__":
    main()

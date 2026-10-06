"""Builds app/workouts/exercise_data.json from the owner's exercise workbook (an .xlsx file).

    python tools/build_exercise_data.py PATH_TO_WORKBOOK.xlsx

Reads the xlsx with the standard library only (an xlsx is a zip of XML). The Compendium rows, the resistance
categories, the per-exercise overrides and the speed / power / effort tables come from tools/compendium_data.json
(read from pacompendium.com), so building needs no network. An exercise's MET is its Compendium row's MET: the
workbook's mapped code, or the override where the Compendium names the activity or has a table for it.
"""

import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "workouts" / "exercise_data.json"
COMPENDIUM = Path(__file__).resolve().parent / "compendium_data.json"

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
                "note": r.get("O"), "speed_table": None,
            })
        compendium = json.loads(COMPENDIUM.read_text(encoding="utf-8"))
        rows = compendium["rows"]
        for e in exercises:   # the Compendium row an exercise is estimated from
            override = compendium["overrides"].get(e["name"])
            if override is None:
                continue
            e["evidence"] = override.get("evidence", e["evidence"])
            if "table" in override:
                e["speed_table"] = override["table"]
                default = compendium["tables"][override["table"]].get("default")
                e["code"], e["met"] = (default, rows[default]["met"]) if default else (None, None)
            else:
                e["code"], e["met"] = override["code"], rows[override["code"]]["met"]
        for e in exercises:   # a mapped code must be a known Compendium row, or the MET would be unsourced
            if e["code"] and e["code"] not in rows and not e["speed_table"]:
                raise SystemExit(f"{e['name']}: Compendium code {e['code']} is not in tools/compendium_data.json")
        known = {e["name"] for e in exercises}
        aliases = {}
        for r in sheet_rows(book, "Exercise Aliases")[1:]:
            alias, target = (r.get("A") or "").strip(), (r.get("B") or "").strip()
            if alias and target in known:
                aliases.setdefault(alias.casefold(), target)
    return {
        "source": compendium["source"] + ", via the owner's workbook",
        "exercises": exercises, "aliases": aliases,
        "compendium": [{"code": code, **row} for code, row in sorted(rows.items())],
        "categories": [{**c, "met": rows[c["code"]]["met"]} for c in compendium["categories"]],
        "speed_tables": compendium["tables"],
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    data = build(Path(sys.argv[1]))
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(data['exercises'])} exercises, {len(data['aliases'])} aliases, {len(data['categories'])} categories -> {OUT}")


if __name__ == "__main__":
    main()

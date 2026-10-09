"""Builds app/library/base_library.json, the peptide cards that ship with Amide, from a library database.

    python tools/export_base_library.py [path to amide.db]

One record per base peptide (the names in migration 0003), in the shape app.library.loader.load_sheets reads. Left out on purpose:
price estimates (real prices never ship), the owner's private notes and the card images. The tool refuses to write if a base card has no
summary or if any dollar amount is left in the output."""

import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "library" / "base_library.json"
SHEET_COLUMNS = ("half_life_text", "bioavailability_text", "tmax_text", "route_summary", "storage_before_text", "storage_after_text",
                 "storage_temperature_text", "legal_status_text", "tags", "summary")
DOLLAR = re.compile(r"\$\s?\d")
PAREN_PRICE = re.compile(r"\s*\([^()]*\$\s?\d[^()]*\)")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
NEWLINE = chr(10)
removed = {"parentheticals": 0, "sentences": 0}


def scrub(value):
    """The same data with every dollar amount taken out: a parenthetical that holds one is dropped, then any sentence that still has one."""
    if isinstance(value, str):
        if not DOLLAR.search(value):
            return value
        value, n = PAREN_PRICE.subn("", value)
        removed["parentheticals"] += n
        lines = []
        for line in value.split(NEWLINE):
            kept = [x for x in SENTENCE_END.split(line) if not DOLLAR.search(x)]
            removed["sentences"] += len(SENTENCE_END.split(line)) - len(kept)
            lines.append(" ".join(kept))
        return NEWLINE.join(lines)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value



EXTRA_NAMES = ["HGH Fragment 176-191"]      # shipped cards beyond the 105 seed names


def base_names() -> list[str]:
    path = next(ROOT.glob("migrations/versions/0003_*.py"))
    spec = importlib.util.spec_from_file_location("_seed_0003_export", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CARD_PEPTIDES + module.STARTER_PEPTIDES + EXTRA_NAMES


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def record(db: sqlite3.Connection, name: str) -> dict:
    row = db.execute("SELECT * FROM peptides WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if row is None:
        raise SystemExit(f"{name} is not in the database")
    pid = row["id"]
    rec = {"name": row["name"], "aliases": [a.strip() for a in (row["aliases"] or "").split(", ") if a.strip()]}
    for column in SHEET_COLUMNS:
        rec[column] = _json(row[column]) if column == "tags" else row[column]
    rec["usage_tips"] = _json(row["usage_tips"]) or []
    rec["sheet_sections"] = _json(row["sheet_sections"]) or {}
    rec["sheet_sections_simple"] = _json(row["sheet_sections_simple"]) or {}
    rec["dosing_tiers"] = [dict(level=t["level"], dose_text=t["dose_text"], frequency_text=t["frequency_text"], time_of_day=t["time_of_day"])
                           for t in db.execute("SELECT level, dose_text, frequency_text, time_of_day FROM peptide_dosing_tiers WHERE peptide_id = ? ORDER BY id", (pid,))]
    cycle = db.execute("SELECT on_weeks, off_weeks, note FROM peptide_cycles WHERE peptide_id = ?", (pid,)).fetchone()
    rec["cycle"] = dict(cycle) if cycle else None
    rec["stack_relations"] = [dict(r) for r in db.execute("SELECT partner_name, relation, note FROM peptide_stack_relations WHERE peptide_id = ? ORDER BY id", (pid,))]
    rec["monitoring_tests"] = [dict(r) for r in db.execute("SELECT test_name, when_text, why_text, target_text FROM peptide_monitoring_tests WHERE peptide_id = ? ORDER BY id", (pid,))]
    rec["cost_estimate_text"] = None
    return rec


def main() -> None:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "amide.db"
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    records = sorted((scrub(record(db, n)) for n in base_names()), key=lambda r: r["name"].lower())
    for r in records:
        if not (r["summary"] and r["tags"] and r["sheet_sections_simple"]):
            raise SystemExit(f"{r['name']} has no full card; refusing to write")
    text = json.dumps(records, ensure_ascii=False, indent=1)
    if DOLLAR.search(text):
        raise SystemExit("a dollar amount is still in the data; refusing to write")
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"removed {removed['parentheticals']} price parentheticals and {removed['sentences']} sentences that held a dollar amount")
    print(f"wrote {len(records)} cards to {OUT} ({len(text) // 1024} KB)")


if __name__ == "__main__":
    main()

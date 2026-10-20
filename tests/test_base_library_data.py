"""The shipped base library: 105 names, full cards, and nothing that must not ship."""

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "app" / "library" / "base_library.json"
SEED = next(ROOT.glob("migrations/versions/0003_*.py"))


EXTRA = ["HGH Fragment 176-191", "FST-344"]      # shipped beyond the 105 seed names


def seed_names():
    spec = importlib.util.spec_from_file_location("_seed_0003_data", SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CARD_PEPTIDES + module.STARTER_PEPTIDES


def records():
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_it_has_exactly_the_base_names_plus_the_extra_each_with_a_full_card():
    recs = records()
    assert sorted(r["name"] for r in recs) == sorted(seed_names() + EXTRA)
    for r in recs:
        assert r["summary"] and r["tags"] and r["sheet_sections_simple"], r["name"]


def test_nothing_that_must_not_ship_is_in_the_file():
    text = DATA.read_text(encoding="utf-8")
    assert not re.search(r"\$\s?\d", text)
    for r in records():
        assert not r.get("cost_estimate_text") and not r.get("notes") and not r.get("card_image")


def test_records_have_the_shape_load_sheets_reads():
    for r in records():
        assert isinstance(r["aliases"], list) and isinstance(r["dosing_tiers"], list) and isinstance(r["stack_relations"], list)
        assert isinstance(r["monitoring_tests"], list) and isinstance(r["sheet_sections"], dict) and "cycle" in r


def test_the_fst_344_card_answers_to_every_name_follistatin_is_sold_under_and_takes_over_the_old_entry():
    r = next(x for x in records() if x["name"] == "FST-344")
    names = {a.lower() for a in r["aliases"]}
    for wanted in ("follistatin", "follistatin 344", "follistatin-344", "fs-344", "fs344", "fst344"):
        assert wanted in names, wanted
    assert r["renames"] == ["Follistatin-344"]
    assert all(t["dose_text"] == "Not established" for t in r["dosing_tiers"])          # no human dose exists, so none is invented
    assert len(r["sheet_sections"]["citations"].splitlines() if isinstance(r["sheet_sections"]["citations"], str) else r["sheet_sections"]["citations"]) >= 5

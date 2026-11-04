"""One peptide's Library page as plain blocks a PDF can lay out: the same sections the page shows, without its dosing chart.

A block is {"heading", and any of "paragraphs", "bullets", "facts" ((label, value) pairs), "table" ({"head", "rows"}), "numbered"}.
Sheet-style entries and old card-style entries each have their own sections; an entry with neither says so."""

from app.goals import GOALS

_NARRATIVE = (("what_is", "What Is It?"), ("how_it_works", "How It Works"), ("benefits", "Benefits"), ("side_effects", "Side Effects"),
              ("contraindications", "Contraindications"), ("drug_interactions", "Drug Interactions"), ("legal", "Legal Status"),
              ("who_should_consider", "Who Should Consider This"), ("product_quality", "Product Quality"))
_BULLETED = {"benefits", "contraindications", "drug_interactions", "who_should_consider", "product_quality"}


def _lines(text: str) -> list[str]:
    return [line.strip() for line in str(text).split("\n") if line.strip()]


def _sheet_blocks(p) -> list[dict]:
    blocks = []
    facts = [(label, value) for label, value in (("Half-life", p.half_life_text), ("Route", p.route_summary)) if value]
    if p.cycle:
        facts.append(("Cycle", f"{p.cycle.on_weeks}w on" + (f" / {p.cycle.off_weeks}w off" if p.cycle.off_weeks is not None else "")))
    if p.summary:
        blocks.append({"heading": "", "paragraphs": [p.summary], "lead": True})
    if facts:
        blocks.append({"heading": "Quick Facts", "facts": facts})
    if p.cycle and p.cycle.note:
        blocks.append({"heading": "Cycle Notes", "paragraphs": [p.cycle.note]})
    works = [(r.partner_name, r.note) for r in p.stack_relations if r.relation.value == "works_with"]
    avoid = [(r.partner_name, r.note) for r in p.stack_relations if r.relation.value == "avoid"]
    for heading, rows in (("Works With", works), ("Avoid", avoid)):
        if rows:
            blocks.append({"heading": heading, "bullets": [f"{name}: {note}" if note else name for name, note in rows]})
    if p.monitoring_tests:
        blocks.append({"heading": "Recommended Monitoring", "table": {
            "head": ["Test", "When", "Why", "Target"],
            "rows": [[m.test_name, m.when_text or "-", m.why_text or "-", m.target_text or "-"] for m in p.monitoring_tests]}})
    simple = p.sheet_sections_simple or {}
    for key, heading in _NARRATIVE:
        text = simple.get(key)
        if not text:
            continue
        if key in _BULLETED or (key == "side_effects" and "\n" in text):
            blocks.append({"heading": heading, "bullets": _lines(text)})
        else:
            blocks.append({"heading": heading, "paragraphs": [text]})
    storage = [(label, value) for label, value in (("Before reconstitution", p.storage_before_text), ("After reconstitution", p.storage_after_text),
                                                   ("Temperature", p.storage_temperature_text)) if value]
    if storage:
        blocks.append({"heading": "Storage", "facts": storage})
    blocks.append({"heading": "", "paragraphs": ["From an imported reference sheet, simplified for readability. Informational only, not a recommendation."], "small": True})
    return blocks


def _card_blocks(card: dict) -> list[dict]:
    blocks = []
    if card.get("applications"):
        blocks.append({"heading": "Key Applications", "bullets": [f"{a.get('title', '')}: {a.get('detail', '')}".strip(": ") for a in card["applications"]]})
    how = {"heading": "How It Works"}
    if card.get("mechanism_flow"):
        how["numbered"] = list(card["mechanism_flow"])
    if card.get("mechanism"):
        how["bullets"] = list(card["mechanism"])
    if card.get("clinical_use_note"):
        how["paragraphs"] = [card["clinical_use_note"]]
    if len(how) > 1:
        blocks.append(how)
    evidence = card.get("evidence") or {}
    if evidence.get("points") or evidence.get("level"):
        blocks.append({"heading": "Evidence" + (f" ({str(evidence['level']).title()})" if evidence.get("level") else ""), "bullets": list(evidence.get("points") or [])})
    if card.get("cautions"):
        blocks.append({"heading": "Clinical Caution", "bullets": list(card["cautions"])})
    if card.get("quick_info"):
        blocks.append({"heading": "Quick Practical Info", "facts": [(k, v or "-") for k, v in card["quick_info"].items()]})
    if card.get("regulatory"):
        blocks.append({"heading": "Regulatory Status", "facts": [(k, v or "-") for k, v in card["regulatory"].items()]})
    if card.get("quick_read"):
        blocks.append({"heading": "Clinical Read In 10 Seconds", "bullets": list(card["quick_read"])})
    if card.get("references"):
        blocks.append({"heading": "Key References", "bullets": list(card["references"])})
    blocks.append({"heading": "", "paragraphs": ["From your peptide card. Informational only, not a recommendation."], "small": True})
    return blocks


def library_page(p, goal_slugs, cautions) -> dict:
    """{"name", "subtitle", "tags", "goals", "blocks"} for one Peptide. `cautions` is [(medicine, note)] against the person's own medicine list."""
    is_sheet = p.source.value == "sheet"
    card = p.card_details or {}
    if is_sheet:
        tags = list(p.tags or [])
    else:
        tags = [t for t in (p.card_class, p.category, f"Evidence: {p.evidence_level}" if p.evidence_level else None, p.status) if t]
    blocks = []
    if cautions:
        blocks.append({"heading": "Caution With Your Medicines", "caution": True, "bullets": [f"{m}: {n}" for m, n in cautions],
                       "paragraphs": ["A prompt to ask your prescriber or pharmacist, not advice."]})
    if is_sheet:
        blocks += _sheet_blocks(p)
    elif card:
        blocks += _card_blocks(card)
    else:
        blocks.append({"heading": "", "paragraphs": ["No card has been imported for this peptide."]})
    goals = [g.label for g in GOALS if g.slug in set(goal_slugs)]
    return {"name": p.name, "subtitle": None if is_sheet else card.get("subtitle"), "tags": tags, "goals": goals, "blocks": blocks}

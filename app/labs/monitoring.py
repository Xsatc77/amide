"""Which lab results a peptide's "Recommended Monitoring" list covers.

A monitoring test is free text ("Lipid panel", "Fasting glucose / HbA1c", "Liver enzymes (ALT, AST)")
and a lab result is a marker name ("LDL", "Zinc, Plasma or Serum"). Both are reduced to topics ("ldl",
"zinc"); a result is covered when the two share a topic. A panel word on the monitoring side stands
for its parts ("Lipid panel" covers total cholesterol, LDL, HDL and triglycerides)."""

import re

TOPICS = {
    "glucose": r"glucose|blood sugar", "hba1c": r"a1c|glycated|glycosylated", "insulin": r"insulin(?!-like)",
    "igf1": r"igf-?1\b|\bigf-?i\b|insulin-like growth factor 1", "igfbp3": r"igfbp",
    "hdl": r"(?<![a-z-])hdl", "ldl": r"(?<![a-z])ldl", "vldl": r"vldl", "nonhdl": r"non-?hdl", "trig": r"triglycerid",
    "tsh": r"\btsh\b", "ft3": r"\bt3\b|\bft3\b", "ft4": r"\bt4\b|\bft4\b",
    "alt": r"\balt\b|sgpt|alanine", "ast": r"\bast\b|sgot|aspartate", "alp": r"alkaline phosphatase|\balp\b",
    "bili": r"bilirubin", "ggt": r"\bggt\b",
    "creat": r"creatinine", "egfr": r"egfr|glomerular", "bun": r"\bbun\b|urea nitrogen",
    "wbc": r"\bwbc\b|white blood", "rbc": r"\brbc\b|red blood", "hgb": r"hemoglobin(?!\s*a1c)|\bhgb\b",
    "hct": r"hematocrit", "plt": r"platelet",
    "sodium": r"sodium", "potassium": r"potassium", "chloride": r"chloride", "co2": r"carbon dioxide|bicarbonate|\bco2\b",
    "magnesium": r"magnesium", "zinc": r"\bzinc", "copper": r"\bcopper", "selenium": r"selenium",
    "iron": r"\biron\b|tibc|uibc|transferrin", "ferritin": r"ferritin",
    "vitd": r"vitamin d|25-?hydroxy|25-?oh", "b12": r"b-?12\b|cobalamin", "folate": r"folate|folic",
    "vita": r"vitamin a\b|retinol", "vite": r"vitamin e\b|tocopherol", "vitk": r"vitamin k", "b1": r"vitamin b1\b|vit\. b1|thiamine",
    "pth": r"\bpth\b|parathyroid", "prealbumin": r"prealbumin", "albumin": r"(?<!pre)albumin", "protein": r"total protein|protein, total",
    "crp": r"c-reactive|\bcrp\b", "testosterone": r"testosterone", "estradiol": r"estradiol|\be2\b",
    "lh": r"\blh\b|luteinizing", "fsh": r"\bfsh\b|follicle", "shbg": r"\bshbg\b", "prolactin": r"prolactin", "cortisol": r"cortisol",
    "psa": r"\bpsa\b|prostate-specific", "calcitonin": r"calcitonin", "lipase": r"lipase", "amylase": r"amylase",
    "uric": r"uric acid", "icalcium": r"ionized calcium|calcium, ionized", "calcium": r"calcium",
}
PATTERNS = {topic: re.compile(rx) for topic, rx in TOPICS.items()}

# A panel word on the monitoring side covers its parts.
PANELS = {
    r"\bcbc\b|complete blood count|blood count": {"wbc", "rbc", "hgb", "hct", "plt"},
    r"\bcmp\b|comprehensive metabolic|metabolic panel": {"glucose", "bun", "creat", "egfr", "sodium", "potassium", "chloride", "co2", "calcium", "albumin", "protein", "alt", "ast", "alp", "bili"},
    r"lipid": {"totalchol", "ldl", "hdl", "trig", "nonhdl", "vldl"},
    r"thyroid": {"tsh", "ft3", "ft4"},
    r"liver|hepatic": {"alt", "ast", "alp", "bili"},
    r"renal|kidney": {"creat", "egfr", "bun"},
    r"electrolyte": {"sodium", "potassium", "chloride", "co2"},
    r"sex hormones": {"testosterone", "estradiol"},
}
NOT_BLOOD = re.compile(r"urin|ultrasound|\bscan\b|photograph|palpation|tocometry")      # urine and imaging checks are not lab results


def _topics(text: str) -> set[str]:
    text = text.lower()
    found = {topic for topic, rx in PATTERNS.items() if rx.search(text)}
    if "icalcium" in found:
        found.discard("calcium")
    if "cholesterol" in text and not ({"hdl", "ldl", "vldl", "nonhdl"} & found):
        found.add("totalchol")
    return found


def result_topics(label: str) -> set[str]:
    """The topics one lab result (by its marker name) stands for."""
    return _topics(label)


def test_topics(test_name: str) -> set[str]:
    """The topics one monitoring test covers: its own words plus the parts of any panel it names."""
    if NOT_BLOOD.search(test_name.lower()):
        return set()
    found = _topics(test_name)
    lowered = test_name.lower()
    for rx, parts in PANELS.items():
        if re.search(rx, lowered):
            found |= parts
    if "cholesterol" in lowered and not ({"hdl", "ldl", "vldl", "nonhdl"} & found):
        found |= {"totalchol", "ldl", "hdl"}                    # "cholesterol" on its own means the usual cholesterol numbers
    return found


def peptide_topics(test_names: list[str]) -> set[str]:
    topics: set[str] = set()
    for name in test_names:
        topics |= test_topics(name)
    return topics


def covering(label: str, peptides: list[dict]) -> list[dict]:
    """The peptides (each {"id", "name", "topics"}) whose monitoring covers the lab result called `label`."""
    mine = result_topics(label)
    return [p for p in peptides if mine & p["topics"]]

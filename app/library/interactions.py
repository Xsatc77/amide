"""Medicine and peptide cautions: a short, conservative list of well-known reasons to ask a prescriber or pharmacist.

Informational only. A match means "worth asking about", never "safe" or "unsafe", and an empty result does not mean
there is no interaction: the list is short on purpose and most peptides have little interaction research.
Matching is by words in the medicine name (generic or brand) and in the peptide name or aliases.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    medicines: tuple          # words that identify the medicine
    peptides: tuple           # words that identify the peptides
    note: str


GLP1 = ("semaglutide", "tirzepatide", "retatrutide", "cagrilintide", "liraglutide", "survodutide", "mazdutide")
GH = ("cjc", "ipamorelin", "sermorelin", "tesamorelin", "mk-677", "ibutamoren", "ghrp", "hexarelin", "igf-1", "mecasermin",
      "somatropin", "hgh")
HEALING = ("bpc", "tb-500", "thymosin beta", "ghk")
IMMUNE = ("thymosin alpha", "thymalin", "thymulin", "ll-37", "ta-1")
GROWTH = GH + HEALING
MELANO = ("pt-141", "bremelanotide", "melanotan")
SEDATING = ("dsip",)
NOOTROPIC = ("semax", "selank")

DIABETES = ("insulin", "metformin", "glucophage", "glipizide", "glyburide", "glimepiride", "sitagliptin", "januvia", "jardiance",
            "empagliflozin", "farxiga", "dapagliflozin", "canagliflozin", "pioglitazone", "lantus", "humalog", "novolog", "levemir", "tresiba")
BLOOD_THINNER = ("warfarin", "coumadin", "apixaban", "eliquis", "rivaroxaban", "xarelto", "clopidogrel", "plavix", "heparin",
                 "enoxaparin", "lovenox", "dabigatran", "aspirin", "ticagrelor", "prasugrel")
BLOOD_PRESSURE = ("lisinopril", "enalapril", "losartan", "valsartan", "amlodipine", "metoprolol", "atenolol", "carvedilol",
                  "hydrochlorothiazide", "hctz", "furosemide", "spironolactone", "clonidine", "propranolol", "diltiazem")
VASODILATOR = ("nitroglycerin", "isosorbide", "sildenafil", "viagra", "tadalafil", "cialis", "vardenafil")
ABSORPTION = ("levothyroxine", "synthroid", "liothyronine", "armour thyroid", "birth control", "ethinyl estradiol", "norethindrone",
              "drospirenone", "warfarin")
IMMUNOSUPPRESSANT = ("prednisone", "prednisolone", "dexamethasone", "methotrexate", "azathioprine", "cyclosporine", "tacrolimus",
                     "mycophenolate", "adalimumab", "humira", "etanercept", "enbrel", "infliximab", "remicade", "ustekinumab",
                     "tofacitinib", "rituximab")
CORTICOSTEROID = ("prednisone", "prednisolone", "dexamethasone", "hydrocortisone", "methylprednisolone")
THYROID = ("levothyroxine", "synthroid", "liothyronine", "armour thyroid", "methimazole")
CANCER = ("tamoxifen", "anastrozole", "arimidex", "letrozole", "exemestane", "capecitabine", "imatinib", "chemotherapy",
          "bicalutamide", "enzalutamide", "abiraterone")
SEDATIVE = ("zolpidem", "ambien", "lorazepam", "ativan", "alprazolam", "xanax", "clonazepam", "klonopin", "diazepam", "valium",
            "trazodone", "gabapentin", "pregabalin", "oxycodone", "hydrocodone", "tramadol", "morphine")
MOOD_FOCUS = ("sertraline", "zoloft", "fluoxetine", "prozac", "escitalopram", "lexapro", "citalopram", "bupropion", "wellbutrin",
              "venlafaxine", "duloxetine", "phenelzine", "tranylcypromine", "adderall", "amphetamine", "methylphenidate", "ritalin")

RULES = (
    Rule(DIABETES, GLP1, "Can lower blood sugar further, most of all with insulin or sulfonylureas. Your diabetes medicine dose may need to change."),
    Rule(DIABETES, GH, "Growth hormone and IGF-1 change how the body handles sugar and insulin. IGF-1 LR3 can also lower blood sugar. Watch your readings."),
    Rule(BLOOD_THINNER, HEALING, "These peptides act on blood vessel growth and tissue repair, and the effect on clotting is not well studied. Bruising at injection sites is also more likely."),
    Rule(BLOOD_PRESSURE, GLP1, "Weight loss and reduced fluid intake can lower blood pressure on top of the medicine. Dizziness is worth reporting."),
    Rule(BLOOD_PRESSURE, MELANO, "Can raise blood pressure and heart rate for a while after a dose."),
    Rule(VASODILATOR, MELANO, "Both can change blood pressure; the combination can drop or swing it. Ask before combining."),
    Rule(ABSORPTION, GLP1, "Slower stomach emptying can change how well some tablets are absorbed, such as thyroid and birth control pills. Warfarin levels may need a check."),
    Rule(IMMUNOSUPPRESSANT, IMMUNE, "These peptides stimulate the immune system, which can work against medicine meant to calm it."),
    Rule(CORTICOSTEROID, GH, "Steroids can blunt the growth hormone response to these peptides."),
    Rule(THYROID, GH, "Growth hormone can change thyroid hormone activity, so thyroid dosing may need a recheck."),
    Rule(CANCER, GROWTH, "These peptides promote growth and blood vessel formation, which is a concern during or after cancer treatment. Ask your oncology team first."),
    Rule(SEDATIVE, SEDATING, "Adds to drowsiness. Do not combine with other sedating medicine without asking."),
    Rule(MOOD_FOCUS, NOOTROPIC, "These peptides act on mood and focus pathways and have little interaction research alongside antidepressants or stimulants."),
    Rule(MOOD_FOCUS, MELANO, "Both can cause nausea or a racing heart; the combination is not well studied."),
)


def _has(text: str, words) -> bool:
    text = text.casefold()
    return any(w in text for w in words)


def cautions_for(peptide_name: str, aliases: str | None, medicines: list[str]) -> list[dict]:
    """Every caution between this peptide and the person's medicines, as {"medicine", "note"} (each pair once)."""
    peptide = f"{peptide_name} {aliases or ''}"
    out, seen = [], set()
    for rule in RULES:
        if not _has(peptide, rule.peptides):
            continue
        for med in medicines:
            if _has(med, rule.medicines) and (med, rule.note) not in seen:
                seen.add((med, rule.note))
                out.append({"medicine": med, "note": rule.note})
    return out

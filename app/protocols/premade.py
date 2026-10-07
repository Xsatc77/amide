"""Premade protocols: starting points that open the builder prefilled.

Facts only (peptide, dose, schedule, length, titration) drawn from public label schedules and commonly
published stacks. Nothing here names a person, and the descriptions are written for Amide. Amounts are
typical published ranges, picked from the middle: a starting point to review with a provider, not advice.
"""

from dataclasses import dataclass, field

WEEKDAYS_5ON = "MTWRF"       # five days on, weekends off


@dataclass(frozen=True)
class Item:
    peptide: str
    dose: float
    unit: str = "mcg"
    frequency: str = "daily"          # daily | eod | weekly | weekdays
    weekdays: str = ""
    time_of_day: str = "any"
    route: str = "subq"
    steps: tuple = ()                  # (start_week, end_week or None, dose)
    note: str = ""


@dataclass(frozen=True)
class Premade:
    slug: str
    name: str
    group: str                         # heading on the picker
    goals: tuple
    weeks: int | None
    summary: str
    items: tuple
    titration: bool = False
    caution: str = ""
    aliases: tuple = field(default=())


def _gh(p, dose=200, tod="bedtime"):
    return Item(p, dose, frequency="weekdays", weekdays=WEEKDAYS_5ON, time_of_day=tod)


PREMADE: tuple = (
    # ---- label titrations
    Premade("semaglutide-titration", "Slow Climb: semaglutide, one step a month", "Weight loss", ("glp1-weight",), 20,
            "The step-up schedule on the prescribing label: four weeks at each dose, stepping up every month to the maintenance dose. Hold at a lower step if side effects persist.",
            (Item("Semaglutide", 0.25, "mg", "weekly", route="subq", steps=((1, 4, 0.25), (5, 8, 0.5), (9, 12, 1), (13, 16, 1.7), (17, None, 2.4)),
                  note="Same day each week. Rotate sites."),), titration=True),
    Premade("tirzepatide-titration", "Staircase: tirzepatide, 2.5 mg at a time", "Weight loss", ("glp1-weight",), 24,
            "The label step-up: 2.5 mg for four weeks, then up 2.5 mg every four weeks as tolerated. Many people settle at a middle dose rather than the top one.",
            (Item("Tirzepatide", 2.5, "mg", "weekly", steps=((1, 4, 2.5), (5, 8, 5), (9, 12, 7.5), (13, 16, 10), (17, 20, 12.5), (21, None, 15)),
                  note="Same day each week."),), titration=True),
    Premade("retatrutide-titration", "Triple Threat: retatrutide ramp-up (trial schedule)", "Weight loss", ("glp1-weight",), 20,
            "A slow step-up used in published trials of this still-investigational triple agonist: four weeks per step, doubling about every month.",
            (Item("Retatrutide", 1, "mg", "weekly", steps=((1, 4, 1), (5, 8, 2), (9, 12, 4), (13, 16, 8), (17, None, 12))),),
            titration=True, caution="Not approved anywhere. Trial schedule only; titrate slowly and stop stepping up if side effects are heavy."),
    Premade("semaglutide-low-dose", "Whisper Dose: semaglutide, small and steady", "Weight loss", ("glp1-weight",), None,
            "A small weekly dose, a fraction of the label starting dose, run continuously.",
            (Item("Semaglutide", 0.05, "mg", "weekly"),)),

    # ---- healing
    Premade("bpc-tb-healing", "Patch Kit: BPC-157 + TB-500", "Healing & recovery", ("muscle-recovery",), 8,
            "The common two-peptide recovery pair. BPC-157 twice a day near the injury; TB-500 front-loaded then tapered to a weekly maintenance dose.",
            (Item("BPC-157", 250, frequency="daily", time_of_day="am", note="Inject near the injury when you can. Add a second PM dose if you want twice daily."),
             Item("TB-500", 2, "mg", "weekdays", weekdays="MR", steps=((1, 4, 2), (5, 8, 1)), note="Twice a week for the loading weeks, then once weekly at half the dose."))),
    Premade("bpc-conservative", "Gentle Mend: BPC-157 on its own", "Healing & recovery", ("muscle-recovery",), 6,
            "A single peptide at a modest dose, twice a day, near the injury site.",
            (Item("BPC-157", 250, time_of_day="am"),)),
    Premade("tb500-load-maintain", "Front-Load: TB-500 loading, then weekly upkeep", "Healing & recovery", ("muscle-recovery",), 8,
            "Four weeks of twice-weekly loading, then four weeks at half the dose once a week.",
            (Item("TB-500", 2, "mg", "weekdays", weekdays="MR", steps=((1, 4, 2), (5, 8, 1))),)),
    Premade("wolverine", "Full Repair Crew: healing pair + bedtime GH pair", "Healing & recovery", ("muscle-recovery", "gh-performance",), 8,
            "The healing pair plus a growth-hormone pair at bedtime for recovery. The pairs can be run separately; start with the healing pair if this is new.",
            (Item("BPC-157", 350, time_of_day="am"),
             Item("TB-500", 2.5, "mg", "weekdays", weekdays="MR"),
             Item("Ipamorelin", 200, frequency="weekdays", weekdays=WEEKDAYS_5ON, time_of_day="bedtime"),
             Item("Tesamorelin", 1, "mg", "weekdays", weekdays=WEEKDAYS_5ON, time_of_day="bedtime"))),
    Premade("glow", "Glow Up: GHK-Cu, BPC-157 and TB-500", "Skin & healing", ("skin-beauty", "muscle-recovery",), 10,
            "A skin and tissue repair trio, often bought as one pre-blended vial in roughly a 5:1:1 ratio, injected once daily.",
            (Item("GHK-Cu", 1.7, "mg", note="From a blended vial this is one injection of the blend."),
             Item("BPC-157", 330),
             Item("TB-500", 330))),
    Premade("bpc-kpv-gut", "Calm Gut: BPC-157 + KPV", "Healing & recovery", ("muscle-recovery",), 10,
            "A gentle pair for gut lining and inflammation; both are commonly taken as oral capsules.",
            (Item("BPC-157", 400, route="oral"), Item("KPV", 350, route="oral"))),
    Premade("bpc-kpv-tb", "Cool Down: BPC-157, KPV and TB-500", "Healing & recovery", ("muscle-recovery",), 10,
            "The gut pair plus TB-500 twice a week for whole-body inflammation and repair.",
            (Item("BPC-157", 400), Item("KPV", 350), Item("TB-500", 3, "mg", "weekdays", weekdays="MR"))),
    Premade("immune-support", "Shield Up: thymus and LL-37 immune support", "Healing & recovery", ("wellness",), 6,
            "Thymosin Alpha 1 twice a week with short bursts of a second thymic peptide and a few weeks of LL-37.",
            (Item("Thymosin Alpha 1", 1.5, "mg", "weekdays", weekdays="MR"), Item("LL-37", 100, time_of_day="am", note="Four to six weeks; flu-like effects can occur early on.")),
            caution="LL-37 can cause a temporary flare of symptoms in the first days."),

    # ---- growth hormone / muscle
    Premade("gh-beginner", "Night Shift: CJC-1295 + Ipamorelin starter", "Growth hormone", ("gh-performance",), 10,
            "The usual first growth-hormone stack: both peptides together at bedtime on an empty stomach, five days on and two off, then a four-week break.",
            (Item("CJC-1295 (No DAC)", 200, frequency="weekdays", weekdays=WEEKDAYS_5ON, time_of_day="bedtime"),
             Item("Ipamorelin", 200, frequency="weekdays", weekdays=WEEKDAYS_5ON, time_of_day="bedtime"))),
    Premade("gh-triple", "Triple Pulse: Sermorelin, CJC-1295 and Ipamorelin", "Growth hormone", ("gh-performance", "longevity",), 14,
            "Three growth-hormone signals in one bedtime injection, for longer cycles with breaks.",
            (Item("Sermorelin", 200, time_of_day="bedtime"), Item("CJC-1295 (No DAC)", 200, time_of_day="bedtime"), Item("Ipamorelin", 200, time_of_day="bedtime"))),
    Premade("fat-loss-gh", "Belly Burner: Tesamorelin with the bedtime pair", "Growth hormone", ("fat-loss", "gh-performance",), 10,
            "Tesamorelin in the morning for visceral fat, with the CJC-1295 and Ipamorelin pair at bedtime.",
            (Item("Tesamorelin", 1.5, "mg", time_of_day="am"), Item("CJC-1295 (No DAC)", 200, time_of_day="bedtime"), Item("Ipamorelin", 200, time_of_day="bedtime"))),
    Premade("mass-recovery", "Bulk & Mend: GH pair plus IGF-1 LR3", "Growth hormone", ("gh-performance",), 7,
            "The starter pair plus a short post-workout dose of IGF-1 LR3. Advanced; cycles are kept short.",
            (Item("CJC-1295 (No DAC)", 200, time_of_day="bedtime"), Item("Ipamorelin", 200, time_of_day="bedtime"), Item("IGF-1 LR3", 35, time_of_day="post_workout")),
            caution="Advanced stack. IGF-1 LR3 acts on blood sugar; keep cycles short."),
    Premade("gh-dac-oral", "Slow Release: CJC-1295 DAC with an oral secretagogue", "Growth hormone", ("gh-performance",), None,
            "A long-acting CJC-1295 twice a week, with an oral growth-hormone secretagogue taken nightly.",
            (Item("CJC-1295 (DAC)", 2.5, "mg", "weekdays", weekdays="MR", time_of_day="bedtime"), Item("MK-677 (Ibutamoren)", 25, "mg", "daily", time_of_day="bedtime", route="oral"))),

    # ---- anti-aging
    Premade("longevity-30", "Thirty Days Younger: four-peptide longevity month", "Anti-aging", ("longevity",), 5,
            "Four peptides aimed at different ageing processes over about a month. Start with one or two rather than all four at once.",
            (Item("SS-31 (Elamipretide)", 10, "mg"), Item("Epithalon", 5, "mg", "weekdays", weekdays=WEEKDAYS_5ON),
             Item("Thymosin Alpha 1", 1.5, "mg"), Item("FOXO4-DRI", 10, "mg", "eod", note="Two weeks only, then stop.")),
            caution="Several of these are research compounds with little human data."),
    Premade("epithalon-burst", "Ten-Day Reset: Epithalon burst", "Anti-aging", ("longevity",), 2,
            "A 10-day course, repeated once or twice a year, often paired with Thymosin Alpha 1.",
            (Item("Epithalon", 10, "mg"), Item("Thymosin Alpha 1", 1.5, "mg"))),
    Premade("longevity-gh", "Long Game: Epithalon burst over the GH pair", "Anti-aging", ("longevity", "gh-performance",), 10,
            "The growth-hormone pair for ten weeks, with Epithalon run separately in a short burst.",
            (Item("CJC-1295 (No DAC)", 200, time_of_day="bedtime"), Item("Ipamorelin", 200, time_of_day="bedtime"),
             Item("Epithalon", 5, "mg", note="Two weeks only, then stop; repeat in a few months."))),

    # ---- body composition
    Premade("twelve-week-recomp", "Twelve-Week Overhaul: timed recomposition stack", "Muscle & fat loss", ("gh-performance", "fat-loss",), 12,
            "Several timed injections a day: a growth-hormone releaser each morning, before training and before bed, with AOD-9604 fasted and TB-500 twice weekly. Demanding to follow.",
            (Item("GHRP-6", 200, time_of_day="am", note="Empty stomach; wait 20-30 minutes before eating."),
             Item("CJC-1295 (No DAC)", 100, time_of_day="am"),
             Item("Ipamorelin", 200, time_of_day="pre_workout"),
             Item("GHRP-2", 200, time_of_day="bedtime"),
             Item("AOD-9604", 300, time_of_day="fasting"),
             Item("TB-500", 2, "mg", "weekdays", weekdays="MR")),
            caution="Aggressive stack with many injections a day."),
)

BY_SLUG = {p.slug: p for p in PREMADE}


def groups() -> list[tuple[str, list[Premade]]]:
    """Premades in the order above, under their group headings."""
    out: dict[str, list[Premade]] = {}
    for p in PREMADE:
        out.setdefault(p.group, []).append(p)
    return list(out.items())


def _num(v) -> str:
    return f"{v:g}"


def to_state(premade: Premade, resolve, today) -> dict:
    """Builder state for `premade`. `resolve(name)` returns a peptide id or None (then the name is created on save)."""
    from datetime import timedelta
    items = []
    for it in premade.items:
        pid = resolve(it.peptide)
        first = it.steps[0][2] if it.steps else it.dose
        items.append({
            "peptide_id": "" if pid is None else str(pid), "new_name": "" if pid is not None else it.peptide,
            "dose": _num(first), "dose_unit": it.unit, "frequency": it.frequency, "every_n_days": "",
            "weekdays": it.weekdays, "time_of_day": it.time_of_day, "route": it.route,
            "inventory_item_id": "", "notes": it.note,
            "steps": [{"start_week": str(a), "end_week": "" if b is None else str(b), "dose": _num(d)} for a, b, d in it.steps],
            "cycle_offs": [],
        })
    end = (today + timedelta(weeks=premade.weeks)).isoformat() if premade.weeks else ""
    notes = premade.summary + (f"\n\nCaution: {premade.caution}" if premade.caution else "")
    return {"name": premade.name, "start_date": today.isoformat(), "end_date": end, "weeks": "", "notes": notes,
            "titration": premade.titration or any(i.steps for i in premade.items), "goals": list(premade.goals), "items": items}

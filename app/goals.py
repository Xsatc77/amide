"""The fixed list of protocol goals shown as cards on the Protocols page."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Goal:
    slug: str
    label: str
    description: str
    color: str  # token used to look up --goal-<color> in app.css


SHORT_COURSE_GOAL = "annual-short-term"      # picking this goal turns the builder into the short-course builder (days, per-item run days, repeats)

GOALS: tuple[Goal, ...] = (
    Goal("fat-loss", "Fat Loss / Metabolic", "Metabolism and body composition", "fat-loss"),
    Goal("muscle-recovery", "Muscle & Recovery", "Tissue repair and recovery", "muscle-recovery"),
    Goal("gh-performance", "Growth Hormone / Performance", "GH secretagogues and performance", "gh-performance"),
    Goal("longevity", "Longevity & Cellular Health", "Mitochondrial and cellular health", "longevity"),
    Goal("skin-beauty", "Skin & Beauty", "Skin, hair and appearance", "skin-beauty"),
    Goal("wellness", "Wellness / General Health", "Immune, mood and general support", "wellness"),
    Goal("glp1-weight", "GLP-1 / Weight Management", "Incretin-based weight management", "glp1-weight"),
    Goal("sleep-recovery", "Sleep & Recovery", "Rest and restoration", "sleep-recovery"),
    Goal("supplements", "Vitamins & Supplements", "Non-peptide: vitamins, minerals, supplements", "supplements"),
    Goal("prescriptions", "Prescriptions", "Non-peptide: medicines your doctor prescribed", "prescriptions"),
    Goal(SHORT_COURSE_GOAL, "Annual Short Term Protocol", "A short course that comes round again every year, twice a year or quarterly", "annual-short-term"),
)

GOALS_BY_SLUG: dict[str, Goal] = {g.slug: g for g in GOALS}

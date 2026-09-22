"""The fixed list of protocol goals shown as cards on the Protocols page."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Goal:
    slug: str
    label: str
    description: str


GOALS: tuple[Goal, ...] = (
    Goal("fat-loss", "Fat Loss / Metabolic", "Metabolism and body composition"),
    Goal("muscle-recovery", "Muscle & Recovery", "Tissue repair and recovery"),
    Goal("gh-performance", "Growth Hormone / Performance", "GH secretagogues and performance"),
    Goal("longevity", "Longevity & Cellular Health", "Mitochondrial and cellular health"),
    Goal("skin-beauty", "Skin & Beauty", "Skin, hair and appearance"),
    Goal("wellness", "Wellness / General Health", "Immune, mood and general support"),
    Goal("glp1-weight", "GLP-1 / Weight Management", "Incretin-based weight management"),
    Goal("sleep-recovery", "Sleep & Recovery", "Rest and restoration"),
)

GOALS_BY_SLUG: dict[str, Goal] = {g.slug: g for g in GOALS}

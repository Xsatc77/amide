from app.goals import GOALS_BY_SLUG
from app.library.tag_goals import TAG_GOALS, goal_colors_for_tag


def test_unmapped_tag_returns_empty_list():
    assert goal_colors_for_tag("Not A Real Tag XYZ") == []


def test_every_mapped_slug_is_a_real_goal():
    for tag, slugs in TAG_GOALS.items():
        assert len(slugs) <= 3, f"{tag!r} maps to more than 3 goals"
        for slug in slugs:
            assert slug in GOALS_BY_SLUG, f"{tag!r} maps to unknown goal {slug!r}"


def test_a_known_single_goal_tag():
    # "Tissue Repair" is unambiguously about recovery from injury.
    assert goal_colors_for_tag("Tissue Repair") == ["muscle-recovery"]


def test_a_known_multi_goal_tag():
    # "Gut Health" plausibly relates to general wellness, GLP-1/weight
    # management, and fat loss -- must be exactly these three, in this order.
    assert goal_colors_for_tag("Gut Health") == ["wellness", "glp1-weight", "fat-loss"]


def test_grade_and_regulatory_tags_are_unmapped():
    # Safety grades and regulatory status aren't about a use-case goal.
    for tag in ("Grade A", "Grade B", "Grade C", "Grade D", "FDA-Approved", "Research Only"):
        assert goal_colors_for_tag(tag) == [], f"{tag!r} should be neutral (unmapped)"

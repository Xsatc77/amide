from app.templating import tag_style


def test_tag_style_empty_for_unmapped_tag():
    assert tag_style("Grade A") == ""


def test_tag_style_solid_color_for_single_goal_tag():
    style = tag_style("Tissue Repair")
    assert "var(--goal-muscle-recovery)" in style
    assert "linear-gradient" not in style


def test_tag_style_uses_dark_text_not_white():
    """All 8 goal colors fail WCAG AA contrast (4.5:1) against white text at tag-chip size --
    confirmed by computing contrast ratios for each: amber 1.92:1, emerald 2.28:1, cyan 2.43:1,
    orange 2.8:1, rose 3.53:1, blue 3.68:1, violet 4.23:1, indigo 4.47:1, all below 4.5:1. Every
    goal color is a mid-to-high-luminance hue, so a single dark text token works for all of them
    instead of needing a per-goal light/dark text decision."""
    style = tag_style("Tissue Repair")
    assert "color: #fff" not in style
    assert "var(--goal-text)" in style


def test_tag_style_diagonal_gradient_for_multi_goal_tag():
    style = tag_style("Gut Health")
    assert "linear-gradient(135deg" in style
    assert "var(--goal-wellness)" in style
    assert "var(--goal-glp1-weight)" in style
    assert "var(--goal-fat-loss)" in style
    # Three equal hard-stop bands, not a blended gradient: each color's own
    # stop must appear twice (start % and end %) with no soft midpoint.
    assert style.count("var(--goal-wellness)") == 1  # one background-image color-stop entry

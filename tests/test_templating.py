from app.templating import tag_style


def test_tag_style_empty_for_unmapped_tag():
    assert tag_style("Grade A") == ""


def test_tag_style_solid_color_for_single_goal_tag():
    style = tag_style("Tissue Repair")
    assert "var(--goal-muscle-recovery)" in style
    assert "linear-gradient" not in style


def test_tag_style_diagonal_gradient_for_multi_goal_tag():
    style = tag_style("Gut Health")
    assert "linear-gradient(135deg" in style
    assert "var(--goal-wellness)" in style
    assert "var(--goal-glp1-weight)" in style
    assert "var(--goal-fat-loss)" in style
    # Three equal hard-stop bands, not a blended gradient: each color's own
    # stop must appear twice (start % and end %) with no soft midpoint.
    assert style.count("var(--goal-wellness)") == 1  # one background-image color-stop entry

from app.goals import GOALS, GOALS_BY_SLUG


def test_every_goal_has_a_unique_color_token():
    tokens = [g.color for g in GOALS]
    assert len(tokens) == len(GOALS)
    assert len(set(tokens)) == len(GOALS), "goal color tokens must be unique"
    assert all(tokens)  # none blank


def test_goals_by_slug_exposes_color():
    assert GOALS_BY_SLUG["fat-loss"].color == "fat-loss"

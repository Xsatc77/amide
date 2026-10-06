from types import SimpleNamespace as N

import pytest

from app.food.targets import Totals, day_targets, deficit, fulfilment, sum_totals
from app.models import BiologicalSex, DietPreset, MacroGoal

MALE, FEMALE = BiologicalSex.MALE, BiologicalSex.FEMALE


def test_a_balanced_maintenance_day_splits_calories_by_the_preset():
    t = day_targets(2000, MacroGoal.MAINTAIN, DietPreset.BALANCED, None, MALE)
    assert (t.calories, t.protein_g, t.carb_g, t.fat_g) == (2000, 150, 200, 67)         # 30/40/30 -> 150g, 200g, 66.7g
    assert t.fiber_g == 28 and t.floored is False
    assert t.kcal_split == (600.0, 800.0, 600.0)


def test_the_goal_offset_is_applied_to_tdee():
    assert day_targets(2200, MacroGoal.MODERATE_LOSS, DietPreset.HIGH_PROTEIN, None, MALE).calories == 1700
    assert day_targets(2200, MacroGoal.SLOW_GAIN, DietPreset.HIGH_PROTEIN, None, MALE).calories == 2450


def test_each_diet_type_gives_its_own_split():
    keto = day_targets(2000, MacroGoal.MAINTAIN, DietPreset.KETO, None, MALE)
    assert (keto.protein_g, keto.carb_g, keto.fat_g) == (100, 25, 167)
    low = day_targets(2000, MacroGoal.MAINTAIN, DietPreset.LOW_CARB, None, MALE)
    assert (low.protein_g, low.carb_g, low.fat_g) == (200, 75, 100)


def test_a_custom_split_uses_the_given_percentages():
    t = day_targets(2000, MacroGoal.MAINTAIN, DietPreset.CUSTOM, (25, 50, 25), FEMALE)
    assert (t.protein_g, t.carb_g, t.fat_g) == (125, 250, 56)


def test_a_custom_split_that_does_not_total_100_is_an_error():
    with pytest.raises(ValueError):
        day_targets(2000, MacroGoal.MAINTAIN, DietPreset.CUSTOM, (30, 30, 30), MALE)
    with pytest.raises(ValueError):
        day_targets(2000, MacroGoal.MAINTAIN, DietPreset.CUSTOM, None, MALE)


def test_a_target_below_the_safe_floor_is_raised_and_flagged():
    t = day_targets(1400, MacroGoal.AGGRESSIVE_LOSS, DietPreset.BALANCED, None, FEMALE)
    assert t.calories == 1200 and t.floored is True
    assert day_targets(1900, MacroGoal.AGGRESSIVE_LOSS, DietPreset.BALANCED, None, MALE).calories == 1500


def test_totals_sum_entries_and_an_empty_day_is_all_zero():
    rows = [N(calories=300.5, protein_g=10, carb_g=40, fat_g=5, fiber_g=3), N(calories=199.5, protein_g=20.25, carb_g=0, fat_g=9, fiber_g=1.5)]
    assert sum_totals(rows) == Totals(500.0, 30.25, 40.0, 14.0, 4.5)
    assert sum_totals([]) == Totals(0.0, 0.0, 0.0, 0.0, 0.0)


def test_deficit_counts_tdee_plus_workout_minus_eaten():
    d = deficit(2400, 300, 1800)
    assert (d["kcal"], d["surplus"], d["lb_per_week"]) == (900, False, 1.8)         # 900 * 7 / 3500
    assert deficit(2400, 0, 2400)["kcal"] == 0


def test_eating_more_than_burned_is_a_surplus():
    d = deficit(2000, 100, 2600)
    assert (d["kcal"], d["surplus"], d["lb_per_week"]) == (-500, True, 1.0)


@pytest.mark.parametrize("eaten, state, pct", [(0, "under", 0), (89, "under", 89), (90, "good", 90), (110, "good", 110),
                                                (111, "over", 111), (250, "over", 250)])
def test_fulfilment_states_at_the_boundaries(eaten, state, pct):
    f = fulfilment(eaten, 100)
    assert (f["state"], f["pct"]) == (state, pct) and f["remaining"] == 100 - eaten


def test_fulfilment_never_divides_by_zero():
    assert fulfilment(50, 0) == {"pct": 0, "state": "under", "remaining": -50.0}

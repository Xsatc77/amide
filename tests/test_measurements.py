import pytest

from app.measurements.calculations import (
    bmi, bmr, body_fat_pct, macros_for_preset, target_calories, tdee, water_goal_oz, water_pace,
)
from app.models import ActivityLevel, BiologicalSex, DietPreset, MacroGoal


def test_bmr_male():
    # 180 lb (~81.65 kg), 70 in (~177.8 cm), age 30, male.
    value = bmr(180, 70, 30, BiologicalSex.MALE)
    assert value == pytest.approx(1782.7, abs=1.0)


def test_bmr_female():
    value = bmr(140, 65, 28, BiologicalSex.FEMALE)
    assert value == pytest.approx(1365.9, abs=1.0)


def test_tdee_multiplies_by_activity_value():
    assert tdee(1800, ActivityLevel.SEDENTARY) == pytest.approx(1800 * 1.2)
    assert tdee(1800, ActivityLevel.EXTRA_ACTIVE) == pytest.approx(1800 * 1.9)


def test_target_calories_applies_goal_offset_without_flooring():
    calories, floored = target_calories(2500, MacroGoal.MODERATE_LOSS, BiologicalSex.MALE)
    assert calories == pytest.approx(2000) and floored is False


def test_target_calories_floors_at_1500_for_male():
    calories, floored = target_calories(1800, MacroGoal.AGGRESSIVE_LOSS, BiologicalSex.MALE)
    assert calories == 1500 and floored is True


def test_target_calories_floors_at_1200_for_female():
    calories, floored = target_calories(1500, MacroGoal.AGGRESSIVE_LOSS, BiologicalSex.FEMALE)
    assert calories == 1200 and floored is True


def test_macros_for_balanced_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.BALANCED)
    assert protein == pytest.approx(150) and carb == pytest.approx(200) and fat == pytest.approx(66.7, abs=0.1)


def test_macros_for_keto_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.KETO)
    assert protein == pytest.approx(100) and carb == pytest.approx(25) and fat == pytest.approx(166.7, abs=0.1)


def test_macros_for_custom_preset():
    protein, carb, fat = macros_for_preset(2000, DietPreset.CUSTOM, custom=(35, 35, 30))
    assert protein == pytest.approx(175) and carb == pytest.approx(175) and fat == pytest.approx(66.7, abs=0.1)


def test_macros_for_custom_preset_rejects_ratios_not_summing_to_100():
    with pytest.raises(ValueError):
        macros_for_preset(2000, DietPreset.CUSTOM, custom=(35, 35, 20))


def test_macros_for_custom_preset_requires_custom_ratios():
    with pytest.raises(ValueError):
        macros_for_preset(2000, DietPreset.CUSTOM, custom=None)


def test_water_goal_default_is_half_bodyweight():
    assert water_goal_oz(200, None) == 100


def test_water_goal_uses_override_when_set():
    assert water_goal_oz(200, 120) == 120


def test_water_pace_breaks_down_by_hour():
    pace = water_pace(200)
    assert pace["oz_per_hour"] == pytest.approx(12.5)
    assert pace["cups_per_hour"] == pytest.approx(1.5625)
    assert pace["bottles_per_hour"] == pytest.approx(0.7396, abs=0.001)


def test_bmi():
    assert bmi(180, 70) == pytest.approx(25.83, abs=0.01)


def test_body_fat_pct_male():
    value = body_fat_pct(BiologicalSex.MALE, height_in=70, neck_in=15, waist_in=34)
    assert value == pytest.approx(11.05, abs=0.1)


def test_body_fat_pct_female_requires_hips():
    assert body_fat_pct(BiologicalSex.FEMALE, height_in=65, neck_in=13, waist_in=28) is None


def test_body_fat_pct_female_with_hips():
    value = body_fat_pct(BiologicalSex.FEMALE, height_in=65, neck_in=13, waist_in=28, hips_in=38)
    assert value is not None and value == pytest.approx(2.93, abs=0.1)

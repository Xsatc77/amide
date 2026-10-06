"""The expected numbers below were read from tdeecalculator.org's own output for these profiles (numbers only)."""

import pytest

from app.measurements.tdee import LIFE_STAGES, MACRO_GRID, bmi_category, macro_cell, report, round_half_up

# (male, age, height_in, weight_lb, activity_factor, life_stage) -> the site's figures
MODERATE_MAN = dict(male=True, age=30, height_in=71, weight_lb=176, activity_factor=1.55)
LIGHT_WOMAN = dict(male=False, age=28, height_in=64, weight_lb=140, activity_factor=1.375)
SEDENTARY_PERI = dict(male=False, age=45, height_in=66, weight_lb=170, activity_factor=1.2, life_stage="perimenopause")
ATHLETE_MAN = dict(male=True, age=55, height_in=70, weight_lb=220, activity_factor=1.9)
BREASTFEEDING = dict(male=False, age=32, height_in=65, weight_lb=150, activity_factor=1.55, life_stage="breastfeeding")


def test_moderate_man():
    r = report(**MODERATE_MAN)
    assert (r.bmr, r.tdee_unadjusted, r.tdee) == (1780, 2760, 2760)
    assert [f[1] for f in r.bmr_formulas] == [1780, 1853, 1697, 1852] and r.bmr_average == 1796
    assert r.split == (1780, 704, 276) and r.split_pct == (64, 26, 10)
    assert (r.bmi, r.bmi_category, r.lbm_kg, r.lbm_lb, r.min_calories) == (24.5, "Normal", 61.4, 135.4, 1500)
    assert [g[2] for g in r.goals] == [1760, 2010, 2260, 2760, 3010, 3260]
    assert [lv[2] for lv in r.levels] == [2136, 2448, 2759, 3071, 3382]
    assert r.life_stage is None and r.life_stage_note == "No life-stage adjustment applied."


def test_light_woman():
    r = report(**LIGHT_WOMAN)
    assert (r.bmr, r.tdee) == (1350, 1856)
    assert [f[1] for f in r.bmr_formulas] == [1350, 1417, 1333, 1481] and r.bmr_average == 1395
    assert r.split == (1350, 320, 186) and r.split_pct == (73, 17, 10)
    assert (r.bmi, r.bmi_category, r.lbm_kg, r.lbm_lb, r.min_calories) == (24.0, "Normal", 44.6, 98.3, 1200)
    assert [g[2] for g in r.goals] == [856, 1106, 1356, 1856, 2106, 2356]      # the ladder is not floored
    assert [lv[2] for lv in r.levels] == [1620, 1856, 2093, 2329, 2565]


def test_sedentary_perimenopause_clips_activity_at_zero():
    r = report(**SEDENTARY_PERI)
    assert (r.bmr, r.tdee_unadjusted, r.tdee) == (1433, 1719, 1544)
    assert [f[1] for f in r.bmr_formulas] == [1433, 1485, 1459, 1609] and r.bmr_average == 1497
    assert r.split == (1433, 0, 154) and r.split_pct == (90, 0, 10)
    assert (r.bmi, r.bmi_category, r.lbm_kg, r.lbm_lb) == (27.4, "Overweight", 50.4, 111.1)
    assert [lv[2] for lv in r.levels] == [1545, 1795, 2046, 2297, 2548]
    assert [g[2] for g in r.goals] == [544, 794, 1044, 1544, 1794, 2044]
    assert r.life_stage_note == "Perimenopause -175 kcal/day: adjusted TDEE 1,544 kcal/day."


def test_athlete_man():
    r = report(**ATHLETE_MAN)
    assert (r.bmr, r.tdee, r.split, r.split_pct) == (1839, 3494, (1839, 1306, 349), (53, 37, 10))
    assert [f[1] for f in r.bmr_formulas] == [1839, 1966, 1858, 2016] and r.bmr_average == 1920
    assert (r.bmi, r.bmi_category) == (31.6, "Obese Class I")
    assert [lv[2] for lv in r.levels] == [2207, 2529, 2850, 3172, 3494]


def test_breastfeeding_adds_400():
    r = report(**BREASTFEEDING)
    assert (r.bmr, r.tdee_unadjusted, r.tdee) == (1391, 2156, 2556)
    assert r.split == (1391, 909, 256) and r.split_pct == (54, 36, 10)
    assert [lv[2] for lv in r.levels] == [2069, 2313, 2556, 2799, 3043]
    assert (r.lbm_kg, r.lbm_lb) == (46.9, 103.4)


@pytest.mark.parametrize("stage,tdee,levels,pct", [
    ("luteal", 2006, [1770, 2006, 2243, 2479, 2715], (67, 23, 10)),
    ("pregnancy_1", 1856, [1620, 1856, 2093, 2329, 2565], (73, 17, 10)),
    ("pregnancy_2", 2196, [1960, 2196, 2433, 2669, 2905], (61, 29, 10)),
    ("pregnancy_3", 2308, [2072, 2308, 2545, 2781, 3017], (58, 31, 10)),
    ("breastfeeding", 2256, [2020, 2256, 2493, 2729, 2965], (60, 30, 10)),
    ("perimenopause", 1681, [1445, 1681, 1918, 2154, 2390], (80, 10, 10)),
])
def test_each_life_stage_against_the_site(stage, tdee, levels, pct):
    r = report(male=False, age=28, height_in=64, weight_lb=140, activity_factor=1.375, life_stage=stage)
    assert r.tdee == tdee and [lv[2] for lv in r.levels] == levels and r.split_pct == pct


def test_pcos_applies_the_stated_six_percent_of_bmr_unlike_the_site():
    r = report(male=False, age=28, height_in=64, weight_lb=140, activity_factor=1.375, life_stage="pcos")
    assert r.tdee_unadjusted == 1856 and r.tdee == round_half_up(1856.03 - 0.06 * 1350.03)


def test_life_stages_are_female_only_and_unknown_ones_are_ignored():
    man = report(**MODERATE_MAN, life_stage="luteal")
    assert man.tdee == 2760 and man.life_stage is None
    assert report(**{**LIGHT_WOMAN, "life_stage": "nonsense"}).tdee == 1856
    assert set(LIFE_STAGES) == {"luteal", "pregnancy_1", "pregnancy_2", "pregnancy_3", "breastfeeding",
                                "perimenopause", "pcos"}


def test_the_breastfeeding_floor_holds_for_a_small_person():
    r = report(male=False, age=60, height_in=58, weight_lb=95, activity_factor=1.2, life_stage="breastfeeding")
    assert r.tdee == 1800 and all(lv[2] >= 1800 for lv in r.levels)


@pytest.mark.parametrize("kwargs,bmi,category,bmr", [
    (dict(male=False, age=25, height_in=70, weight_lb=118, activity_factor=1.2), 16.9, "Underweight", 1360),
    (dict(male=True, age=40, height_in=66, weight_lb=205, activity_factor=1.2), 33.1, "Obese Class I", 1783),
    (dict(male=True, age=40, height_in=66, weight_lb=224, activity_factor=1.2), 36.2, "Obese Class II", 1869),
    (dict(male=True, age=40, height_in=66, weight_lb=265, activity_factor=1.2), 42.8, "Obese Class III", 2055),
])
def test_bmi_categories_and_bmr_against_the_site(kwargs, bmi, category, bmr):
    r = report(**kwargs)
    assert (r.bmi, r.bmi_category, r.bmr) == (bmi, category, bmr)


def test_bmi_category_boundaries():
    assert [bmi_category(x) for x in (18.49, 18.5, 24.99, 25.0, 29.99, 30.0, 34.99, 35.0, 39.99, 40.0)] == [
        "Underweight", "Normal", "Normal", "Overweight", "Overweight", "Obese Class I", "Obese Class I",
        "Obese Class II", "Obese Class II", "Obese Class III"]


def test_the_macro_grid_matches_the_sites_percentages_and_grams():
    assert {k: (v["delta"], v["low"], v["moderate"], v["high"]) for k, v in MACRO_GRID.items()} == {
        "cut": (-500, (40, 20, 40), (40, 30, 30), (35, 45, 20)),
        "maintain": (0, (30, 30, 40), (30, 40, 30), (25, 50, 25)),
        "bulk": (500, (30, 30, 40), (25, 50, 25), (20, 60, 20))}
    cell = macro_cell("maintain", "moderate", 2760)
    assert cell["grams"] == (207, 276, 92) and cell["per_meal"] == (52, 69, 23) and cell["kcal_per_meal"] == 690
    assert macro_cell("maintain", "moderate", 1856)["grams"] == (139, 186, 62)
    assert macro_cell("cut", "low", 2000)["calories"] == 1500

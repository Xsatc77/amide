import html
import re
from datetime import date, timedelta

import pytest

from app.models import BodyMeasurement
from test_workout_logging import plan_with, post_log


@pytest.fixture
def profile(client, db, me):
    """Sets the test user's body profile and a single weigh-in; resets both afterwards."""
    def set_profile(sex="Male", age=30, height="71", activity="1.55", life_stage="", weight=176.0):
        today = date.today()
        birth = date(today.year - age, today.month, min(today.day, 28))
        client.post("/settings/body-profile", data={"sex": sex, "birth_date": birth.isoformat(), "height_in": height,
                                                    "activity_level": activity, "life_stage": life_stage})
        db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
        if weight:
            db.add(BodyMeasurement(owner_id=me, measured_at=today - timedelta(days=1), weight_lbs=weight))
        db.commit()
    yield set_profile
    client.post("/settings/body-profile", data={})
    db.query(BodyMeasurement).filter(BodyMeasurement.owner_id == me).delete()
    db.commit()


def energy(client, **params):
    return html.unescape(client.get("/workouts/energy", params=params).text)


def test_the_energy_tab_is_linked_from_the_workouts_page(client):
    page = client.get("/workouts").text
    assert 'href="/workouts/energy"' in page and 'href="/workouts/progress"' in page


def test_missing_profile_data_is_named_instead_of_calculated(client, db, me, profile):
    profile(sex="", age=30, height="", activity="", weight=None)
    page = energy(client)
    assert "Missing:" in page and "sex" in page and "height" in page and "activity level" in page and "weigh-in" in page
    assert "BMR" not in page


def test_the_numbers_match_the_reference_calculator_for_a_moderate_man(client, profile):
    profile()                                                  # man, 30, 71 in, 176 lb, 1.55
    page = energy(client)
    for expected in ("2,760", "1,780", "24.5", "Normal", "135.4", "1,500"):
        assert expected in page
    ladder = re.findall(r"<td>([\d,]+) kcal</td>", page.split("Goal ladder", 1)[1].split("</table>", 1)[0])
    assert ladder == ["1,760", "2,010", "2,260", "2,760", "3,010", "3,260"]
    for formula, value in (("Mifflin-St Jeor", "1,780"), ("Harris-Benedict", "1,853"), ("Katch-McArdle", "1,697"),
                           ("Cunningham", "1,852")):
        assert re.search(rf"{formula}[^<]*</td>\s*<td[^>]*>{value}", page)
    assert "64%" in page and "26%" in page and "10%" in page          # BMR / activity / food digestion
    for level in ("2,136", "2,448", "2,759", "3,071", "3,382"):
        assert level in page


def test_a_woman_with_a_life_stage_gets_the_adjusted_tdee(client, profile):
    profile(sex="Female", age=45, height="66", activity="1.2", life_stage="perimenopause", weight=170.0)
    page = energy(client)
    assert "1,544" in page and "1,719" in page and "Perimenopause" in page and "1,200" in page


def test_pcos_says_it_applies_the_stated_six_percent(client, profile):
    profile(sex="Female", age=28, height="64", activity="1.375", life_stage="pcos", weight=140.0)
    page = energy(client)
    assert "-6%" in page and "applies no change" in page


def test_the_macro_grid_selection_changes_the_grams_and_bad_values_fall_back(client, profile):
    profile()
    default = energy(client)
    assert "207 g" in default and "276 g" in default and "92 g" in default           # maintain, moderate carb
    cut = energy(client, goal="cut", carb="low")                                      # 2,260 kcal at 40/20/40
    assert "226 g" in cut and "113 g" in cut and "100 g" in cut
    assert "207 g" in energy(client, goal="nonsense", carb="also nonsense")


def test_the_burn_chart_stacks_the_days_workout_on_the_tdee_baseline(client, db, profile):
    profile()
    plan = plan_with(client, db, ["Push-up"], name="Energy Chart Plan")
    day, ex = plan.days[0], plan.days[0].exercises[0]
    post_log(client, day, log_date=date.today().isoformat(),
             **{f"completed[{ex.id}]": "on", f"sets_value[{ex.id}]": "4", f"reps_value[{ex.id}]": "15"})
    page = energy(client, range="7")
    assert 'class="price-chart wk-chart"' in page and page.count('class="wk-bar"') == 7
    assert re.search(r"workout about \d+ kcal net", page)
    for legend in ("BMR", "Activity", "Food digestion", "Workout", "Goal target"):
        assert legend in page


def test_a_period_without_workouts_still_draws_the_baseline_and_says_so(client, profile):
    profile()
    page = energy(client, range="30")
    assert page.count('class="wk-bar"') == 30 and "No workouts logged in this period" in page


def test_unknown_ranges_fall_back_to_thirty_days(client, profile):
    profile()
    assert energy(client, range="9999").count('class="wk-bar"') == 30


def test_the_chart_follows_weight_changes_over_time(client, db, me, profile):
    """Each day uses the weigh-in in effect that day: heavier earlier, lighter later, so the daily TDEE falls."""
    profile(weight=None)
    db.add_all([BodyMeasurement(owner_id=me, measured_at=date.today() - timedelta(days=25), weight_lbs=220.0),
                BodyMeasurement(owner_id=me, measured_at=date.today() - timedelta(days=3), weight_lbs=176.0)])
    db.commit()
    tips = re.findall(r'(\d\d/\d\d/\d{4}): TDEE ([\d,]+) kcal', energy(client, range="30"))
    by_day = {d: int(v.replace(",", "")) for d, v in tips}
    early = (date.today() - timedelta(days=20)).strftime("%m/%d/%Y")
    late = date.today().strftime("%m/%d/%Y")
    assert by_day[early] > by_day[late] + 200                       # 220 lb burns clearly more than 176 lb
    assert by_day[late] == 2760                                      # the reference figure for the man at 176 lb

import html
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.measurements.calculations import bmr, macros_for_preset, target_calories, tdee
from app.models import (ActivityLevel, BiologicalSex, BodyMeasurement, DietPreset, MacroGoal,
                         Share, ShareCategory, User)


def _logged_in_client(username: str, password: str = "Other1!") -> TestClient:
    """A second user's own logged-in TestClient (registering already signs them in). Mirrors
    tests/test_vendors_page.py's _logged_in_client."""
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": username, "password": password, "confirm": password})
    return other


def _text(response) -> str:
    return html.unescape(response.text)


def _clear_measurements(*owner_ids: int) -> None:
    with SessionLocal() as s:
        for oid in owner_ids:
            s.query(BodyMeasurement).filter_by(owner_id=oid).delete()
        s.commit()


def test_measurements_page_loads_with_empty_state(client, db, me):
    try:
        r = client.get("/measurements")
        assert r.status_code == 200
        assert "No measurements logged yet" in r.text or "Log a measurement" in r.text
    finally:
        _clear_measurements(me)


def test_new_measurement_entry_all_fields_optional(client, db, me):
    try:
        r = client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "180"},
                        follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            [bm] = s.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == tester.id)).all()
            assert bm.weight_lbs == 180.0 and bm.neck_in is None
    finally:
        _clear_measurements(me)


def test_new_measurement_entry_full_session(client, db, me):
    try:
        r = client.post("/measurements", data={
            "measured_at": "2026-09-28", "weight_lbs": "180", "systolic": "120", "diastolic": "80",
            "heart_rate_bpm": "62",
            "neck_in": "15.5", "waist_in": "34", "hips_in": "38",
            "biceps_l_in": "16", "biceps_r_in": "16.3",
            "forearm_l_in": "12", "forearm_r_in": "12.1",
            "quad_l_in": "22", "quad_r_in": "22.2",
            "calf_l_in": "15", "calf_r_in": "15.1",
        }, follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            [bm] = s.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == tester.id)).all()
            assert bm.biceps_l_in == 16.0 and bm.biceps_r_in == 16.3
            assert bm.heart_rate_bpm == 62
    finally:
        _clear_measurements(me)


def test_new_measurement_rejects_non_positive_weight(client, db, me):
    try:
        r = client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "-5"})
        assert r.status_code == 422
    finally:
        _clear_measurements(me)


def test_measurements_page_respects_personal_data_sharing(client, db, me):
    other = _logged_in_client("MeasureSharePartner")
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "measuresharepartner"))
    try:
        r = other.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "222.2"},
                       follow_redirects=False)
        assert r.status_code == 303

        # No Share yet -- the signed-in test user must not see the other user's measurement.
        t_before = _text(client.get("/measurements"))
        assert "222.2" not in t_before

        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
            s.commit()

        # Now visible, tagged with the owner's name (matching this app's established shared-item
        # display convention, e.g. tests/test_vendors_page.py's "Shared by" column).
        t_after = _text(client.get("/measurements"))
        assert "222.2" in t_after
        assert "MeasureSharePartner" in t_after
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.PERSONAL_DATA).delete()
            s.commit()
        _clear_measurements(me, other_id)


def _tester_id() -> int:
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


def _clear_body_profile() -> None:
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username_key == "tester"))
        u.sex = u.birth_date = u.height_in = u.activity_level = None
        u.macro_goal = u.diet_preset = u.water_goal_oz = None
        u.custom_protein_pct = u.custom_carb_pct = u.custom_fat_pct = None
        s.commit()


def test_silhouette_shows_average_of_bilateral_measurement(client, db):
    tester = _tester_id()
    try:
        client.post("/measurements", data={
            "measured_at": "2026-09-20", "biceps_l_in": "16", "biceps_r_in": "16",
        })
        client.post("/measurements", data={
            "measured_at": "2026-09-27", "biceps_l_in": "16", "biceps_r_in": "16.3",
        })
        t = html.unescape(client.get("/measurements").text)
        assert "16.15" in t  # average of the most recent entry's two sides
    finally:
        _clear_measurements(tester)


def test_silhouette_shows_change_since_previous_entry(client, db):
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-20", "waist_in": "34"})
        client.post("/measurements", data={"measured_at": "2026-09-27", "waist_in": "33.5"})
        t = html.unescape(client.get("/measurements").text)
        assert "-0.5" in t or "−0.5" in t
    finally:
        _clear_measurements(tester)


def test_silhouette_shows_single_side_when_other_side_missing(client, db):
    """Review Focus item 1: a bilateral measurement missing one side must show that one side
    alone, clearly labeled -- never averaged with None/zero."""
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-27", "quad_l_in": "22"})
        t = html.unescape(client.get("/measurements").text)
        assert "22.0" in t or "22" in t
        # It must not silently render as if it were a full average (e.g. 11.0 = (22+0)/2).
        assert "11.0" not in t
    finally:
        _clear_measurements(tester)


def test_macros_tab_shows_prompt_when_profile_incomplete(client, db):
    tester = _tester_id()
    try:
        t = client.get("/measurements?tab=macros").text
        assert "profile" in t.lower() or "add your" in t.lower()
    finally:
        _clear_measurements(tester)


def test_macros_tab_computes_from_stored_profile_and_latest_weight(client, db):
    tester = _tester_id()
    try:
        client.post("/settings/body-profile", data={
            "sex": "Male", "birth_date": "1996-01-01", "height_in": "70",
            "activity_level": "1.55", "macro_goal": "0", "diet_preset": "balanced",
        })
        client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "180"})
        r = client.get("/measurements?tab=macros")
        assert r.status_code == 200

        with SessionLocal() as s:
            user = s.scalar(select(User).where(User.username_key == "tester"))
            birth_date = user.birth_date
        today = __import__("datetime").date.today()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))

        bmr_value = bmr(180.0, 70.0, age, BiologicalSex.MALE)
        tdee_value = tdee(bmr_value, ActivityLevel.MODERATELY_ACTIVE)
        calories, floored = target_calories(tdee_value, MacroGoal.MAINTAIN, BiologicalSex.MALE)
        protein_g, carb_g, fat_g = macros_for_preset(calories, DietPreset.BALANCED)

        t = html.unescape(r.text)
        assert str(round(calories)) in t
        assert str(round(protein_g)) in t
        assert str(round(carb_g)) in t
        assert str(round(fat_g)) in t
        if floored:
            assert "adjust" in t.lower()
    finally:
        _clear_measurements(tester)
        _clear_body_profile()


def test_macros_tab_shows_floor_notice_when_calories_floored(client, db):
    """Review Focus item 2: when target_calories() floors the number, the UI must show a
    visible 'adjusted' notice."""
    tester = _tester_id()
    try:
        client.post("/settings/body-profile", data={
            "sex": "Female", "birth_date": "2000-01-01", "height_in": "60",
            "activity_level": "1.2", "macro_goal": "-1000", "diet_preset": "balanced",
        })
        client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "100"})
        r = client.get("/measurements?tab=macros")
        assert r.status_code == 200
        t = html.unescape(r.text)
        assert "adjust" in t.lower()
    finally:
        _clear_measurements(tester)
        _clear_body_profile()


def test_water_goal_and_pace_shown_on_measurements_tab(client, db):
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "200"})
        t = client.get("/measurements").text
        assert "100" in t  # default water goal: 200/2
    finally:
        _clear_measurements(tester)


def test_charts_default_to_a_range(client, db, me):
    try:
        r = client.get("/measurements")
        assert r.status_code == 200
    finally:
        _clear_measurements(me)


def test_charts_range_query_param_changes_window(client, db, me):
    old = date(2026, 1, 1)
    recent = date(2026, 9, 28)
    try:
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            s.add(BodyMeasurement(owner_id=tester.id, measured_at=old, weight_lbs=190))
            s.add(BodyMeasurement(owner_id=tester.id, measured_at=recent, weight_lbs=180))
            s.commit()
        r_lifetime = client.get("/measurements?range=lifetime")
        r_7d = client.get(f"/measurements?range=7d&as_of={recent.isoformat()}")
        assert "190" in r_lifetime.text
        assert "190" not in r_7d.text  # outside the 7-day window from the pinned "as_of" date
    finally:
        _clear_measurements(me)


def test_charts_reject_unknown_range_falls_back_to_default(client, db, me):
    try:
        r = client.get("/measurements?range=bogus")
        assert r.status_code == 200  # never a 500 on a garbage query param
    finally:
        _clear_measurements(me)


def test_overview_chart_lists_every_trackable_metric_and_heart_rate(client, db, me):
    try:
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            s.add(BodyMeasurement(owner_id=tester.id, measured_at=date(2026, 9, 28), weight_lbs=180,
                                  heart_rate_bpm=62, systolic=120, diastolic=80))
            s.commit()
        t = client.get("/measurements").text
        assert '<select id="overview-metric-select">' in t
        assert '<option value="weight_lbs">Weight (lbs)</option>' in t
        assert '<option value="heart_rate_bpm">Heart Rate (bpm)</option>' in t
        assert '<option value="bp">Blood pressure</option>' in t
        # Only the first metric's panel starts visible; every other panel is hidden until chosen.
        assert 'class="overview-chart-panel" data-metric="weight_lbs">' in t
        assert 'class="overview-chart-panel" data-metric="heart_rate_bpm" hidden' in t
    finally:
        _clear_measurements(me)


def test_overview_and_body_silhouette_sit_in_the_same_top_row(client, db, me):
    try:
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            s.add(BodyMeasurement(owner_id=tester.id, measured_at=date(2026, 9, 28), weight_lbs=180))
            s.commit()
        t = client.get("/measurements").text
        row = t[t.index('class="overview-row"'):t.index('id="charts-heading"')]
        assert "overview-chart-section" in row and "overview-body-section" in row
    finally:
        _clear_measurements(me)


def test_measurements_page_does_not_crash_when_waist_equals_neck(client, db, me):
    """Review Focus item 1 (CRITICAL): body_fat_pct's log10 argument is <= 0 whenever
    waist <= neck, which used to raise ValueError inside _charts_context and 500 every tab."""
    try:
        client.post("/settings/body-profile", data={
            "sex": "Male", "birth_date": "1990-01-01", "height_in": "70", "activity_level": "1.55",
        })
        r = client.post("/measurements", data={
            "measured_at": "2026-09-28", "neck_in": "15", "waist_in": "15",
        }, follow_redirects=False)
        assert r.status_code == 303

        for tab in ("measurements", "macros", "journal", "labs"):
            assert client.get(f"/measurements?tab={tab}").status_code == 200
    finally:
        _clear_measurements(me)
        _clear_body_profile_for(me)


def _clear_body_profile_for(uid: int) -> None:
    with SessionLocal() as s:
        u = s.get(User, uid)
        u.sex = u.birth_date = u.height_in = u.activity_level = None
        s.commit()


def test_body_profile_height_zero_rejected_so_bmi_never_crashes_the_page(client, db, me):
    """Part of Review Focus item 1: height_in=0 must be rejected at the settings endpoint,
    not silently stored (which would divide by zero for every subsequent BMI computation)."""
    try:
        r = client.post("/settings/body-profile", data={"height_in": "0"})
        assert r.status_code == 422
        with SessionLocal() as s:
            assert s.get(User, me).height_in is None
    finally:
        _clear_body_profile_for(me)


def test_charts_do_not_mix_a_sharing_partners_data_points_into_own_chart(client, db, me):
    """Review Focus item 2: a sharing partner's weight/measurement entries must never be plotted
    into the viewer's own chart series, even though they legitimately appear in the separate
    'Shared with you' table."""
    other = _logged_in_client("ChartSharePartner")
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "chartsharepartner"))
    try:
        client.post("/measurements", data={"measured_at": "2026-09-20", "weight_lbs": "180"})
        other.post("/measurements", data={"measured_at": "2026-09-21", "weight_lbs": "999.9"})
        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
            s.commit()

        r = client.get("/measurements?range=lifetime")
        assert r.status_code == 200
        # The partner's entry is visible in the "Shared with you" table (page text), but must not
        # be one of the viewer's own weight-chart data points.
        assert "999.9" in r.text

        # Directly inspect the rendered chart's underlying points via the app's own context
        # builder, to check the actual chart data structure rather than raw page text (which
        # could contain "999.9" from the shared table alone).
        from app.routers.measurements import _charts_context
        with SessionLocal() as s:
            tester = s.scalar(select(User).where(User.username_key == "tester"))
            own = s.scalars(select(BodyMeasurement).where(BodyMeasurement.owner_id == tester.id)
                            .order_by(BodyMeasurement.measured_at.desc())).all()
            charts = _charts_context(own, tester, "lifetime")
        weight_series = next(s for s in charts["series"] if s["key"] == "weight_lbs")
        # Only the viewer's own 180 lb entry should ever have been scaled into the chart -- with a
        # single point, min_v == max_v == 180.
        assert weight_series["chart"]["min_v"] == pytest.approx(180.0)
        assert weight_series["chart"]["max_v"] == pytest.approx(180.0)
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.PERSONAL_DATA).delete()
            s.commit()
        _clear_measurements(me, other_id)


def test_silhouette_falls_back_to_earlier_non_null_value_after_partial_followup_entry(client, db):
    """Review Focus item 3(a): a weight-only follow-up entry after a full tape-measure session
    must not blank the silhouette's tape-measure values."""
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-20", "waist_in": "34", "neck_in": "15"})
        client.post("/measurements", data={"measured_at": "2026-09-27", "weight_lbs": "180"})
        t = html.unescape(client.get("/measurements").text)
        assert "34.0" in t or "34" in t
        assert "as of" in t.lower()  # flags that the 34 in shown here is from an older entry
    finally:
        _clear_measurements(tester)


def test_water_goal_uses_earlier_weight_after_weight_only_then_tape_measure_only_entries(client, db):
    """Review Focus item 3(b): a tape-measure-only follow-up entry (no weight) must not make the
    water goal / Macros tab forget an earlier logged weight."""
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-20", "weight_lbs": "200"})
        client.post("/measurements", data={"measured_at": "2026-09-27", "waist_in": "34"})
        t = client.get("/measurements").text
        assert "100" in t  # 200/2 default water goal, still computed from the earlier weight

        client.post("/settings/body-profile", data={
            "sex": "Male", "birth_date": "1990-01-01", "height_in": "70", "activity_level": "1.55",
        })
        macros_t = client.get("/measurements?tab=macros").text
        assert "log a weight entry" not in macros_t.lower()
    finally:
        _clear_measurements(tester)
        _clear_body_profile_for(tester)


def test_body_fat_shows_explicit_message_when_not_enough_data(client, db):
    """Review Focus item 5(c): when body_fat_pct() can't compute a value (waist <= neck here),
    the page must show an explicit 'not enough data' style message, not just omit it silently."""
    tester = _tester_id()
    try:
        client.post("/settings/body-profile", data={
            "sex": "Male", "birth_date": "1990-01-01", "height_in": "70", "activity_level": "1.55",
        })
        client.post("/measurements", data={"measured_at": "2026-09-28", "neck_in": "15", "waist_in": "15"})
        t = client.get("/measurements?range=lifetime").text.lower()
        assert "not enough data" in t
    finally:
        _clear_measurements(tester)
        _clear_body_profile_for(tester)


def test_weight_field_has_info_tooltip(client, db, me):
    try:
        t = client.get("/measurements").text
        assert "Best to weigh within 1 hour of waking, at least once a week, no clothing." in t
    finally:
        _clear_measurements(me)


def test_water_pace_shows_both_cups_and_bottles_per_hour(client, db):
    tester = _tester_id()
    try:
        client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "200"})
        t = client.get("/measurements").text.lower()
        assert "cups per hour" in t and "bottles per hour" in t
    finally:
        _clear_measurements(tester)

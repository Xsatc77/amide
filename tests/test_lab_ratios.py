"""Lab ratios worked out from results already entered: HOMA-IR, triglyceride/HDL, non-HDL cholesterol, LDL/HDL."""

from datetime import date, timedelta

import pytest

from app.models import LabMarker, LabPanel, LabResult

TODAY = date.today()


@pytest.fixture(autouse=True)
def clean(client, db, me):
    yield
    db.rollback()
    db.query(LabPanel).delete()
    db.commit()


def panel(db, me, days_ago, **results):
    p = LabPanel(owner_id=me, drawn_at=TODAY - timedelta(days=days_ago))
    for marker, spec in results.items():
        value, unit = spec if isinstance(spec, tuple) else (spec, None)
        p.results.append(LabResult(marker=LabMarker[marker], value=value, unit=unit))
    db.add(p)
    db.commit()


def test_each_ratio_is_charted_once_there_are_two_panels_with_its_inputs(client, db, me):
    panel(db, me, 60, FASTING_INSULIN=10.0, FASTING_GLUCOSE=90.0, TRIGLYCERIDES=150.0, HDL=50.0, LDL=100.0, TOTAL_CHOLESTEROL=200.0)
    page = client.get("/measurements?tab=labs&range=lifetime").text
    assert "HOMA-IR (Calculated)" not in page                               # one panel is not a trend
    panel(db, me, 0, FASTING_INSULIN=8.0, FASTING_GLUCOSE=85.0, TRIGLYCERIDES=120.0, HDL=55.0, LDL=90.0, TOTAL_CHOLESTEROL=190.0)
    page = client.get("/measurements?tab=labs&range=lifetime").text
    for label in ("HOMA-IR (Calculated)", "Triglycerides / HDL (Calculated)", "Non-HDL Cholesterol (Calculated)", "LDL / HDL (Calculated)"):
        assert f"<h3>{label}</h3>" in page


def test_a_ratio_needs_both_of_its_inputs_on_the_same_panel(client, db, me):
    panel(db, me, 60, TRIGLYCERIDES=150.0)
    panel(db, me, 0, HDL=55.0)
    page = client.get("/measurements?tab=labs&range=lifetime").text
    assert "Triglycerides / HDL (Calculated)" not in page


def test_glucose_in_mmol_uses_the_matching_homa_constant(client, db, me):
    from app.routers.labs import _labs_charts
    from sqlalchemy import select
    panel(db, me, 60, FASTING_INSULIN=10.0, FASTING_GLUCOSE=(5.0, "mmol/L"))
    panel(db, me, 0, FASTING_INSULIN=10.0, FASTING_GLUCOSE=(5.0, "mmol/L"))
    panels = db.scalars(select(LabPanel).order_by(LabPanel.drawn_at)).all()
    homa = next(c for c in _labs_charts(panels, None) if c["marker_label"] == "HOMA-IR (calculated)")
    assert [v for _, v in homa["chart"]["raw"]] == [pytest.approx(2.22, abs=0.01)] * 2          # 10 x 5 / 22.5

"""Every line chart draws reference lines with labels on its value axis."""

from app.routers.measurements import _chart, _dual_chart
from datetime import date


def test_a_line_chart_has_low_middle_and_high_reference_lines():
    chart = _chart([(date(2026, 1, 1), 180.0), (date(2026, 1, 8), 170.0)])
    ticks = chart["y_axis"]["ticks"]
    assert [t["value"] for t in ticks] == ["180", "175", "170"]
    assert ticks[0]["y"] < ticks[1]["y"] < ticks[2]["y"]


def test_a_flat_series_gets_one_line():
    assert len(_chart([(date(2026, 1, 1), 5.0), (date(2026, 1, 2), 5.0)])["y_axis"]["ticks"]) == 1


def test_points_stay_right_of_the_labels():
    chart = _chart([(date(2026, 1, 1), 1.0), (date(2026, 1, 8), 2.0)])
    assert min(x for x, _ in chart["points"]) >= chart["y_axis"]["left"]


def test_blood_pressure_chart_has_the_axis_too():
    chart = _dual_chart([(date(2026, 1, 1), 120.0), (date(2026, 1, 2), 125.0)], [(date(2026, 1, 1), 80.0), (date(2026, 1, 2), 82.0)])
    assert len(chart["y_axis"]["ticks"]) == 3


def test_the_pages_draw_the_lines(client, db, me):
    page = client.get("/fitness-test").text
    assert "measurement-chart" in page or "Log at least two" in page

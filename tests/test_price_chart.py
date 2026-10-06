from datetime import date

from app.library.price_lists.chart import multi_series_chart

BOX = dict(width=400, height=200, pad_l=40, pad_r=10, pad_t=10, pad_b=20)


def test_series_share_one_date_scale_and_one_value_scale():
    chart = multi_series_chart({"10mg": [(date(2026, 8, 1), 50.0), (date(2026, 9, 1), 60.0)],
                                "5mg": [(date(2026, 8, 1), 30.0)]}, **BOX)
    a, b = chart["series"]["10mg"]["points"], chart["series"]["5mg"]["points"]
    assert a[0]["x"] == b[0]["x"] == 40 and a[1]["x"] == 390      # first date at the left pad, last at the right
    assert a[1]["y"] == 10 and b[0]["y"] == 180                   # highest value at the top, lowest at the bottom
    assert 10 < a[0]["y"] < 180


def test_points_are_sorted_by_date_and_carry_their_values():
    chart = multi_series_chart({"10mg": [(date(2026, 9, 1), 60.0), (date(2026, 8, 1), 50.0)]}, **BOX)
    points = chart["series"]["10mg"]["points"]
    assert [(p["date"], p["value"]) for p in points] == [(date(2026, 8, 1), 50.0), (date(2026, 9, 1), 60.0)]
    assert chart["series"]["10mg"]["poly"] == f"{points[0]['x']},{points[0]['y']} {points[1]['x']},{points[1]['y']}"


def test_a_single_date_is_centered_and_a_single_value_sits_mid_height():
    chart = multi_series_chart({"10mg": [(date(2026, 8, 1), 50.0)]}, **BOX)
    (point,) = chart["series"]["10mg"]["points"]
    assert point["x"] == 40 + (400 - 40 - 10) / 2 and point["y"] == 10 + (200 - 10 - 20) / 2
    assert [t["value"] for t in chart["y_ticks"]] == [50.0]
    assert [t["date"] for t in chart["x_ticks"]] == [date(2026, 8, 1)]


def test_ticks_mark_the_value_extremes_and_the_date_extremes():
    chart = multi_series_chart({"10mg": [(date(2026, 8, 1), 30.0), (date(2026, 9, 1), 60.0)]}, **BOX)
    assert [t["value"] for t in chart["y_ticks"]] == [60.0, 45.0, 30.0]
    assert [t["date"] for t in chart["x_ticks"]] == [date(2026, 8, 1), date(2026, 9, 1)]
    assert (chart["min_d"], chart["max_d"]) == (date(2026, 8, 1), date(2026, 9, 1))


def test_nothing_to_plot_gives_none():
    assert multi_series_chart({}) is None
    assert multi_series_chart({"10mg": []}) is None

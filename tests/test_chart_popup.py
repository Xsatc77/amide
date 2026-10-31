"""Every chart shows a date-and-value popup on hover or tap: the shared script is on every page, and plotted points carry what it shows."""

from app.db import SessionLocal  # noqa: F401  (client fixture needs the app up)


def test_the_shared_chart_script_is_on_every_page(client, me):
    for path in ("/dashboard", "/measurements", "/protocols"):
        assert 'src="/static/js/charts.js"' in client.get(path).text


def test_the_script_serves_and_handles_hover_and_tap(client, me):
    js = client.get("/static/js/charts.js").text
    assert "pointerover" in js and "click" in js and "dataset.date" in js


def test_chart_numbers_round_to_two_decimals_and_drop_trailing_zeros():
    from app.templating import num
    assert num(3.14159) == "3.14" and num(3.1) == "3.1" and num(4.0) == "4" and num(4) == "4" and num(0.005) == "0.01" and num(None) == ""
    assert num(182.30000000000001) == "182.3" and num(1 / 3) == "0.33"

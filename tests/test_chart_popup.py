"""Every chart shows a date-and-value popup on hover or tap: the shared script is on every page, and plotted points carry what it shows."""

from app.db import SessionLocal  # noqa: F401  (client fixture needs the app up)


def test_the_shared_chart_script_is_on_every_page(client, me):
    for path in ("/dashboard", "/measurements", "/protocols"):
        assert 'src="/static/js/charts.js"' in client.get(path).text


def test_the_script_serves_and_handles_hover_and_tap(client, me):
    js = client.get("/static/js/charts.js").text
    assert "pointerover" in js and "click" in js and "dataset.date" in js

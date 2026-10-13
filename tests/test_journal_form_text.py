"""The journal popup explains the 1-5 scales and its inputs fill the popup."""

from pathlib import Path

CSS = Path(__file__).parent.parent / "app" / "static" / "css" / "app.css"


def test_the_one_to_five_scales_are_explained(client):
    page = client.get("/measurements?tab=journal").text
    assert "1 = bad" in page and "5 = great" in page
    assert "Mood (1 bad" in page and "Energy (1 low" in page and "Sleep quality (1 poor" in page


def test_popup_fields_are_full_width():
    assert ".dialog .settings-form > .field" in CSS.read_text(encoding="utf-8")

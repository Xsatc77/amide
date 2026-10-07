"""Two small layout fixes: the caution stripe keeps its rounded corners, and the Workouts import form is laid out by CSS classes."""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_the_caution_stripe_is_a_clipped_element_not_a_border_image():
    block = re.search(r"\.lib-section-caution \{[^}]*\}", CSS).group(0)
    assert "border-image:" not in block and "overflow: hidden" in block and "border-radius" in block
    stripe = re.search(r"\.lib-section-caution::before \{[^}]*\}", CSS).group(0)
    assert "repeating-linear-gradient" in stripe and "position: absolute" in stripe


def test_the_workouts_import_form_uses_classes_with_a_tight_gap(client, db):
    page = client.get("/workouts").text
    form = page.split('action="/workouts/upload"')[1].split("</form>")[0]
    assert "style=" not in form.split(">")[0] and "margin-left: 0.5rem" not in form
    assert ".workout-import" in CSS and 'class="workout-import"' in page

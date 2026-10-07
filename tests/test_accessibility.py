"""Basic accessibility every page shares: a way past the navigation, named landmarks, the current page marked, visible focus, reduced motion."""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_every_page_starts_with_a_skip_link_to_the_main_content(client, db):
    page = client.get("/dashboard").text
    assert page.index('class="skip-link"') < page.index('class="nav"')
    assert 'href="#main"' in page and re.search(r'<main id="main"[^>]*tabindex="-1"', page)


def test_the_navigation_is_a_named_landmark_and_marks_the_current_page(client, db):
    page = client.get("/inventory").text
    assert '<nav class="nav" aria-label="Main"' in page
    assert re.search(r'<a href="/inventory"[^>]*aria-current="page"', page)
    assert page.count('aria-current="page"') == 1


def test_links_buttons_and_summaries_show_a_focus_ring_and_motion_can_be_turned_off():
    assert re.search(r"a:focus-visible[^{]*\{[^}]*outline", CSS) and "summary:focus-visible" in CSS and "button:focus-visible" in CSS
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert ".skip-link" in CSS

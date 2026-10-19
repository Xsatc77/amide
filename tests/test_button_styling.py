"""Actions look like buttons: no text-only 'ghost' buttons, links that do something are buttons, button rows are one tidy flex row, popups span their width."""

import re
from pathlib import Path

CSS = Path("app/static/css/app.css").read_text(encoding="utf-8")
TEMPLATES = Path("app/templates")


def rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, selector
    return m.group(1)


def test_a_secondary_button_keeps_its_border_so_it_does_not_read_as_a_text_link():
    ghost = rule(".btn-ghost")
    assert "transparent" not in ghost
    assert "transparent" in rule(".btn-ghost.btn-icon")                  # only the close x stays bare


def test_a_summary_can_be_a_button_and_button_rows_are_flex_rows():
    assert "list-style: none" in rule("summary.btn")
    row = rule(".btn-row")
    assert "display: flex" in row and "flex-wrap: wrap" in row and "gap" in row


def test_a_popups_title_bar_and_button_row_span_the_popup():
    assert ".dialog .settings-form > .dialog-head" in CSS and "align-self: stretch" in CSS


# action links that used to be plain text, by file and the text they carry
CONVERTED = {
    "inventory/detail.html": ["&larr; Inventory", ">View</a>"], "inventory/group.html": ["Track orders"], "library/detail.html": ["Open in calculator", "See all your notes", "← Library"],
    "library/edit.html": ["←"], "protocols/builder.html": ["← Protocols"], "vendors/detail.html": ["&larr; Vendors", "Price list link", "Price list file"],
    "measurements/index.html": [">Edit</a>", "View attached report"], "settings/ingest.html": ["Open the vendor"], "backup/preview.html": ["Open a different file"],
    "auth/twofa_setup.html": ["Set up later"], "measurements/_food.html": ["Go to Settings"], "dashboard/index.html": ["Add photos"],
    "settings/settings.html": ["Price correction", "Price list inbox"], "library/list.html": ["My notes", "Goal stacks"], "workouts/edit.html": ["Find a demo"], "workouts/log.html": ["Find a demo"],
}


def test_action_links_are_buttons():
    for name, texts in CONVERTED.items():
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        for needle in texts:
            for m in re.finditer(r"<a\s([^>]*)>[^<]*" + re.escape(needle.strip(">")) if needle.startswith(">") else r"<a\s([^>]*)>[^<]*" + re.escape(needle), text):
                assert "btn" in m.group(1), f"{name}: the '{needle}' link is not a button"


def test_the_journal_tab_buttons_share_one_row_and_the_photo_form_is_a_popup(client):
    page = client.get("/measurements?tab=journal").text
    row = page.split('class="btn-row"', 1)[1].split("</div>", 1)[0]
    for label in ("New Entry", "Add a photo", "Log Workout", "Quick activity", "Add body photo"):
        assert label in row, label
    assert 'id="journal-photo-dialog"' in page and "<summary" not in row

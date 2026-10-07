"""Every visible form control on the main pages has a name a screen reader can announce (a label, aria-label or title), and every image has alt text."""

from html.parser import HTMLParser

import pytest

PAGES = ["/dashboard", "/inventory", "/inventory/orders", "/inventory/spending", "/protocols", "/protocols/new", "/vendors", "/library", "/library/learning", "/library/stacks",
         "/calculator", "/today", "/calendar", "/calendar?view=week", "/calendar?view=day", "/measurements", "/measurements?tab=journal", "/measurements?tab=labs",
         "/measurements?tab=food", "/workouts", "/workouts/energy", "/workouts/progress", "/fitness-test", "/settings"]
CONTROLS = {"input", "select", "textarea"}
NO_LABEL_NEEDED_TYPES = {"hidden", "submit", "button", "reset", "image"}


class Audit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.problems, self.label_depth, self.label_for, self.controls, self.ids_labelled = [], 0, set(), [], set()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        if tag == "img" and "alt" not in a:
            self.problems.append(f"<img src={a.get('src')}> has no alt")
        if tag in CONTROLS:
            if a.get("type", "").lower() in NO_LABEL_NEEDED_TYPES or "hidden" in a:
                return
            named = self.label_depth > 0 or a.get("aria-label") or a.get("aria-labelledby") or a.get("title")
            self.controls.append((tag, a.get("name") or a.get("id") or a.get("type"), a.get("id"), bool(named)))

    def handle_endtag(self, tag):
        if tag == "label" and self.label_depth:
            self.label_depth -= 1

    def finish(self):
        for tag, name, ident, named in self.controls:
            if not named and not (ident and ident in self.label_for):
                self.problems.append(f"<{tag} name={name}> has no label")
        return self.problems


@pytest.mark.parametrize("path", PAGES)
def test_the_page_names_every_control(client, db, path):
    response = client.get(path)
    assert response.status_code == 200, path
    audit = Audit()
    audit.feed(response.text)
    assert audit.finish() == [], path

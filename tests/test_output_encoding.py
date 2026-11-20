"""Output encoding: text a person typed (or a restored backup carried) must stay text in every page, file and header Amide makes.

Each page test stores a hostile string, fetches the page and parses it: a real tag, an event attribute, a script address or an unquoted
break-out is a failure. The scanner is checked against a page that does break out, so it cannot pass by being blind."""

import json
from html.parser import HTMLParser

import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from app import spreadsheet
from app.ai_summary import to_html
from app.calendar.ical import escape as ical_escape
from app.db import SessionLocal
from app.main import app
from app.models import InventoryItem, JournalEntry, PeptideNote, Protocol, SavedLink, SecurityEvent, User, Vendor
from app.templating import safe_href

MARK = "ENCMARK"
BREAK = MARK + '"><svg/onload=alert(1)>\'-alert(1)-\'</script><script>alert(1)</script>{{7*7}}${7*7}javascript:alert(1)'
URL_ATTRS = ("href", "src", "action", "formaction")


class Scan(HTMLParser):
    """Collects what a break-out would create: event attributes, script addresses, inline scripts, the tags the payload opens."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems, self._script = [], None

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name.startswith("on") or any(c in name for c in "\"'<>/"):
                self.problems.append(f"attribute {name!r} on <{tag}>")
            if name in URL_ATTRS and (value or "").strip().lower().startswith(("javascript:", "data:", "vbscript:")):
                self.problems.append(f"{name}={value!r} on <{tag}>")
        if tag == "script":
            self._script = dict(attrs).get("type"), dict(attrs).get("src")
            if self._script == (None, None):
                self.problems.append("inline script")
        if tag == "svg" and any(n == "onload" for n, _ in attrs):
            self.problems.append("svg with onload")

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = None

    def handle_data(self, data):
        if self._script and self._script[0] == "application/json":
            try:
                json.loads(data)
            except ValueError:
                self.problems.append("a JSON block that does not parse")
            if "<" in data:
                self.problems.append("a raw < inside a JSON block")
        elif self._script and "alert(1)" in data:
            self.problems.append("the payload inside a script")


def problems(text: str) -> list[str]:
    scan = Scan()
    scan.feed(text)
    return scan.problems


def assert_text_only(text: str):
    assert problems(text) == []
    assert "<svg/onload" not in text and MARK + '"><svg' not in text and "</script><script>" not in text
    assert MARK + "49" not in text                       # {{7*7}} was not computed


def test_the_scanner_catches_a_page_that_breaks_out():
    bad = f'<input value="{BREAK}"><a href="javascript:x">a</a><img src=x onerror=1><script type="application/json">{{"a": "</script>"}}</script>'
    found = problems(bad)
    assert any("onload" in p for p in found) and any("javascript:" in p for p in found) and any("onerror" in p for p in found)
    assert any("inline script" in p for p in found)


@pytest.fixture
def second(client):
    c = TestClient(app, follow_redirects=False)
    c.post("/notice", data={"understand": "1"})
    c.post("/register", data={"username": "EncodingOther", "password": "Other1!", "confirm": "Other1!"})
    return c


def test_stored_text_stays_text_on_every_page_that_shows_it(client, db, me):
    start = {"start_date": "2026-09-01", "goal": ["fat-loss"], "items-0-dose": "250", "items-0-dose_unit": "mcg", "items-0-frequency": "daily",
             "items-0-time_of_day": "am", "items-0-route": "subq"}
    assert client.post("/inventory", data={"name": BREAK, "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10", "quantity": "1",
                                          "order_date": "2026-08-01", "vendor": BREAK, "cost": "1", "notes": BREAK}).status_code in (200, 303)
    item = db.scalar(select(InventoryItem).where(InventoryItem.owner_id == me, InventoryItem.name == BREAK))
    assert item is not None
    r = client.post("/protocols", data={**start, "name": BREAK, "notes": BREAK, "items-0-new_name": "Example Peptide", "items-0-notes": BREAK}, follow_redirects=False)
    protocol = db.scalar(select(Protocol).where(Protocol.owner_id == me, Protocol.name == BREAK))
    assert protocol is not None, r.status_code
    client.post("/journal/entries", data={"entry_date": "2026-11-19", "mood": "3", "side_effects_other": BREAK, "notes": BREAK})
    try:
        for path in ("/inventory", f"/inventory/{item.id}", "/protocols", f"/protocols/{protocol.id}/edit", f"/protocols/{protocol.id}/print",
                     "/measurements?tab=journal", "/measurements?tab=journal&edit=2026-11-19", "/dashboard", "/calendar", "/vendors"):
            page = client.get(path)
            assert page.status_code == 200, path
            assert_text_only(page.text)
    finally:
        db.query(JournalEntry).filter(JournalEntry.owner_id == me, JournalEntry.entry_date == __import__("datetime").date(2026, 11, 19)).delete()
        db.commit()


def test_a_value_echoed_in_the_query_string_or_an_error_page_stays_text(client):
    quoted = "%22%3E%3Csvg/onload%3Dalert(1)%3E"
    for path in (f"/protocols?import_error={quoted}", f"/backup?imported={quoted}", f"/settings?error={quoted}", f"/vendors?sort={quoted}",
                 f"/library?q={quoted}", f"/links?q={quoted}&edit={quoted}", f"/calendar?view={quoted}&date={quoted}", f"/login?next={quoted}"):
        page = client.get(path, follow_redirects=True)
        assert problems(page.text) == [], path
        assert "<svg/onload" not in page.text, path
    bad = client.post("/links", data={"name": BREAK, "url": BREAK, "link_type": "Other"})
    assert bad.status_code == 422 and problems(bad.text) == [] and "<svg/onload" not in bad.text


def test_a_failed_sign_in_name_is_shown_in_the_security_log_as_text(client, db):
    stranger = TestClient(app, follow_redirects=False)
    stranger.post("/notice", data={"understand": "1"})
    stranger.post("/login", data={"username": BREAK, "password": "Wrong1!"})
    assert db.scalar(select(SecurityEvent.id).where(SecurityEvent.event == "login_failed", SecurityEvent.username.contains("<svg"))) is not None
    page = client.get("/settings")
    assert problems(page.text) == [] and "<svg/onload" not in page.text


def test_an_ai_answer_with_html_in_it_is_escaped_before_it_is_shown():
    out = str(to_html(f"## {BREAK}\n- {BREAK}\n**{BREAK}** and <img src=x onerror=alert(1)>"))
    assert problems(out) == [] and "<svg" not in out and "<img" not in out and "<script" not in out
    assert "&lt;svg/onload" in out and "<strong>" in out


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "  JavaScript:alert(1)", "data:text/html,x", "vbscript:x", "//example.test", "example.test", "", None, "https://a\n.test"])
def test_a_stored_address_that_is_not_web_or_mail_never_becomes_a_link(bad):
    assert safe_href(bad) == ""


def test_a_stored_address_that_is_web_or_mail_stays_a_link():
    for good in ("https://example.test/a?b=1&c=2", "http://example.test", "mailto:a@example.test", "tel:+15550100"):
        assert safe_href(good) == good


def test_a_script_address_that_reached_the_database_is_not_linked_on_the_pages(client, db, me, second):
    """A restored backup writes rows as they are, so the page must refuse a bad address itself, not rely on the form that normally checks it."""
    vendor = Vendor(name="Example Vendor", created_by_id=me, website="javascript:alert(1)", price_list_url="data:text/html,x")
    db.add(vendor)
    db.add(SavedLink(owner_id=me, name="Example Link", url="javascript:alert(1)", link_type="Other"))
    db.add(PeptideNote(owner_id=me, peptide_id=None, title="Example Note", url="javascript:alert(1)", body="x"))
    db.commit()
    for path in (f"/vendors/{vendor.id}", "/links", "/library/learning"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert problems(page.text) == [], path


def test_spreadsheet_cells_that_look_like_formulas_become_plain_text():
    for text in ("=HYPERLINK(\"http://example.test\",\"x\")", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"):
        assert spreadsheet.cell(text) == "'" + text
    assert spreadsheet.cell("Example") == "Example" and spreadsheet.cell(-5) == -5 and spreadsheet.cell(None) is None
    assert spreadsheet.row(["=x", 1]) == ["'=x", 1]


def test_the_inventory_csv_does_not_carry_a_live_formula(client, db, me):
    client.post("/inventory", data={"name": "=HYPERLINK(\"http://example.test\",\"x\")", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
                                    "quantity": "1", "order_date": "2026-08-01", "cost": "1"})
    body = client.get("/backup/export/inventory.csv").text
    assert "\"'=HYPERLINK" in body and "\n\"=HYPERLINK" not in body and not any(line.startswith("=") for line in body.splitlines())


def test_a_lone_carriage_return_cannot_start_a_new_calendar_line():
    assert "\r" not in ical_escape("Example\rEND:VEVENT\rBEGIN:VEVENT") and "\n" not in ical_escape("a\nb")
    assert ical_escape("a\rb") == "a\\nb" and ical_escape("a\r\nb") == "a\\nb"


def test_the_label_file_name_is_plain_ascii_whatever_the_peptide_is_called(client, db, me):
    """A symbol is built from the name's letters, so a name in another alphabet used to make a header that could not be encoded (a 500)."""
    client.post("/inventory", data={"name": "\u0416\u0438\u0434\u043a\u0438\u0439", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
                                    "quantity": "1", "order_date": "2026-08-01", "cost": "1"})
    item = db.scalar(select(InventoryItem).where(InventoryItem.owner_id == me))
    response = client.get(f"/inventory/{item.id}/labels.docx")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert disposition.isascii() and disposition.startswith('attachment; filename="Amide_Labels_') and disposition.count('"') == 2


def test_the_portfolio_pdf_treats_text_that_looks_like_markup_as_text(client, db, me):
    hostile = '<b>x</b><font size="999">y</font><img src="file:///etc/passwd"/><a href="javascript:alert(1)">z</a> & <unclosed'
    client.post("/protocols", data={"name": hostile, "notes": hostile, "start_date": "2026-09-01", "goal": ["fat-loss"], "items-0-new_name": "Example Peptide",
                                    "items-0-dose": "250", "items-0-dose_unit": "mcg", "items-0-frequency": "daily", "items-0-time_of_day": "am",
                                    "items-0-notes": hostile})
    protocol = db.scalar(select(Protocol).where(Protocol.owner_id == me, Protocol.name == hostile))
    assert protocol is not None
    response = client.get(f"/protocols/{protocol.id}/portfolio.pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert response.headers["content-disposition"].isascii()

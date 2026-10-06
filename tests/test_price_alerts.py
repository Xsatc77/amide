import html
from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from price_helpers import item, make_card, make_list, make_vendor


def dashboard(client) -> str:
    return html.unescape(client.get("/dashboard").text)


def second_user(username="alertother") -> TestClient:
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": username, "password": "Other1!", "confirm": "Other1!"})
    return other


def test_dashboard_alerts_name_the_product_and_its_vendor_or_vendors(client, db):
    acme, zephyr = make_vendor(db, "Acme"), make_vendor(db, "Zephyr")
    make_list(db, acme, date(2026, 9, 1), item("Quillamine", 5, 20), item("Marnitol", 5, 9))
    make_list(db, zephyr, date(2026, 9, 2), item("Quillamine", 5, 22))
    page = dashboard(client)
    assert "NEW PEPTIDE ALERT" in page
    assert "Quillamine</a> — Acme, Zephyr" in page and "Marnitol</a> — Acme" in page


def test_no_alert_when_every_product_has_a_card(client, db):
    make_card(db, "Quillamine")
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Quillamine", 5, 20))
    assert "NEW PEPTIDE ALERT" not in dashboard(client)


def test_a_backlog_shows_five_and_tucks_the_rest_away(client, db):
    names = [f"Zorvexa{chr(97 + i)}" for i in range(7)]
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), *[item(n, 5, 10 + i) for i, n in enumerate(names)])
    page = dashboard(client)
    assert page.count("NEW PEPTIDE ALERT") == 7
    assert "Show 2 more" in page


def test_the_administrator_can_ignore_a_product(client, db):
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Quillamine", 5, 20), item("Marnitol", 5, 9))
    page = dashboard(client)
    assert 'action="/price-alerts/ignore"' in page
    r = client.post("/price-alerts/ignore", data={"name": "Quillamine"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard"
    after = dashboard(client)
    assert "Quillamine</a>" not in after and "Marnitol</a>" in after


def test_another_user_sees_the_alert_but_cannot_ignore_it(client, db):
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Quillamine", 5, 20))
    other = second_user()
    page = html.unescape(other.get("/dashboard").text)
    assert "NEW PEPTIDE ALERT" in page and "/price-alerts/ignore" not in page
    assert other.post("/price-alerts/ignore", data={"name": "Quillamine"}).status_code == 404
    assert "Quillamine</a>" in dashboard(client)


def test_a_new_alias_clears_the_alert(client, db):
    make_list(db, make_vendor(db, "Acme"), date(2026, 9, 1), item("Marnitol", 5, 9))
    assert "Marnitol</a>" in dashboard(client)
    make_card(db, "Mannitol Peptide", aliases="Marnitol")
    assert "Marnitol</a>" not in dashboard(client)

"""Session lifecycle through cookies only: a new token at sign-in (no fixation), the old token is dead after logout."""

from fastapi.testclient import TestClient

from app.main import app
from tests.test_auth_flow import PW, accept, register


def cookie(c: TestClient) -> str | None:
    return c.cookies.get("amide_session")


def test_signing_in_hands_out_a_new_session_token(client):
    browser = TestClient(app, follow_redirects=False)
    accept(browser)
    before = cookie(browser)
    assert before
    r = register(browser, "Lifecycle1")
    assert r.status_code == 303
    after = cookie(browser)
    assert after and after != before
    # the token an attacker could have planted before sign-in is worth nothing afterwards
    planted = TestClient(app, follow_redirects=False)
    planted.cookies.set("amide_session", before)
    assert planted.get("/dashboard").status_code in (303, 401)
    assert browser.get("/dashboard").status_code == 200


def test_a_second_sign_in_also_changes_the_token(client):
    browser = TestClient(app, follow_redirects=False)
    accept(browser)
    register(browser, "Lifecycle2")
    first = cookie(browser)
    browser.post("/logout")
    accept(browser)
    assert browser.post("/login", data={"username": "Lifecycle2", "password": PW}).status_code == 303
    assert cookie(browser) not in (None, first)


def test_the_old_token_stops_working_after_logout(client):
    browser = TestClient(app, follow_redirects=False)
    accept(browser)
    register(browser, "Lifecycle3")
    old = cookie(browser)
    assert browser.get("/dashboard").status_code == 200
    browser.post("/logout")
    replay = TestClient(app, follow_redirects=False)
    replay.cookies.set("amide_session", old)
    r = replay.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"] in ("/notice", "/welcome")
    assert replay.get("/protocols").status_code == 303 and replay.post("/vendors/1/favorite").status_code == 303      # signed out: every gated page still turns it away

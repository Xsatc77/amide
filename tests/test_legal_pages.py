import re

import pytest
from fastapi.testclient import TestClient

from app.main import app

LEGAL_PATHS = ("/legal/terms", "/legal/disclaimer", "/legal/privacy")
FOOTER = ('Amide — Copyright 2026 — XSATC — <a href="/legal/terms">Terms of Service</a> — '
          '<a href="/legal/disclaimer">Disclaimer</a> — <a href="/legal/privacy">Privacy Policy</a>')
EMAIL = "demigod_ruffle.00@icloud.com"


@pytest.fixture
def fresh():
    """A brand-new browser (no cookies), like test_auth_flow's."""
    return TestClient(app, follow_redirects=False)


def _accept(c):
    assert c.post("/notice", data={"understand": "1"}).status_code == 303


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_legal_pages_open_to_a_visitor_who_has_not_accepted_the_notice(fresh, path):
    assert fresh.get(path).status_code == 200


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_legal_pages_open_to_a_visitor_who_is_not_signed_in(fresh, path):
    _accept(fresh)
    assert fresh.get(path).status_code == 200


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_signed_out_legal_page_does_not_show_the_app_nav(fresh, path):
    assert 'class="topbar"' not in fresh.get(path).text


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_signed_in_legal_page_keeps_the_app_nav(client, path):
    assert 'class="topbar"' in client.get(path).text


@pytest.mark.parametrize("path", ["/dashboard", "/inventory", *LEGAL_PATHS])
def test_footer_on_signed_in_pages(client, path):
    assert FOOTER in client.get(path).text


@pytest.mark.parametrize("path", ["/notice", "/welcome", "/login", "/register", *LEGAL_PATHS])
def test_footer_on_signed_out_pages(fresh, path):
    _accept(fresh)
    assert FOOTER in fresh.get(path).text


def test_terms_links_its_references_to_the_disclaimer_and_privacy_policy(client):
    body = client.get("/legal/terms").text
    assert 'See our full <a href="/legal/disclaimer">Disclaimer</a>' in body
    assert len(re.findall(r'see our <a href="/legal/privacy">Privacy Policy</a>', body, re.I)) == 2


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_contact_email_is_a_mailto_link(client, path):
    assert f'<a href="mailto:{EMAIL}">{EMAIL}</a>' in client.get(path).text


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_no_stray_links_from_the_word_documents(client, path):
    body = client.get(path).text.lower()
    assert "sharepoint" not in body and "peptideschedule" not in body


def test_privacy_policy_has_no_text_the_owner_did_not_write(client):
    assert "Because Amide is self-hosted" not in client.get("/legal/privacy").text


@pytest.mark.parametrize("path", ["/dashboard", "/inventory", *LEGAL_PATHS])
def test_the_footer_says_research_and_informational_purposes_only(client, path):
    assert "For Research &amp; Informational Purposes Only" in client.get(path).text


def test_the_footer_notice_is_on_signed_out_pages_too(fresh):
    _accept(fresh)
    assert "For Research &amp; Informational Purposes Only" in fresh.get("/login").text

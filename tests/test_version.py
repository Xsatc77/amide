"""The running build is shown honestly in the footer and in Settings."""

import pytest

from app import version


def test_a_release_build_shows_its_number(monkeypatch):
    monkeypatch.setenv("AMIDE_VERSION", "1.1.27")
    assert version.label() == "v1.1.27"


def test_a_branch_build_or_a_checkout_says_what_it_is(monkeypatch):
    monkeypatch.setenv("AMIDE_VERSION", "main")
    assert version.label() == "main"
    monkeypatch.delenv("AMIDE_VERSION")
    assert version.label() == "development"


@pytest.mark.parametrize("path", ["/settings", "/legal/terms"])
def test_the_version_is_on_the_page(client, me, monkeypatch, path):
    monkeypatch.setenv("AMIDE_VERSION", "9.8.7")
    page = client.get(path).text
    assert "Amide v9.8.7" in page


def test_settings_also_shows_the_database_revision(client, me):
    page = client.get("/settings").text
    assert "database 0" in page

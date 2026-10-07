"""Premade protocols: the list on the Protocols page, and each one opens the builder prefilled."""

import re

import pytest

from app.goals import GOALS_BY_SLUG
from app.models import WEEKDAY_LETTERS
from app.protocols.premade import PREMADE


def test_every_premade_is_well_formed():
    assert len({p.slug for p in PREMADE}) == len(PREMADE)
    for p in PREMADE:
        assert p.items and all(g in GOALS_BY_SLUG for g in p.goals), p.slug
        for it in p.items:
            assert it.dose > 0 and set(it.weekdays) <= set(WEEKDAY_LETTERS), (p.slug, it.peptide)


def test_no_premade_text_names_a_person():
    text = " ".join(f"{p.name} {p.summary} {p.caution} " + " ".join(i.note for i in p.items) for p in PREMADE)
    assert not re.search(r"\b(Dr\.|Huge|Greenfield|Johnson|Huberman|Attia|Campbell|Pakulski|Tremblay|Holtorf|Koniver|Williams|Moore)\b", text)


def test_the_protocols_page_lists_them(client, db):
    page = client.get("/protocols").text
    assert "Premade Protocol" in page
    for p in PREMADE:
        assert f"/protocols/new?premade={p.slug}" in page


@pytest.mark.parametrize("slug", [p.slug for p in PREMADE])
def test_each_one_opens_the_builder(client, db, slug):
    r = client.get(f"/protocols/new?premade={slug}")
    assert r.status_code == 200
    assert [p.name for p in PREMADE if p.slug == slug][0].split(":")[0].replace("&", "&amp;") in r.text


def test_a_titration_premade_carries_its_steps(client, db):
    page = client.get("/protocols/new?premade=semaglutide-titration").text
    assert '"titration": true' in page.lower() or 'titration' in page
    assert "2.4" in page and "1.7" in page


def test_an_unknown_premade_falls_back_to_a_blank_builder(client, db):
    assert client.get("/protocols/new?premade=nope").status_code == 200

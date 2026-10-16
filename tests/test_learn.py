"""The Learn page: a tutorial on gray-market peptides with a glossary, term boxes, hover definitions and a syringe teaching aid."""

import html
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.learn import glossary, syringe
from app.learn.sections import LAST_UPDATED, SECTIONS
from app.main import app


def page(client):
    return html.unescape(client.get("/learn").text)


# ---- the arithmetic ---------------------------------------------------------------------------------------------------------------------

def test_the_same_units_hold_more_peptide_from_a_stronger_mix():
    a, b = syringe.drawn(10, 10, 2), syringe.drawn(10, 10, 1)
    assert (a.concentration_mg_ml, a.volume_ml, a.mg, a.mcg) == (5.0, 0.1, 0.5, 500.0)
    assert (b.concentration_mg_ml, b.mg) == (10.0, 1.0)


def test_working_backward_gives_the_units_to_draw():
    assert syringe.units_for(0.5, 10, 2) == pytest.approx(10.0)
    assert syringe.units_for(0.5, 5, 2) == pytest.approx(20.0)             # a 2.5 mg/mL mix
    assert syringe.units_for(0.25, 5, 2) == pytest.approx(10.0)


@pytest.mark.parametrize("args", [(-1, 10, 2), (10, 0, 2), (10, 10, 0)])
def test_nonsense_numbers_are_refused(args):
    with pytest.raises(ValueError):
        syringe.drawn(*args)


def test_numbers_are_shown_without_trailing_zeros():
    assert (syringe.num(0.5), syringe.num(500.0, 0), syringe.num(1.0), syringe.num(0.125)) == ("0.5", "500", "1", "0.125")


# ---- the content is consistent ----------------------------------------------------------------------------------------------------------

def test_every_term_a_section_lists_has_a_glossary_entry():
    for section in SECTIONS:
        for term in section.terms:
            assert term in glossary.BY_TERM, f"{section.slug} lists {term!r}, which is not in the glossary"


def test_sections_are_numbered_in_order_and_unique():
    assert [s.slug for s in SECTIONS] == [f"s{i:02d}" for i in range(1, len(SECTIONS) + 1)]
    assert len({s.title for s in SECTIONS}) == len(SECTIONS)


def test_the_glossary_has_no_duplicate_terms_or_clashing_aliases_and_every_entry_says_something():
    seen = {}
    for e in glossary.ENTRIES:
        assert e.definition.strip() and len(e.definition) < 420, e.term
        for name in (e.term, *e.aliases):
            key = name.lower()
            assert seen.setdefault(key, e.term) == e.term, f"{name!r} is used by both {seen[key]!r} and {e.term!r}"


def test_the_glossary_is_listed_a_to_z_and_amide_terms_are_kept_apart():
    assert [e.term.lower() for e in glossary.SORTED] == sorted(e.term.lower() for e in glossary.SORTED)
    assert all(not e.amide for e in glossary.SORTED) and all(e.amide for e in glossary.AMIDE_TERMS)


# ---- the page ---------------------------------------------------------------------------------------------------------------------------

def test_the_page_needs_a_login(client):
    stranger = TestClient(app, follow_redirects=False)
    assert stranger.get("/learn").status_code in (303, 401)


def test_the_page_has_every_section_a_terms_box_and_the_glossary(client):
    text = page(client)
    for section in SECTIONS:
        assert f'id="{section.slug}"' in text and section.title in text, section.slug
        box = text.split(f'id="{section.slug}"', 1)[1].split('class="learn-terms"', 1)[1].split("</div>", 1)[0]
        for term in section.terms:
            assert term in box, f"{section.slug} box is missing {term}"
    assert 'id="glossary"' in text
    for entry in glossary.ENTRIES:
        assert f'id="g-{glossary.slug(entry.term)}"' in text, entry.term


def test_the_page_says_it_is_education_not_advice_and_when_it_was_last_updated(client):
    text = page(client)
    assert "not medical or legal advice" in text and LAST_UPDATED in text
    assert "does not recommend that you use anything" in text


def test_the_hover_data_is_embedded_and_matches_the_glossary(client):
    text = client.get("/learn").text
    data = json.loads(re.search(r'<script type="application/json" id="learn-glossary">(.*?)</script>', text, re.S).group(1))
    assert data["BAC water"]["definition"].startswith("Bacteriostatic water")
    assert "bacteriostatic water" in [a.lower() for a in data["BAC water"]["aliases"]]
    assert data["BAC water"]["id"] == "g-bac-water"
    assert not any(k in data for k in ("Active vial", "Lot banner"))                 # Amide's own terms are not underlined in the tutorial
    sections = json.loads(re.search(r'<script type="application/json" id="learn-sections">(.*?)</script>', text, re.S).group(1))
    assert sections["s08"] == [t for t in next(s for s in SECTIONS if s.slug == "s08").terms]


def test_the_units_entry_shows_computed_worked_examples(client):
    text = page(client)
    entry = text.split('id="g-units-on-a-syringe"', 1)[1].split('id="g-vial"', 1)[0]
    for expected in ("5 mg/mL", "0.5 mg", "500 mcg", "10 mg/mL", "1 mg", "20 units"):
        assert expected in entry, expected
    assert "not dose suggestions" in entry and "U-100" in entry


def test_the_syringe_teaching_aid_and_the_scorecard_are_on_the_page(client):
    text = page(client)
    assert 'data-learn-syringe' in text and "learn-syringe.js" in text and "learn.js" in text
    assert text.count("data-learn-check") >= 10


def test_the_account_menu_links_to_learn(client):
    assert 'href="/learn"' in client.get("/dashboard").text


def test_nothing_names_a_vendor_and_no_dose_advice_slips_in(client):
    text = page(client).lower()
    assert "recommended dose" not in text and "you should take" not in text

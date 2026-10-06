# tests/test_price_list_filename.py
from datetime import date

import pytest

from app.library.price_lists.filename import FileInfo, parse_filename

FILENAME_SHAPES = [  # invented vendors; every shape the owner's real filenames take
    ("Acme Labs - Price List - 2026-08-17-01 .jpg", "Acme Labs", None, date(2026, 8, 17)),  # page suffix, space
    ("Borealis - China Price List - 2026-08-24.pdf", "Borealis", "china", date(2026, 8, 24)),
    ("Borealis - USA Price List - 2026-08-31.pdf", "Borealis", "us", date(2026, 8, 31)),
    ("Cobalt Bio Labs - Price List - 2026-08-31.pdf", "Cobalt Bio Labs", None, date(2026, 8, 31)),
    ("Delta - Price List - 2026-09-15.pdf", "Delta", None, date(2026, 9, 15)),
    ("Delta Peptides - China Price List - 2026-08-18.pdf", "Delta Peptides", "china", date(2026, 8, 18)),
    ("Evergreen - Pricelist - 2026-10-03.pdf", "Evergreen", None, date(2026, 10, 3)),  # "Pricelist", one word
    ("Evergreen - USA Price List - 2026-10-03-01.jpg", "Evergreen", "us", date(2026, 10, 3)),
    ("Evergreen - USA Price List - 2026-10-03-02.jpg.jpg", "Evergreen", "us", date(2026, 10, 3)),  # doubled ext
    ("Fjord Peptide - Price list - 2026-09-10.pdf", "Fjord Peptide", None, date(2026, 9, 10)),
    ("Garnet Biolabs - price list - 2026-09-19.pdf", "Garnet Biolabs", None, date(2026, 9, 19)),
    ("Harbor Peptide - Price List - 2026-09-14.xlsx", "Harbor Peptide", None, date(2026, 9, 14)),
    ("Ivory - Price List NEW - 2026-05-19.pdf", "Ivory", None, date(2026, 5, 19)),
]


@pytest.mark.parametrize("name, vendor, warehouse, when", FILENAME_SHAPES)
def test_every_filename_shape_parses(name, vendor, warehouse, when):
    assert parse_filename(name) == FileInfo(vendor, warehouse, when)


def test_warehouse_words_are_whole_word_and_case_insensitive():
    assert parse_filename("X - china price list - 2026-01-02.pdf").warehouse == "china"
    assert parse_filename("X - Chinese Warehouse Price List - 2026-01-02.pdf").warehouse == "china"
    assert parse_filename("X - United States Price List - 2026-01-02.pdf").warehouse == "us"
    assert parse_filename("X - Focus Price List - 2026-01-02.pdf").warehouse is None  # "us" inside "Focus"


@pytest.mark.parametrize("name", [
    "Price List.pdf",                       # no vendor segment, no date
    "Acme - Price List.pdf",                # no date
    " - Price List - 2026-01-01.pdf",       # empty vendor
    "Acme - Price List - 2026-13-45.pdf",   # not a real date
])
def test_unusable_filenames_are_rejected(name):
    with pytest.raises(ValueError):
        parse_filename(name)

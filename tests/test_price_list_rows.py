# tests/test_price_list_rows.py
import pytest

from app.library.price_lists.rows import (
    ParsedRow, Spec, check_code_size, check_pack_size, code_number, code_prefix, pack_type, parse_price, parse_spec,
)
from app.library.price_lists.specs import format_spec, merge_specs


@pytest.mark.parametrize("text, expected", [
    ("5mg*10vials", Spec(5, "mg", 10)),
    ("10mg x 10 vials", Spec(10, "mg", 10)),
    ("20mg *10vials", Spec(20, "mg", 10)),
    ("10mg *10", Spec(10, "mg", 10)),
    ("100IU*10vi", Spec(100, "IU", 10)),
    ("5000IUal*10via", Spec(5000, "IU", 10)),        # text overlap seen in a real PDF
    ("12iu*2", Spec(12, "IU", 2)),
    ("250mg/ml*1vials", Spec(250, "mg/ml", 1)),     # oil concentration, a one-vial box
    ("600mg/ml 10ml*10 vials", Spec(10, "ml", 10)),  # the sized spec wins over the concentration
    ("2.5mg*10vials", Spec(2.5, "mg", 10)),
    ("500mcg*10vials", Spec(500, "mcg", 10)),
    ("500ug*10vials", Spec(500, "mcg", 10)),
    ("100mg", Spec(100, "mg", None)),               # pack size not stated
    ("60mg *6 vials", Spec(60, "mg", 6)),            # fewer than 10 vials: a box, not a kit
    ("10ml*600mg/ml", Spec(10, "ml", None)),         # "*600mg/ml" is a concentration, not 600 vials
    ("10ml x 600mg/ml/via", Spec(10, "ml", None)),
])
def test_parse_spec(text, expected):
    assert parse_spec(text) == expected


@pytest.mark.parametrize("text", ["", "Retatrutide", "Specification"])
def test_parse_spec_rejects_non_specs(text):
    assert parse_spec(text) is None


@pytest.mark.parametrize("text, expected", [
    ("$45", (45.0, None)),
    ("40", (40.0, None)),
    ("$50.00", (50.0, None)),
    ("$ 65", (65.0, None)),
    ("$1,250", (1250.0, None)),
    ("$30/1vial", (30.0, 1)),
    ("", (None, None)),
    ("Instructions for Use (for reference only)", (None, None)),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("code, prefix, number", [
    ("RT10", "RT", 10.0),
    ("2AD", "AD", 2.0),
    ("HCG5000(GK5)", "HCG", 5000.0),
    ("G10K", "G", 10.0),
    ("BBG70/Glow 70", "BBG", 70.0),
    ("Botox", "BOTOX", None),
    ("*", None, None),
    ("", None, None),
    (None, None, None),
])
def test_code_prefix_and_number(code, prefix, number):
    assert code_prefix(code) == prefix
    assert code_number(code) == number


def row(code, amount, unit="mg"):
    return ParsedRow(code=code, name=None, spec=Spec(amount, unit, 10), pack_price=1.0)


def test_code_digits_matching_the_vial_size_are_not_flagged():
    r = row("RT10", 10)
    check_code_size(r)
    assert r.flags == []


def test_code_digits_differing_from_the_vial_size_are_flagged():
    r = row("RT10", 20)
    check_code_size(r)
    assert r.flags == ["code-size-mismatch"]


@pytest.mark.parametrize("size, expected", [
    (10, "kit"), (1, "box"), (2, "box"), (5, "box"), (6, "box"), (9, "box"),
    (None, None), (11, None), (20, None), (0, None),
])
def test_pack_type_a_kit_is_exactly_ten_vials_and_fewer_is_a_box(size, expected):
    assert pack_type(size) == expected


@pytest.mark.parametrize("size, flags", [(10, []), (5, []), (1, []), (None, []), (11, ["unusual-pack-size"]),
                                         (20, ["unusual-pack-size"])])
def test_unusual_pack_sizes_are_flagged(size, flags):
    r = ParsedRow(code="ZX10", name=None, spec=Spec(10, "mg", size), pack_price=1.0)
    check_pack_size(r)
    assert r.flags == flags


def test_no_code_or_a_liquid_unit_is_never_flagged():
    for r in (row(None, 10), row("AA10", 3, "ml"), row("Botox", 100, "IU")):
        check_code_size(r)
        assert r.flags == []


def test_merge_specs_unions_and_sorts_by_unit_then_amount():
    assert merge_specs("50mg", "5mg, 10mg", "5mg") == "5mg, 10mg, 50mg"
    assert merge_specs("12IU, 500mcg", "10mg") == "10mg, 500mcg, 12IU"
    assert merge_specs("", "") == ""


def test_format_spec_drops_trailing_zeros():
    assert format_spec(10.0, "mg") == "10mg"
    assert format_spec(2.5, "mg") == "2.5mg"
    assert format_spec(12.0, "IU") == "12IU"

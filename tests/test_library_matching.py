from types import SimpleNamespace as NS

import pytest

from app.library.matching import match_name, name_key, suggest


def card(id, name, aliases=None):
    return NS(id=id, name=name, aliases=aliases)


CARDS = [
    card(1, "BPC-157", "BPC 157, Body Protection Compound"),
    card(2, "Wolverine Stack (BPC-157 + TB-500)", "BPC157, BPC-157/TB-500"),
    card(3, "TB-500"),
    card(4, "Semaglutide"),
    card(5, "Hexarelin"),
    card(6, "CJC-1295 DAC"),
    card(7, "CJC-1295 (No DAC)"),
    card(8, "AICAR (Acadesine)"),
    card(9, "PT-141"),
    card(10, "PT-141 (Bremelanotide)"),
    card(11, "Elamipretide (SS-31)"),
    card(12, "SS-31 (Elamipretide)"),
    card(13, "HGH Fragment 176-191"),
    card(14, "CagriSema"),
]


def names(match):
    return sorted(c.name for c in match.cards) if match else None


# ---------------------------------------------------------------- name_key

@pytest.mark.parametrize("a, b", [
    ("BPC 157", "BPC-157"), ("bpc157", "BPC-157"), ("  B.P.C 157 ", "BPC-157"),
    ("Hexarelin Acetate", "Hexarelin"), ("Kiss Peptin-10", "Kisspeptin-10"),
    ("CJC1295 Without DAC", "CJC-1295 (No DAC)"), ("CJC 1295 (with DAC)", "CJC-1295 DAC"),
    ("Glow（TB10mg）", "Glow (TB10mg)"),  # fullwidth punctuation from a PDF
])
def test_name_key_ignores_spacing_punctuation_case_and_salt_words(a, b):
    assert name_key(a) == name_key(b)


def test_name_key_keeps_with_and_without_variants_apart():
    assert name_key("CJC-1295 DAC") != name_key("CJC-1295 (No DAC)")
    assert name_key("CJC 1295 (with DAC)") != name_key("CJC1295 Without DAC")
    assert name_key("Glutathione with B12") != name_key("Glutathione without B12")


def test_name_key_of_nothing_is_empty():
    assert name_key(None) == "" and name_key("  --  ") == ""


# ---------------------------------------------------------------- match_name tiers

def test_exact_name_wins_regardless_of_case():
    m = match_name("bpc-157", CARDS)
    assert m.how == "exact" and names(m) == ["BPC-157"]


def test_spacing_and_punctuation_variants_match_by_normalized_name():
    for vendor in ("BPC157", "BPC 157", "bpc-157"):
        m = match_name(vendor, CARDS)
        assert names(m) == ["BPC-157"], vendor


def test_normalized_tier_wins_over_an_alias_on_another_card():
    # "BPC157" is also an alias of the stack card; the card actually named that must win alone.
    assert names(match_name("BPC157", CARDS)) == ["BPC-157"]


def test_salt_suffix_is_ignored():
    assert names(match_name("Hexarelin Acetate", CARDS)) == ["Hexarelin"]


def test_dac_variants_map_to_the_right_card():
    assert names(match_name("CJC 1295 (with DAC)", CARDS)) == ["CJC-1295 DAC"]
    assert names(match_name("CJC1295 Without DAC", CARDS)) == ["CJC-1295 (No DAC)"]


def test_vendor_name_without_its_parenthetical_matches_the_card():
    assert names(match_name("Aicar", CARDS)) == ["AICAR (Acadesine)"]
    assert names(match_name("Cagri sema (5mg+5mg)", CARDS)) == ["CagriSema"]
    assert match_name("Aicar", CARDS).how == "related"


def test_every_card_in_the_winning_tier_is_returned():
    m = match_name("SS-31", CARDS)
    assert m.how == "related" and names(m) == ["Elamipretide (SS-31)", "SS-31 (Elamipretide)"]


def test_alias_match_is_how_a_confirmed_vendor_spelling_is_remembered():
    cards = [card(1, "FOXO4-DRI", "Fox04 -DRI, FOX04-DRI")]
    assert names(match_name("Fox04 -DRI", cards)) == ["FOXO4-DRI"]


def test_a_generic_card_catches_its_variants_through_its_aliases():
    # "TRT" is the generic card for any testosterone ester; listing the esters as aliases routes them there.
    trt = card(1, "TRT (Testosterone Replacement Therapy)", "Testosterone Cypionate, Testosterone Decanoate")
    cards = [trt, card(2, "Primobolan")]
    assert names(match_name("Testosterone Cypionate", cards)) == [trt.name]
    assert names(match_name("testosterone  decanoate", cards)) == [trt.name]
    assert match_name("Testosterone Undecanoate", cards) is None  # not listed -> reported, never guessed


def test_a_typo_matches_when_unambiguous():
    m = match_name("Samaglutide", CARDS)
    assert m.how == "fuzzy" and names(m) == ["Semaglutide"]


def test_fuzzy_never_matches_across_different_numbers():
    assert match_name("HGH Fragment 176-192", CARDS) is None
    assert match_name("PT-142", CARDS) is None


def test_fuzzy_refuses_when_two_cards_are_equally_close():
    cards = [card(1, "Semaglutide"), card(2, "Semaglutade")]
    assert match_name("Semaglutyde", cards) is None


def test_fuzzy_accepts_only_a_single_misspelled_word():
    cards = [card(1, "Thymosin Alpha Peptide")]
    assert match_name("Thymosyn Alpha Peptide", cards) is not None
    assert match_name("Thymosyn Alfa Peptide", cards) is None  # two words off: too loose to trust


# with / without / no / DAC are real product differences, never typos or noise

def test_without_variant_never_matches_the_with_card():
    cards = [card(1, "Glutathione with B12")]
    assert match_name("Glutathione without B12", cards) is None
    assert match_name("Glutathione", cards) is None


def test_with_and_without_cards_each_get_their_own_vendor_name():
    cards = [card(1, "Glutathione with B12"), card(2, "Glutathione without B12")]
    assert names(match_name("Glutathione without B12", cards)) == ["Glutathione without B12"]
    assert names(match_name("Glutathione with B12", cards)) == ["Glutathione with B12"]


def test_dac_vendor_name_never_falls_through_to_the_no_dac_card():
    cards = [card(1, "CJC-1295 (No DAC)")]
    assert match_name("CJC 1295 (with DAC)", cards) is None
    assert match_name("CJC-1295 DAC", cards) is None


def test_a_parenthetical_qualifier_is_not_stripped_to_find_a_match():
    cards = [card(1, "CJC-1295 (DAC)")]
    assert match_name("CJC-1295", cards) is None
    assert match_name("CJC-1295 (No DAC)", cards) is None


def test_unrelated_and_empty_names_do_not_match():
    assert match_name("Testosterone Cypionate", CARDS) is None
    assert match_name("Disp", CARDS) is None
    assert match_name("", CARDS) is None and match_name(None, CARDS) is None


def test_very_short_variants_do_not_cause_accidental_matches():
    assert match_name("TB2(BT)", [card(1, "Bt")]) is None


# ---------------------------------------------------------------- suggest

def test_suggest_lists_the_closest_cards_for_a_human_to_judge():
    out = suggest("SLU332", [card(1, "SLU-PP-332"), card(2, "Semaglutide"), card(3, "Epitalon")])
    assert out[0] == "SLU-PP-332"
    assert "Epitalon" not in out


def test_suggest_returns_at_most_three_and_nothing_for_gibberish():
    many = [card(i, f"Peptide-{i}") for i in range(10)]
    assert len(suggest("Peptide-5", many)) <= 3
    assert suggest("zzzzqqqq", many) == []


# ---------------------------------------------------------------- aliases with slashes, blends by composition

def test_an_alias_may_contain_a_slash():
    cards = [card(1, "5-Amino-1MQ", "5-Amino-1MQ, 5Amino/MQ")]
    assert names(match_name("5Amino/MQ", cards)) == ["5-Amino-1MQ"]


def test_slash_inside_an_alias_does_not_create_matches_on_its_halves():
    cards = [card(1, "CJC-1295 + Ipamorelin", "CJC-1295/Ipamorelin")]
    assert match_name("Ipamorelin", cards) is None


BLENDS = [
    card(1, "GLOW Blend (BPC-157 + TB-500 + GHK-Cu)"),
    card(2, "KLOW Blend (BPC-157 + TB-500 + GHK-Cu + KPV)"),
    card(3, "Wolverine Stack (BPC-157 + TB-500)"),
    card(4, "CJC-1295 + Ipamorelin"),
]


def test_blends_match_by_ingredients_ignoring_doses_order_and_spelling():
    m = match_name("(BPC157 10mg+GHK-CU50mg+TB500 10mg)", BLENDS)
    assert m.how == "blend" and names(m) == ["GLOW Blend (BPC-157 + TB-500 + GHK-Cu)"]
    assert names(match_name("BPC157 10mg+GHK-CU50mg+TB500 10mg+kpv10mg", BLENDS)) == [BLENDS[1].name]
    for vendor in ("BPC157 5mg+TB500 5mg", "BPC157 15mg+TB500 15mg", "TB-500 10 mg + BPC-157 10 mg"):
        assert names(match_name(vendor, BLENDS)) == [BLENDS[2].name], vendor


def test_blend_needs_exactly_the_same_ingredients():
    assert match_name("BPC157 10mg+TB500 10mg+Thymosin 5mg", BLENDS) is None
    assert match_name("BPC157 10mg+KPV 10mg", BLENDS) is None


def test_a_single_ingredient_is_not_a_blend():
    assert match_name("BPC157 10mg", BLENDS) is None


def test_blend_with_a_without_qualifier_is_not_matched_to_the_plain_blend():
    cards = [card(1, "CJC-1295 + Ipamorelin")]
    assert match_name("CJC1295(Without DAC)5mg+IPA5mg", cards) is None

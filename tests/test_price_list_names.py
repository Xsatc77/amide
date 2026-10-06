# tests/test_price_list_names.py
from app.library.price_lists.names import PrefixName, learn_prefixes, propagate_names, repair_names
from app.library.price_lists.rows import ParsedRow, Spec


def r(code, name=None):
    return ParsedRow(code=code, name=name, spec=Spec(5, "mg", 10), pack_price=1.0)


def names_of(rows):
    return [row.name for row in rows]


def test_a_name_in_the_middle_of_a_run_covers_the_whole_run():
    rows = [r("SM5"), r("SM10"), r("SM15", "Semaglutide"), r("SM20"), r("TR5"), r("TR10", "Tirzepatide")]
    propagate_names(rows)
    assert names_of(rows) == ["Semaglutide"] * 4 + ["Tirzepatide"] * 2


def test_a_run_is_the_same_code_prefix_not_the_same_digits():
    rows = [r("HCG1000", "HCG"), r("HCG2000"), r("HCG5000(GK5)"), r("HCG10000(Gk10)")]
    propagate_names(rows)
    assert names_of(rows) == ["HCG"] * 4


def test_rows_with_their_own_names_keep_them():
    rows = [r("BB10", "BPC 5mg + TB 5mg"), r("BB20", "BPC10mg+TB10mg"), r("BB30")]
    propagate_names(rows)
    assert names_of(rows) == ["BPC 5mg + TB 5mg", "BPC10mg+TB10mg", "BPC10mg+TB10mg"]  # an unnamed row takes the nearest name above


def test_products_sharing_a_code_prefix_keep_their_own_names_when_names_sit_on_the_first_row():
    rows = [r("GR2", "GHRP-2"), r("GR2", None), r("GR6", "GHRP-6"), r("GR6", None)]
    propagate_names(rows)
    assert names_of(rows) == ["GHRP-2", "GHRP-2", "GHRP-6", "GHRP-6"]


def test_consecutive_code_less_rows_continue_the_name_above():
    rows = [r(None, "Zorvex"), r(None, None), r("ZX9", None)]
    propagate_names(rows)
    assert names_of(rows) == ["Zorvex", "Zorvex", None]


def test_rows_without_a_code_never_inherit_a_neighbors_name():
    rows = [r("RT5", "Retatrutide"), r(None), r("RT10")]
    propagate_names(rows)
    assert names_of(rows) == ["Retatrutide", None, None]  # the code-less row breaks the run


def test_a_prefix_is_learned_when_two_vendors_agree():
    table = learn_prefixes([
        ("a", "RT", "Retatrutide"), ("a", "RT", "Retatrutide"), ("b", "RT", "retatrutide"), ("c", "RT", "Retarutide")])
    assert table["RT"] == PrefixName("retatrutide", "Retatrutide")


def test_one_vendor_alone_teaches_nothing():
    assert learn_prefixes([("a", "RT", "Retatrutide")] * 5) == {}


def test_a_prefix_with_two_equally_common_meanings_is_not_learned():
    table = learn_prefixes([("a", "LC", "L-Carnitine"), ("b", "LC", "L-Carnitine"),
                            ("c", "LC", "Lipo-C"), ("d", "LC", "Lipo-C")])
    assert "LC" not in table


def test_repair_fills_blank_names_and_flags_them():
    table = {"RT": PrefixName("retatrutide", "Retatrutide")}
    rows = [r("RT10"), r("ZZ9")]
    repair_names(rows, table, is_known=lambda n: True)
    assert rows[0].name == "Retatrutide" and rows[0].flags == ["name-from-code"]
    assert rows[1].name is None and rows[1].flags == []  # nothing learned for ZZ: stays null


def test_repair_fixes_a_misspelling_the_library_does_not_know():
    table = {"RT": PrefixName("retatrutide", "Retatrutide")}
    row = r("RT5", "Retarutide")
    repair_names([row], table, is_known=lambda n: n == "Retatrutide")
    assert row.name == "Retatrutide" and row.flags == ["name-from-code"]


def test_repair_keeps_a_different_product_that_shares_the_prefix():
    table = {"RT": PrefixName("retatrutide", "Retatrutide")}
    cartridge = r("RT10", "RT10 (Double Chamber Cartridge)")
    repair_names([cartridge], table, is_known=lambda n: False)
    assert cartridge.name == "RT10 (Double Chamber Cartridge)" and cartridge.flags == []


def test_repair_keeps_a_name_the_library_already_knows():
    table = {"RT": PrefixName("retatrutide", "Retatrutide")}
    row = r("RT5", "Retatrutide Acetate")
    repair_names([row], table, is_known=lambda n: True)
    assert row.name == "Retatrutide Acetate"


def test_two_products_sharing_a_prefix_with_centered_names_are_not_guessed():
    rows = [r("GR2"), r("GR2", "GHRP-2"), r("GR2"), r("GR6"), r("GR6", "GHRP-6"), r("GR6")]
    propagate_names(rows)
    assert names_of(rows) == ["GHRP-2", "GHRP-2", None, None, "GHRP-6", "GHRP-6"]
    assert rows[2].flags == ["ambiguous-name"] and rows[3].flags == ["ambiguous-name"]
    assert rows[0].flags == [] and rows[5].flags == []


def test_the_same_name_on_both_sides_fills_the_rows_between():
    rows = [r("SM5", "Semaglutide"), r("SM10"), r("SM15", "semaglutide")]
    propagate_names(rows)
    assert rows[1].name == "Semaglutide" and rows[1].flags == []


def test_repair_never_turns_a_no_dac_name_into_a_with_dac_one():
    table = {"CJC": PrefixName("cjc1295dac", "CJC-1295 with DAC")}
    row = r("CJC5", "CJC-1295 no DAC")
    repair_names([row], table, is_known=lambda n: False)
    assert row.name == "CJC-1295 no DAC" and row.flags == []

def test_repair_still_fixes_a_wo_dac_abbreviation_to_the_no_dac_product():
    table = {"CND": PrefixName("cjc1295nodac", "CJC-1295 No DAC")}
    row = r("CND5", "CJC-1295 WO DAC")
    repair_names([row], table, is_known=lambda n: n == "CJC-1295 No DAC")
    assert row.name == "CJC-1295 No DAC" and row.flags == ["name-from-code"]


def test_without_and_no_mean_the_same_to_the_qualifier_guard():
    from app.library.matching import qualifiers
    assert (qualifiers("CJC-1295 Without DAC") == qualifiers("CJC-1295 no DAC")
            == qualifiers("CJC-1295 Whitout DAC") == qualifiers("CJC-1295 WO/Dac"))
    assert qualifiers("CJC-1295 with DAC") != qualifiers("CJC-1295 no DAC")

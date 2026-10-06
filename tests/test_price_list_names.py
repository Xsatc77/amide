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
    assert names_of(rows) == ["BPC 5mg + TB 5mg", "BPC10mg+TB10mg", "BPC 5mg + TB 5mg"]


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

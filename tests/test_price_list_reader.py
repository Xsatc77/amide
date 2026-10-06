# tests/test_price_list_reader.py
from app.library.price_lists.reader import (
    infer_roles, rows_from_lines, rows_from_table, scan_notes, unread_spec_lines,
)
from app.library.price_lists.rows import Spec

# code | name (centered cell: only one row of the group carries it) | spec | price
CODE_FIRST = [
    ["Product Code Product Name Specification Price per kit(USD)", None, None, None],
    ["SM5", "", "5mg*10vials", "$21"],
    ["SM10", "", "10mg*10 vials", "$32"],
    ["SM15", "Semaglutide", "15mg*10 vials", "$43"],
    ["TR5", "", "5mg*10 vials", "$24"],
    ["TR10", "", "10mg*10 vials", "$35"],
]

# name | code | spec | price
NAME_FIRST = [
    ["Price-List-A", None, None, None],
    ["Procutc", "Cat.No", "Specification", "Price"],
    ["Tirzepatide", "TR10", "10mg*10vials", "$31"],
    [None, "TR15", "15mg*10vials", "$42"],
    [None, "TR20", "20mg*10vials", "$53"],
    ["Retarutide", "RT5", "5mg*10vials", "$37"],
]

# a second price column with its own header
TWO_PRICES = [
    ["Acme Peptides product list", None, None, None, None],
    ["Product name", "Cat NO", "Specification", "Unit Price/kit\nUSD", "Wholesale\nprice(20+Kit)"],
    ["", "TR5", "5mg*10vials", "20", "18"],
    ["", "TR10", "10mg*10vials", "30", "27"],
    ["Tirzepatide", "TR15", "15mg*10vials", "40", "36"],
]

# several price tiers, plus a cartridge row whose code only appears inside its name
TIERS = [
    ["Cat.No.", "Product Name", "Specification", "Price", "10kits+", "50kits+", "100kits+"],
    ["EL5", "Eloralintide", "5mg*10vials", "$100", "$98", "$95", "$90"],
    ["EL10", "", "10mg*10vials", "$190", "$185", "$180", "$175"],
    ["RT5", "Retatrutide", "5mg*10vials", "$40", "$38", "$35", "$30"],
    ["", "RT10 (Double Chamber Cartridge)", "10mg*5vials", "$45",
     "Instructions for Use (for reference only) https://example.test/watch", "", ""],
]

# category | product | code | spec | price
CATEGORY_FIRST = [
    ["", "Mazdutide", "MDT10", "10mg*10vials", "$120.00"],
    ["", "Survodutide", "SUR10", "10mg*10vials", "$150.00"],
    ["Reproductive /\nSex Hormones", "HCG", "HCG1000", "1000iu*10vials", "$40.00"],
    ["", "", "HCG2000", "2000iu*10vials", "$60.00"],
    ["", "", "HCG5000(GK5)", "5000iu*10vials", "$110.00"],
]

# garbled and missing cells (text overlap, an empty code cell, a size with no pack count)
GARBLED = [
    ["G10K", "HCG", "10000IU*10vi", "$70.00"],
    ["G5K", None, "5000IUal*10via", "$45.00"],
    [None, None, "10mg*10vials", "$45.00"],
    ["AU100", None, "100mg", "$35.00"],
    ["RA10", "Ara-290", "10mg*10vials", "$40.00"],
]

OILS = [
    ["TC250", "Testosterone Cypionate", "250mg/ml*1vials", "$22/1vial"],
    ["TE250", "Testosterone Enanthate", "250mg/ml*1vials", "$22/1vial"],
    ["SUS250", "SUS250", "250mg*1vials", "$25/1vial"],
]


def test_roles_are_inferred_from_cell_contents():
    roles = infer_roles(CODE_FIRST)
    assert (roles.code, roles.name, roles.spec, roles.prices) == (0, 1, 2, (3,))
    roles = infer_roles(NAME_FIRST)
    assert (roles.name, roles.code, roles.spec, roles.prices) == (0, 1, 2, (3,))
    assert infer_roles(TIERS).prices == (3, 4, 5, 6)
    assert infer_roles(CATEGORY_FIRST).name == 1  # the product column next to the code, not the category column


def test_a_table_with_no_specs_is_not_a_product_table():
    contacts = [["Contact 3: Sam\nWhatsApp: +10000000000", "x"], ["h t t p s : /", ""]]
    assert infer_roles(contacts) is None
    assert rows_from_table(contacts) == []


def test_rows_carry_code_spec_price_and_leave_empty_names_unset():
    rows = rows_from_table(CODE_FIRST, page=2)
    assert [r.code for r in rows] == ["SM5", "SM10", "SM15", "TR5", "TR10"]
    assert [r.name for r in rows] == [None, None, "Semaglutide", None, None]
    assert rows[0].spec == Spec(5, "mg", 10) and rows[0].pack_price == 21.0 and rows[0].page == 2
    assert rows[1].spec == Spec(10, "mg", 10)  # "10mg*10 vials" with a space


def test_name_before_code_layout():
    rows = rows_from_table(NAME_FIRST)
    assert [(r.code, r.name) for r in rows] == [
        ("TR10", "Tirzepatide"), ("TR15", None), ("TR20", None), ("RT5", "Retarutide")]


def test_extra_price_columns_are_kept_under_their_header():
    rows = rows_from_table(TWO_PRICES)
    assert rows[0].pack_price == 20.0 and rows[0].extra_prices == {"Wholesale price(20+Kit)": 18.0}
    tiers = rows_from_table(TIERS)
    assert tiers[0].pack_price == 100.0
    assert tiers[0].extra_prices == {"10kits+": 98.0, "50kits+": 95.0, "100kits+": 90.0}


def test_cartridge_row_takes_its_code_from_the_name_and_ignores_text_in_price_columns():
    cartridge = rows_from_table(TIERS)[-1]
    assert cartridge.code == "RT10" and cartridge.spec == Spec(10, "mg", 5)
    assert cartridge.pack_price == 45.0 and cartridge.extra_prices == {}


def test_category_column_is_not_mistaken_for_the_name():
    rows = rows_from_table(CATEGORY_FIRST)
    assert [(r.code, r.name) for r in rows] == [
        ("MDT10", "Mazdutide"), ("SUR10", "Survodutide"), ("HCG1000", "HCG"), ("HCG2000", None), ("HCG5000(GK5)", None)]
    assert rows[2].spec == Spec(1000, "IU", 10)


def test_garbled_and_missing_cells_are_kept_and_flagged_not_dropped():
    rows = rows_from_table(GARBLED)
    assert rows[0].spec == Spec(10000, "IU", 10) and rows[1].spec == Spec(5000, "IU", 10)
    assert rows[2].code is None and "no-code" in rows[2].flags
    assert rows[3].spec == Spec(100, "mg", None)  # pack size not stated
    assert "code-size-mismatch" in rows[0].flags  # G10K vs 10000 IU is an expected flag


def test_a_pack_of_more_than_ten_vials_is_flagged_not_called_a_kit():
    table = [["ZX5", "Zorvex", "5mg*12vials", "$50"], ["ZX10", "", "10mg*10vials", "$80"],
             ["ZX20", "", "20mg*10vials", "$90"]]
    rows = rows_from_table(table)
    assert rows[0].spec.pack_size == 12 and "unusual-pack-size" in rows[0].flags
    assert rows[1].flags == []


def test_per_vial_price_makes_a_one_vial_box():
    rows = rows_from_table(OILS)
    assert rows[0].spec == Spec(250, "mg/ml", 1) and rows[0].pack_price == 22.0
    assert rows[2].spec == Spec(250, "mg", 1)


def test_wrapped_name_cell_stays_on_one_row():
    table = [
        ["BBG70", "GHK-CU 50mg + BPC-157 10mg +\nTB-500 10mg", "70mg*10vials", "$60"],
        ["BC5", "BPC157", "5mg*10vials", "$20"],
        ["BC10", "", "10mg*10vials", "$30"],
    ]
    rows = rows_from_table(table)
    assert [r.code for r in rows] == ["BBG70", "BC5", "BC10"]
    assert rows[0].name == "GHK-CU 50mg + BPC-157 10mg + TB-500 10mg"


LINES = [
    "ACME LABS",
    "Customer Price List",
    "Name SKU Dose Price",
    "Semaglutide SM10 10mg x 10 vials $21.00",
    "Tirzepatide TR10 10mg x 10 vials $19.00",
]


def test_line_fallback_reads_name_code_spec_and_price():
    rows = rows_from_lines(LINES, page=1)
    assert [(r.code, r.name, r.spec, r.pack_price) for r in rows] == [
        ("SM10", "Semaglutide", Spec(10, "mg", 10), 21.0),
        ("TR10", "Tirzepatide", Spec(10, "mg", 10), 19.0)]


def test_notes_collect_shipping_lines_and_a_warehouse_hint():
    note, hint = scan_notes([
        "Chinese Warehouse - $20 flat shipping worldwide",
        "Shipping fee: $15 for the first 500g",
        "Semaglutide SM10 10mg x 10 vials $21.00",  # a product line is never a note
        "Customs clearance included",
    ])
    assert hint == "china"
    assert note.splitlines() == [
        "Chinese Warehouse - $20 flat shipping worldwide",
        "Shipping fee: $15 for the first 500g",
        "Customs clearance included"]
    assert scan_notes(["US Warehouse stock"]) == (None, "us")
    assert scan_notes(["nothing here"]) == (None, None)


def test_a_list_with_no_code_column_keeps_its_names_and_invents_no_codes():
    table = [["BPC-157", "2mg*10vials", "$30"], ["TB500", "5mg*10vials", "$40"], ["CJC-1295", "2mg*10vials", "$35"]]
    roles = infer_roles(table)
    assert roles.code is None and roles.name == 0
    rows = rows_from_table(table)
    assert [(r.code, r.name) for r in rows] == [(None, "BPC-157"), (None, "TB500"), (None, "CJC-1295")]
    assert all("no-code" in r.flags and "code-size-mismatch" not in r.flags for r in rows)


def test_category_then_name_columns_with_digit_bearing_names_and_no_code():
    table = [["Healing", "BPC-157", "5mg*10vials", "$30"], ["", "TB500", "5mg*10vials", "$40"],
             ["", "KPV10", "10mg*10vials", "$25"]]
    roles = infer_roles(table)
    assert roles.code is None and roles.name == 1


def test_line_fallback_does_not_mistake_a_hyphenated_name_for_a_code():
    rows = rows_from_lines(["CJC-1295 No DAC 2mg*10vials $45.00"])
    assert [(r.code, r.name) for r in rows] == [(None, "CJC-1295 No DAC")]


def test_a_numeric_column_before_the_price_is_not_taken_for_the_price():
    with_headers = [["Code", "Name", "Spec", "MOQ", "Price"], ["ZX5", "Zorvex", "5mg*10vials", "50", "$30"],
                    ["ZX10", "", "10mg*10vials", "100", "$40"], ["ZX20", "", "20mg*10vials", "20", "$25"]]
    rows = rows_from_table(with_headers)
    assert [r.pack_price for r in rows] == [30.0, 40.0, 25.0] and rows[0].extra_prices == {}
    no_headers = [["ZX5", "Zorvex", "5mg*10vials", "50", "$30"], ["ZX10", "", "10mg*10vials", "100", "$40"],
                  ["ZX20", "", "20mg*10vials", "20", "$25"]]
    assert [r.pack_price for r in rows_from_table(no_headers)] == [30.0, 40.0, 25.0]


def test_a_missing_price_is_flagged_not_silent():
    table = [["ZX5", "Zorvex", "5mg*10vials", "$30"], ["ZX10", "", "10mg*10vials", ""],
             ["ZX20", "", "20mg*10vials", "$25"]]
    rows = rows_from_table(table)
    assert rows[1].pack_price is None and "no-price" in rows[1].flags and rows[0].flags == []


def test_a_two_row_table_is_still_read():
    table = [["ZX5", "Zorvex", "5mg*10vials", "$30"], ["ZX10", "", "10mg*10vials", "$40"]]
    assert [(r.code, r.pack_price) for r in rows_from_table(table)] == [("ZX5", 30.0), ("ZX10", 40.0)]


def test_spec_lines_that_produced_no_row_are_counted():
    lines = ["Zorvex ZX5 5mg*10vials $30", "Zorvex ZX10 10mg*10vials $40", "Quillamine QU5 5mg*10vials $20", "footer text"]
    rows = rows_from_lines(lines[:2])
    assert unread_spec_lines(lines, rows) == 1
    assert unread_spec_lines(lines, rows_from_lines(lines)) == 0


def test_line_fallback_accepts_the_real_code_shapes_but_not_names():
    lines = ["5-amino-1MQ 5AM 5mg*10vials $20", "Semax SX5-XA5 5mg*10vials $15", "SS-31 2S10 10mg*10vials $30",
             "Peptide X 10AM 10mg*10vials $25"]
    assert [(r.code, r.name) for r in rows_from_lines(lines)] == [
        ("5AM", "5-amino-1MQ"), ("SX5-XA5", "Semax"), ("2S10", "SS-31"), ("10AM", "Peptide X")]


PER_KIT = [
    ["ACME PEPTIDES", None, None],
    ["US WAREHOUSE A", None, None],
    ["Product Name", "Specification", "Price / kit (USD)"],
    ["Zorvex", "5mg", "$184"],
    [None, "10mg", "$339"],
    ["Quillamine", "3ml", "$18"],
]


def test_a_per_kit_table_with_bare_doses_is_read_with_a_ten_vial_pack():
    rows = rows_from_table(PER_KIT)
    assert [(r.name, r.spec, r.pack_price) for r in rows] == [
        ("Zorvex", Spec(5, "mg", 10), 184.0), (None, Spec(10, "mg", 10), 339.0), ("Quillamine", Spec(3, "ml", 10), 18.0)]


def test_bare_doses_without_a_per_kit_heading_are_still_not_a_product_table():
    table = [["Product", "Dose", "Price"], ["Zorvex", "5mg", "$184"], [None, "10mg", "$339"]]
    assert rows_from_table(table) == []

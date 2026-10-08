"""A sheet written to the library-sheet template shows its narrative on the library page without separate hand-curation."""

from app.library.sheet_parser import parse_sheet, simple_sections

SHEET = """Full disclaimer
Zorvex
Icon reflects category theme only
Test Category
Human trials
Grade A
Tag One
Tag Two
Tag Three
Zorvex
Also known as: Zx
~2 hours half-life
·
Injection
·
Continuous use
A test summary that is long enough to count as a plain language paragraph about an invented compound used only in this test file today.
What Is Zorvex?
First paragraph.
Second paragraph.
How Zorvex Works
It does a thing.
Zorvex Benefits
Benefit one.
Benefit two.
Zorvex Side Effects
Effect one.
Effect two.
Contraindications
Do not use if allergic.
Is Zorvex Legal?
Legal sentence one.
Who Should Consider Zorvex
Person one.
Zorvex Dosage Guide
Beginner
1 mg
daily
Intermediate
2 mg
daily
Advanced
3 mg
daily
Pharmacokinetics
BIOAVAILABILITY
90%
TMAX
1 hour
"""


def test_paragraph_sections_become_one_paragraph_and_list_sections_one_item_per_line():
    simple = simple_sections(parse_sheet(SHEET)["sheet_sections"])
    assert simple["what_is"] == "First paragraph. Second paragraph."
    assert simple["benefits"] == "Benefit one.\nBenefit two."
    assert simple["side_effects"] == "Effect one.\nEffect two."
    assert simple["legal"] == "Legal sentence one."
    assert "how_it_works" in simple and "who_should_consider" in simple


def test_missing_sections_are_left_out():
    assert "drug_interactions" not in simple_sections(parse_sheet(SHEET)["sheet_sections"])


def test_monitoring_rows_may_use_the_template_separator():
    text = SHEET + "Recommended Monitoring\nTEST | WHEN | WHY | TARGET\nBlood calcium | Per prescriber | The label warns | Normal range\n"
    # the monitoring section must come before Pharmacokinetics in a real sheet; the parser slices by header so order does not matter here
    tests = parse_sheet(text)["monitoring_tests"]
    assert tests and tests[0]["test_name"] == "Blood calcium" and tests[0]["target_text"] == "Normal range"

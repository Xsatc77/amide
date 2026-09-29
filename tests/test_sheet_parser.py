from app.library.sheet_parser import parse_sheet

SAMPLE = """Test-Compound-9

Not medical advice. Talk to your provider before using any peptide.

Full disclaimer
Test-Compound-9
Peptide
Research
Grade B
Synthetic Modulator
Recovery
Test-Compound-9
~3-5 hours half-life
·
Injection
·
6w on / 4w off

Also known as: Test-Compound-9, TC9

Calculate dose
Check with AI
Popular
100
Used?

This is a made-up summary paragraph describing Test-Compound-9 for testing purposes only.

What Is Test-Compound-9?

This is a made-up "what is" narrative paragraph for testing.

How Test-Compound-9 Works

This is a made-up mechanism narrative paragraph for testing.

Test-Compound-9 Benefits
A made-up bulleted benefit for testing.
Another made-up bulleted benefit for testing.

Test-Compound-9 Dosage Guide
Community
Community dosing consensus from peptide research communities
LEVEL	DOSE	FREQUENCY

Beginner
	10mg	Daily

Intermediate
	20mg	Daily

Advanced
	30mg	2x Daily
Note:

A made-up dosing note, take each morning with food for testing purposes.

Cycling Protocol
ON PERIOD
6 weeks
OFF PERIOD
4 weeks

A made-up cycling narrative for testing.

How to Use Test-Compound-9
1
Confirm your vial strength

Generic mechanical instruction, made up for testing.

2
Take in the morning

Take each morning on an empty stomach, made up for testing.

Test-Compound-9 Stacking Protocols
WORKS WITH
Made-Up-Partner-A

A made-up rationale for why these pair well, for testing.

AVOID
Made-Up-Drug-Class

A made-up rationale for why these should be avoided, for testing.

Estimated Cost

A made-up cost estimate paragraph for testing.

Test-Compound-9 Side Effects

A made-up side-effects narrative for testing.

Contraindications
A made-up contraindication for testing.

Drug Interactions
A made-up drug interaction note for testing.

Product Quality

A made-up product-quality narrative for testing.

Recommended Monitoring
TEST	WHEN	WHY	TARGET
Made-up test	Baseline	A made-up reason for testing	A made-up target range

Pharmacokinetics
HALF-LIFE
4h
BIOAVAILABILITY
A made-up bioavailability note for testing.
TMAX
~1 hour
DATA CONFIDENCE
low

Is Test-Compound-9 Legal?

A made-up legal-status narrative for testing.

Who Should Consider Test-Compound-9
A made-up audience bullet for testing.

Related Peptides
Made-Up-Partner-A
Recovery

Storage & Stability
BEFORE RECONSTITUTION
A made-up pre-reconstitution storage note for testing.
AFTER RECONSTITUTION
A made-up post-reconstitution storage note for testing.
TEMPERATURE
2-8°C (36-46°F), refrigerated
"""


def test_parses_name_aliases_and_quick_facts():
    result = parse_sheet(SAMPLE)
    assert result["name"] == "Test-Compound-9"
    assert "TC9" in result["aliases"]
    assert result["half_life_text"] == "~3-5 hours half-life" or "3-5 hours" in result["half_life_text"]
    assert result["route_summary"] == "Injection"


def test_parses_dosing_tiers_with_column_position_not_exact_header():
    result = parse_sheet(SAMPLE)
    tiers = {t["level"]: t for t in result["dosing_tiers"]}
    assert tiers["Beginner"]["dose_text"] == "10mg" and tiers["Beginner"]["frequency_text"] == "Daily"
    assert tiers["Intermediate"]["dose_text"] == "20mg"
    assert tiers["Advanced"]["dose_text"] == "30mg" and tiers["Advanced"]["frequency_text"] == "2x Daily"


def test_dosing_tier_time_of_day_only_set_when_text_names_one():
    """Judgment call (plan hands this to the implementer, no single correct answer):

    The sample's "take each morning" mention lives only in the shared dosing NOTE below the
    table, not in any individual tier's own row text. This parser's chosen behavior is to NOT
    propagate a shared-note time-of-day mention down to every tier -- attributing a note that
    applies to the whole table onto each tier individually would overstate what the source text
    actually says about that specific tier (e.g. a future file might have a note that only
    qualifies one tier, and blanket-propagating would misrepresent the others). Instead,
    time_of_day is only set when the time-of-day word appears in the tier's OWN row/line text.
    Since none of Beginner/Intermediate/Advanced rows in SAMPLE mention a time of day themselves,
    all three tiers should have time_of_day == None here, even though the file overall implies
    morning dosing via its shared note.
    """
    result = parse_sheet(SAMPLE)
    tiers = {t["level"]: t for t in result["dosing_tiers"]}
    assert tiers["Beginner"]["time_of_day"] is None
    assert tiers["Intermediate"]["time_of_day"] is None
    assert tiers["Advanced"]["time_of_day"] is None


def test_dosing_table_header_variant_dose_slash_injection_parses_the_same_way():
    """Review Focus item 3: one sampled real file used 'DOSE / INJECTION' instead of 'DOSE' for
    the same column -- the parser must key off column position, not an exact header string."""
    variant = SAMPLE.replace("LEVEL\tDOSE\tFREQUENCY", "LEVEL\tDOSE / INJECTION\tFREQUENCY")
    result = parse_sheet(variant)
    tiers = {t["level"]: t for t in result["dosing_tiers"]}
    assert tiers["Beginner"]["dose_text"] == "10mg" and tiers["Beginner"]["frequency_text"] == "Daily"
    assert tiers["Advanced"]["dose_text"] == "30mg" and tiers["Advanced"]["frequency_text"] == "2x Daily"


def test_dosing_tier_old_names_moderate_and_aggressive_map_to_renamed_tiers():
    """The source files may still use the old Beginner/Moderate/Aggressive naming -- these must be
    stored under the renamed Beginner/Intermediate/Advanced tiers, never a fourth/fifth level."""
    old_names = SAMPLE.replace("Intermediate", "Moderate").replace("Advanced", "Aggressive")
    result = parse_sheet(old_names)
    levels = {t["level"] for t in result["dosing_tiers"]}
    assert levels == {"Beginner", "Intermediate", "Advanced"}


def test_cycle_parsed_when_present():
    result = parse_sheet(SAMPLE)
    assert result["cycle"] == {
        "on_weeks": 6,
        "off_weeks": 4,
        "note": "A made-up cycling narrative for testing.",
    }


def test_cycle_note_does_not_leak_how_to_use_section():
    """Regression test: 'How to Use <name>' puts the peptide name AFTER the
    label, the opposite order from every other templated header in this
    format ('<name> How to Use' would never match a real file). If that
    header isn't recognized as its own section boundary, the entire
    How-to-Use section (numbered steps, mechanical instructions) silently
    gets appended into whatever section precedes it -- here, Cycling
    Protocol's note. Assert none of the How-to-Use section's own text leaks
    into cycle["note"]."""
    result = parse_sheet(SAMPLE)
    note = result["cycle"]["note"]
    assert "How to Use" not in note
    assert "Confirm your vial strength" not in note
    assert "Generic mechanical instruction" not in note
    assert "Take in the morning" not in note


def test_cycle_is_none_when_section_absent():
    """Review Focus item 1: a continuous-use compound (no Cycling Protocol section at all) must
    produce cycle=None, never a row with null/zero weeks."""
    no_cycle_sample = SAMPLE.split("Cycling Protocol")[0] + SAMPLE.split("How to Use Test-Compound-9", 1)[1]
    no_cycle_sample = "How to Use Test-Compound-9" + no_cycle_sample
    result = parse_sheet(SAMPLE.replace(
        "Cycling Protocol\nON PERIOD\n6 weeks\nOFF PERIOD\n4 weeks\n\nA made-up cycling narrative for testing.\n\n",
        ""))
    assert result["cycle"] is None


def test_stack_relations_work_with_and_avoid():
    result = parse_sheet(SAMPLE)
    by_relation = {r["relation"]: r for r in result["stack_relations"]}
    assert by_relation["works_with"]["partner_name"] == "Made-Up-Partner-A"
    assert by_relation["avoid"]["partner_name"] == "Made-Up-Drug-Class"


def test_stack_relation_partner_name_is_free_text_no_lookup():
    """Review Focus item 4: a partner name that matches no real Peptide (a drug class, or a
    not-yet-imported peptide) must still parse cleanly, never raise or get dropped."""
    result = parse_sheet(SAMPLE)
    names = {r["partner_name"] for r in result["stack_relations"]}
    assert "Made-Up-Drug-Class" in names  # not a real peptide name; parsed anyway


def test_monitoring_tests_table():
    result = parse_sheet(SAMPLE)
    [test] = result["monitoring_tests"]
    assert test["test_name"] == "Made-up test" and test["when_text"] == "Baseline"
    assert test["target_text"] == "A made-up target range"


def test_pharmacokinetics_quick_facts():
    result = parse_sheet(SAMPLE)
    assert result["tmax_text"] and "1 hour" in result["tmax_text"]
    assert result["bioavailability_text"] and "bioavailability note" in result["bioavailability_text"]


def test_storage_fields():
    result = parse_sheet(SAMPLE)
    assert "pre-reconstitution" in result["storage_before_text"]
    assert "post-reconstitution" in result["storage_after_text"]
    assert "2-8" in result["storage_temperature_text"]


def test_narrative_sections_captured_verbatim():
    result = parse_sheet(SAMPLE)
    assert "what is" in result["sheet_sections"]["what_is"].lower()
    assert "mechanism" in result["sheet_sections"]["how_it_works"].lower()
    assert "made-up legal-status" in result["sheet_sections"]["legal"].lower()


def test_missing_section_leaves_field_empty_not_a_crash():
    """A file missing an expected optional section must not crash the parser."""
    stripped = SAMPLE.replace(
        "Who Should Consider Test-Compound-9\nA made-up audience bullet for testing.\n\n", "")
    result = parse_sheet(stripped)
    assert result["sheet_sections"].get("who_should_consider") in (None, "")

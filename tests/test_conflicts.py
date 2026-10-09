"""The conflict checker: each of the four checks has a case that must fire and a case that must not."""

from app.library.conflicts import DISCLAIMER, ItemData, MedicineData, check


def item(pid, name, **kw):
    return ItemData(peptide_id=pid, name=name, **kw)


def kinds(findings, check_name):
    return [f for f in findings if f.check == check_name]


def test_dose_above_the_library_high_is_a_caution_and_within_range_is_quiet():
    hot = item(1, "Alpha", dose=12, unit="mg", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    ok = item(2, "Beta", dose=5, unit="mg", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    found = kinds(check([hot], []), "dose")
    assert [f.severity for f in found] == ["caution"] and "above the library's high dose" in found[0].message
    assert kinds(check([ok], []), "dose") == []


def test_units_are_converted_and_unlike_units_are_a_note():
    mcg = item(1, "Alpha", dose=12000, unit="mcg", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    assert kinds(check([mcg], []), "dose")[0].severity == "caution"
    iu = item(2, "Beta", dose=5, unit="IU", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    found = kinds(check([iu], []), "dose")
    assert found[0].severity == "note" and "cannot be compared" in found[0].message


def test_no_library_range_is_a_note_and_a_missing_dose_is_skipped():
    assert kinds(check([item(1, "Alpha", dose=3, unit="mg")], []), "dose")[0].severity == "note"
    assert check([item(1, "Alpha", dose=None, unit="mg", lib_high=10, lib_unit="mg")], []) == []


def test_a_titration_that_more_than_doubles_and_one_well_below_the_low_dose():
    jump = item(1, "Alpha", dose=1, unit="mg", steps=(1, 2.5, 3), lib_low=0.5, lib_mid=2, lib_high=10, lib_unit="mg")
    assert any("more than double" in f.message for f in kinds(check([jump], []), "dose"))
    smooth = item(2, "Beta", dose=1, unit="mg", steps=(1, 2, 3), lib_low=0.5, lib_mid=2, lib_high=10, lib_unit="mg")
    assert not any("more than double" in f.message for f in kinds(check([smooth], []), "dose"))
    low = item(3, "Gamma", dose=0.1, unit="mg", lib_low=1, lib_mid=2, lib_high=10, lib_unit="mg")
    assert "well below" in kinds(check([low], []), "dose")[0].message


def test_two_peptides_of_one_class_are_a_caution_in_one_slot_and_a_note_in_two():
    a = item(1, "Semaglutide", dose=1, unit="mg", time_of_day="am")
    b = item(2, "Tirzepatide", dose=1, unit="mg", time_of_day="am")
    assert [f.severity for f in kinds(check([a, b], []), "stack")] == ["caution"]
    c = item(3, "Tirzepatide", dose=1, unit="mg", time_of_day="pm")
    assert [f.severity for f in kinds(check([a, c], []), "stack")] == ["note"]
    d = item(4, "BPC-157", dose=1, unit="mg")
    assert kinds(check([a, d], []), "stack") == []


def test_the_librarys_avoid_notes_flag_a_pair_once():
    a = item(1, "Alpha Peptide", avoid=(("Beta Peptide", "The library says do not combine these."),))
    b = item(2, "Beta Peptide")
    found = kinds(check([a, b], []), "stack")
    assert len(found) == 1 and "do not combine" in found[0].message
    assert kinds(check([a], []), "stack") == []


def test_medicine_cautions_get_stronger_above_the_library_mid_dose_and_quote_the_medicine_dose():
    meds = [MedicineData("Metformin", "500 mg twice daily")]
    low = item(1, "Semaglutide", dose=0.25, unit="mg", lib_low=0.25, lib_mid=1, lib_high=2.4, lib_unit="mg")
    high = item(1, "Semaglutide", dose=2, unit="mg", lib_low=0.25, lib_mid=1, lib_high=2.4, lib_unit="mg")
    assert kinds(check([low], meds), "medicine")[0].severity == "note"
    found = kinds(check([high], meds), "medicine")[0]
    assert found.severity == "caution" and "Metformin (500 mg twice daily)" in found.message and "matters more" in found.message
    assert kinds(check([high], []), "medicine") == []


def test_a_sedating_peptide_in_a_daytime_slot_with_a_listed_sedative_is_a_timing_caution():
    meds = [MedicineData("Zolpidem")]
    day = item(1, "DSIP", dose=0.1, unit="mg", time_of_day="am")
    night = item(2, "DSIP", dose=0.1, unit="mg", time_of_day="bedtime")
    assert kinds(check([day], meds), "timing")[0].severity == "caution"
    assert kinds(check([night], meds), "timing") == []
    assert kinds(check([day], []), "timing") == []


def test_findings_are_sorted_cautions_first_serialize_and_the_disclaimer_exists():
    hot = item(1, "Alpha", dose=12, unit="mg", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    note = item(2, "Beta", dose=3, unit="mg")
    found = check([note, hot], [])
    assert [f.severity for f in found] == ["caution", "note"]
    assert set(found[0].as_dict()) == {"severity", "check", "peptide", "other", "message", "peptide_id"}
    assert "prescriber or pharmacist" in DISCLAIMER and check([], []) == []

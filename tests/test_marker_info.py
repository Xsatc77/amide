"""The marker reference: what a lab measures, and what values above or below the range can point to."""

import html

from app.labs import marker_info
from app.models import LabMarker

from tests.test_labs import _clear_lab_panels, _tester_id
from tests.test_labcorp_import import _post_panel


def test_every_marker_in_the_list_has_an_entry_except_other():
    missing = [m.value for m in LabMarker if m is not LabMarker.OTHER and marker_info.lookup(m.value) is None]
    assert missing == []


def test_every_entry_says_what_it_is_and_what_high_and_low_can_mean():
    for topic, (name, short, about, high, low) in marker_info.INFO.items():
        assert all(part.strip() for part in (name, short, about, high, low)), topic
        assert len(short) <= 90, f"{topic}: the hover line is one short sentence"


def test_a_labs_own_name_finds_the_entry():
    assert marker_info.lookup("Cholesterol, Total")["name"] == "Total Cholesterol"
    assert marker_info.lookup("Vitamin D, 25-Hydroxy")["name"] == "Vitamin D"
    assert marker_info.lookup("HDL Cholesterol")["name"] == "HDL Cholesterol"
    assert marker_info.lookup("Hemoglobin A1c")["name"] == "HbA1c"
    assert marker_info.lookup("Hemoglobin")["name"] == "Hemoglobin"
    assert marker_info.lookup("Bilirubin, Total")["name"] == "Bilirubin"
    assert marker_info.lookup("Calcium")["name"] == "Calcium"
    assert marker_info.lookup("Ionized Calcium")["name"] == "Ionized Calcium"


def test_a_ratio_is_not_described_as_one_of_its_parts():
    assert marker_info.lookup("BUN/Creatinine Ratio")["name"] == "BUN/Creatinine Ratio"
    assert marker_info.lookup("A/G Ratio")["name"] == "Albumin/Globulin Ratio"
    assert marker_info.lookup("Some Unknown Ratio") is None


def test_an_unknown_marker_has_no_entry():
    assert marker_info.lookup("Zzyzx Index") is None


def test_marker_names_on_the_labs_tab_carry_the_short_description_and_open_the_popup(client, db):
    me = _tester_id()
    try:
        assert _post_panel(client, [("LDL", "90", "mg/dL")]).status_code == 303
        page = client.get("/measurements?tab=labs").text
        assert 'data-marker-info="LDL"' in page
        assert marker_info.lookup("LDL")["short"] in html.unescape(page)
        assert 'id="marker-info-dialog"' in page and 'id="marker-info-data"' in page
        assert "Above The Range" in page and "Below The Range" in page
    finally:
        _clear_lab_panels(me)

"""The LabCorp report importer and the peptide stars/filter on the Labs tab.

Every report text here is invented (a made-up person and made-up numbers in LabCorp's layout)."""

from datetime import date, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.labs import labcorp, monitoring
from app.models import LabMarker, LabPanel, Peptide, PeptideMonitoringTest, Protocol, ProtocolItem, User

from tests.test_labs import _clear_lab_panels, _tester_id

SAMPLE = """Sample, Pat Q Patient Report
Patient ID: 000000001 Age: 40 Account Number: 1
Date Collected: 03/04/2026 Date Received: 03/04/2026 Date Reported: 03/09/2026 Fasting: Yes
CBC With Differential/Platelet
Test Current Result and Flag Previous Result and Date Units Reference Interval
WBC 01 4.8 4.5 10/08/2024 x10E3/uL 3.4-10.8
MCV 01 93 96 10/08/2024 fL 79-97
Neutrophils 01 70 65 10/08/2024 % Not Estab.
Comp. Metabolic Panel (14)
Test Current Result and Flag Previous Result and Date Units Reference Interval
Glucose 01 93 96 07/08/2025 mg/dL 70-99
eGFR 103 101 07/08/2025 mL/min/1.73 >59
BUN/Creatinine Ratio 22 High 20 07/08/2025 9-20
Bilirubin, Total 01 1.8 High 1.3 07/08/2025 mg/dL 0.0-1.2
Alkaline Phosphatase 01 93 91* 07/08/2025 IU/L 47-123
* Previous Reference Interval: (Alkaline Phosphatase: 44-121 IU/L)
LP+Non-HDL Cholesterol
Test Current Result and Flag Previous Result and Date Units Reference Interval
HDL Cholesterol 01 55 64 10/08/2024 mg/dL >39
VLDL Cholesterol Cal 8 mg/dL 5-40
______________________________________________________________________
: Risk Assessment and Treatment Goals-Adults 30-79 years[1] :
Vitamin E
Test Current Result and Flag Previous Result and Date Units Reference Interval
Vitamin E(Gamma Tocopherol)
1.0 0.8 10/08/2024 mg/L 0.5-5.5
A, 02
Vitamin K1
Test Current Result and Flag Previous Result and Date Units Reference Interval
Vitamin K1 A, 02 0.13 <0.10 10/08/2024 ng/mL 0.10-2.20
Detection Limit = 5
Relative Risk for Future Cardiovascular Event
Low <1.00
Average 1.00 - 3.00
High >3.00
Date Created and Stored 03/09/26 1224 ET Final Report Page 4 of 5
"""


def _by_name(text=SAMPLE):
    return {r.name: r for r in labcorp.parse_text(text).rows}


def test_the_draw_date_is_the_date_collected():
    assert labcorp.parse_text(SAMPLE).drawn_at == date(2026, 3, 4)


def test_current_result_unit_and_range_are_read_and_the_previous_columns_skipped():
    wbc = _by_name()["WBC"]
    assert (wbc.value, wbc.unit, wbc.range_low, wbc.range_high) == (4.8, "x10E3/uL", 3.4, 10.8)
    assert wbc.marker is LabMarker.WBC


def test_a_row_with_a_flag_and_no_unit_is_read():
    ratio = _by_name()["BUN/Creatinine Ratio"]
    assert (ratio.value, ratio.unit, ratio.range_low, ratio.range_high) == (22, None, 9, 20)
    assert (_by_name()["Bilirubin, Total"].value, _by_name()["Bilirubin, Total"].range_high) == (1.8, 1.2)


def test_a_previous_result_marked_with_an_asterisk_does_not_confuse_the_row():
    assert _by_name()["Alkaline Phosphatase"].value == 93


def test_one_sided_and_missing_ranges():
    rows = _by_name()
    assert (rows["eGFR"].range_low, rows["eGFR"].range_high) == (59, None)
    assert (rows["HDL Cholesterol"].range_low, rows["HDL Cholesterol"].range_high) == (39, None)
    assert (rows["Neutrophils"].range_low, rows["Neutrophils"].range_high) == (None, None)


def test_a_row_without_a_previous_result_is_read():
    vldl = _by_name()["VLDL Cholesterol Cal"]
    assert (vldl.value, vldl.unit, vldl.range_low, vldl.range_high) == (8, "mg/dL", 5, 40)


def test_a_less_than_result_keeps_its_qualifier_and_previous_less_than_is_skipped():
    k1 = _by_name()["Vitamin K1"]
    assert k1.value == 0.13 and k1.qualifier is None
    text = SAMPLE.replace("Vitamin K1 A, 02 0.13 <0.10", "Vitamin K1 A, 02 <0.10 0.13")
    assert (_by_name(text)["Vitamin K1"].qualifier, _by_name(text)["Vitamin K1"].value) == ("<", 0.10)


def test_a_name_wrapped_onto_the_line_above_is_joined():
    gamma = _by_name()["Vitamin E(Gamma Tocopherol)"]
    assert (gamma.value, gamma.unit, gamma.range_low, gamma.range_high) == (1.0, "mg/L", 0.5, 5.5)


def test_notes_tables_and_footers_are_not_results():
    names = set(_by_name())
    assert names == {"WBC", "MCV", "Neutrophils", "Glucose", "eGFR", "BUN/Creatinine Ratio", "Bilirubin, Total",
                     "Alkaline Phosphatase", "HDL Cholesterol", "VLDL Cholesterol Cal", "Vitamin E(Gamma Tocopherol)", "Vitamin K1"}


def test_known_tests_use_amide_markers_and_the_rest_keep_the_labs_name():
    rows = _by_name()
    assert rows["HDL Cholesterol"].marker is LabMarker.HDL
    assert rows["MCV"].marker is LabMarker.OTHER and rows["MCV"].marker_other == "MCV"


def test_glucose_is_fasting_glucose_only_on_a_fasting_report():
    assert _by_name()["Glucose"].marker is LabMarker.FASTING_GLUCOSE
    plain = _by_name(SAMPLE.replace("Fasting: Yes", "Fasting: No"))["Glucose"]
    assert plain.marker is LabMarker.OTHER and plain.marker_other == "Glucose"


def test_text_with_no_lab_rows_gives_nothing():
    parsed = labcorp.parse_text("Just a letter.\nNothing here 12 mg/dL 1-2\n")
    assert parsed.rows == [] and parsed.drawn_at is None


# ---------------------------------------------------------------- which labs a peptide covers

def test_a_panel_in_the_monitoring_list_covers_its_parts():
    lipid = monitoring.test_topics("Lipid panel")
    assert {"ldl", "hdl", "trig", "totalchol"} <= lipid
    assert {"alt", "ast", "bili"} <= monitoring.test_topics("Liver function tests (ALT, AST, bilirubin)")
    assert "hba1c" in monitoring.test_topics("Fasting glucose / HbA1c") and "glucose" in monitoring.test_topics("Fasting glucose / HbA1c")


def test_results_are_matched_by_topic():
    peptides = [{"id": 1, "name": "A", "topics": monitoring.peptide_topics(["Lipid panel", "Serum zinc"])}]
    assert [p["name"] for p in monitoring.covering("LDL", peptides)] == ["A"]
    assert [p["name"] for p in monitoring.covering("Non-HDL Cholesterol", peptides)] == ["A"]
    assert [p["name"] for p in monitoring.covering("Zinc, Plasma or Serum", peptides)] == ["A"]
    assert monitoring.covering("Hemoglobin A1c", peptides) == []
    assert monitoring.covering("Hemoglobin", [{"id": 2, "name": "B", "topics": monitoring.test_topics("HbA1c")}]) == []


def test_urine_and_imaging_checks_are_not_lab_results():
    assert monitoring.test_topics("Urine calcium") == set() and monitoring.test_topics("Gallbladder ultrasound") == set()
    assert "icalcium" in monitoring.result_topics("Calcium, Ionized, Serum") and "calcium" not in monitoring.result_topics("Calcium, Ionized, Serum")


# ---------------------------------------------------------------- the Labs tab

def _setup_protocol(tests: list[str], *, active=True):
    """An active (or ended) protocol for the tester holding one made-up peptide that lists `tests` for monitoring. Returns the peptide id."""
    with SessionLocal() as s:
        me = s.scalar(select(User.id).where(User.username_key == "tester"))
        peptide = Peptide(name=f"Teststar {len(s.scalars(select(Peptide)).all())}")
        peptide.monitoring_tests = [PeptideMonitoringTest(test_name=t) for t in tests]
        s.add(peptide)
        s.flush()
        protocol = Protocol(name="Star protocol", start_date=date.today() - timedelta(days=3), owner_id=me,
                            ended_on=None if active else date.today() - timedelta(days=1))
        protocol.items.append(ProtocolItem(peptide_id=peptide.id))
        s.add(protocol)
        s.commit()
        return peptide.id, protocol.id, peptide.name


def _cleanup(peptide_id, protocol_id):
    with SessionLocal() as s:
        s.query(Protocol).filter_by(id=protocol_id).delete()
        s.query(Peptide).filter_by(id=peptide_id).delete()
        s.commit()


def _post_panel(client, rows):
    markers, values, units = zip(*rows)
    return client.post("/labs/panels", data={
        "drawn_at": "2026-05-01", "marker[]": list(markers), "value[]": list(values), "unit[]": list(units),
        "range_low[]": [""] * len(rows), "range_high[]": [""] * len(rows), "marker_other[]": [""] * len(rows)}, follow_redirects=False)


def test_a_lab_a_peptide_recommends_gets_a_star_and_the_filter_narrows_to_it(client, db):
    me = _tester_id()
    pid, proto, name = _setup_protocol(["Lipid panel"])
    try:
        assert _post_panel(client, [("LDL", "90", "mg/dL"), ("TSH", "2.5", "mIU/L")]).status_code == 303
        page = client.get("/measurements?tab=labs").text
        assert "lab-star" in page and f"Recommended for monitoring with {name}" in page
        assert f'<option value="{pid}"' in page
        narrowed = client.get(f"/measurements?tab=labs&peptide={pid}").text
        assert 'data-marker-info="LDL"' in narrowed and 'data-marker-info="TSH"' not in narrowed
        assert f'<option value="{pid}" selected' in narrowed
    finally:
        _clear_lab_panels(me)
        _cleanup(pid, proto)


def test_an_ended_protocol_adds_no_peptide_and_no_stars(client, db):
    me = _tester_id()
    pid, proto, name = _setup_protocol(["Lipid panel"], active=False)
    try:
        _post_panel(client, [("LDL", "90", "mg/dL")])
        page = client.get(f"/measurements?tab=labs&peptide={pid}").text
        assert f'<option value="{pid}"' not in page and "Recommended for monitoring" not in page
        assert 'data-marker-info="LDL"' in page                  # an unknown peptide filters nothing
    finally:
        _clear_lab_panels(me)
        _cleanup(pid, proto)


# ---------------------------------------------------------------- the import route

def _import(client, monkeypatch, *, text=SAMPLE, data=b"%PDF-1.4 made up", name="report.pdf"):
    monkeypatch.setattr(labcorp, "read_pdf", lambda raw: labcorp.parse_text(text))
    return client.post("/labs/import", files={"report": (name, data, "application/pdf")}, follow_redirects=False)


def test_importing_a_report_makes_a_panel_with_its_results_and_the_pdf(client, db, monkeypatch):
    me = _tester_id()
    try:
        r = _import(client, monkeypatch)
        assert r.status_code == 303 and "imported=12" in r.headers["location"]
        with SessionLocal() as s:
            [panel] = s.scalars(select(LabPanel).where(LabPanel.owner_id == me)).all()
            assert panel.drawn_at == date(2026, 3, 4) and panel.report_filename and len(panel.results) == 12
            hdl = next(x for x in panel.results if x.marker is LabMarker.HDL)
            assert (hdl.value, hdl.unit, hdl.range_low) == (55, "mg/dL", 39)
        assert "Imported 12 results" in client.get(r.headers["location"]).text
    finally:
        _clear_lab_panels(me)


def test_importing_the_same_date_twice_is_refused(client, db, monkeypatch):
    me = _tester_id()
    try:
        assert _import(client, monkeypatch).status_code == 303
        again = _import(client, monkeypatch)
        assert again.status_code == 422 and "already have a lab panel" in again.text
        with SessionLocal() as s:
            assert len(s.scalars(select(LabPanel).where(LabPanel.owner_id == me)).all()) == 1
    finally:
        _clear_lab_panels(me)


def test_a_file_that_is_not_a_readable_report_is_refused_and_nothing_is_saved(client, db, monkeypatch):
    me = _tester_id()
    try:
        assert "not a PDF" in _import(client, monkeypatch, data=b"hello").text
        assert "saved as a PDF" in _import(client, monkeypatch, name="report.txt").text
        assert "No LabCorp results" in _import(client, monkeypatch, text="Just a letter.").text
        with SessionLocal() as s:
            assert s.scalars(select(LabPanel).where(LabPanel.owner_id == me)).all() == []
    finally:
        _clear_lab_panels(me)

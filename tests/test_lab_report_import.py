"""Importing lab reports from labs other than LabCorp, and the Import popup and collapsed Charts on the Labs tab.

Every report text here is invented (made-up numbers in each lab's layout)."""

from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.labs import reports
from app.models import LabMarker, LabPanel

from tests.test_labs import _clear_lab_panels, _tester_id

QUEST = """Report Status: Final
COMMENTS: FASTING:NO
Test Name In Range Out Of Range Reference Range Lab
LIPID PANEL, STANDARD
CHOLESTEROL, TOTAL 210 H <200 mg/dL KS
HDL CHOLESTEROL 44 L > OR = 50 mg/dL KS
If a non-fasting specimen was collected, consider
repeat triglyceride testing on a fasting specimen
LDL-CHOLESTEROL 120 H mg/dL (calc) KS
Reference range: <100
Desirable range <100 mg/dL for primary prevention;
CHOL/HDLC RATIO 4.8 <5.0 (calc) KS
GLUCOSE 92 65-139 mg/dL
EGFR 83 > OR = 60 mL/min/1.73m2
BUN/CREATININE RATIO SEE NOTE: 6-22 (calc)
HEMATOCRIT 36.9 L35.0-45.0 %
MPV 9.6 n 7.5-12.5 fL
FOLATE, SERUM 14.1 ng/mL
NEUTROPHILS 5-1.3 %
LYMPHOCYTES 38.2 %
CLIENT SERVICES: 866.697.8378 PAGE 1 OF 3
"""

GENERIC = """Some Lab Results
Collection Date: 2026-02-03
Test Result Unit Reference Range
Total Testosterone 540 ng/dL 264-916
Vitamin D 28 ng/mL 30-100
Estradiol 22.5 pg/mL <40
Phone 555 1234
"""


def _rows(text, reader=reports.parse_quest):
    return {r.name: r for r in reader(text).rows}


def test_quest_rows_carry_value_unit_and_range():
    rows = _rows(QUEST)
    chol = rows["CHOLESTEROL, TOTAL"]
    assert (chol.marker, chol.value, chol.unit, chol.range_low, chol.range_high) == (LabMarker.TOTAL_CHOLESTEROL, 210, "mg/dL", None, 200)
    hdl = rows["HDL CHOLESTEROL"]
    assert (hdl.marker, hdl.range_low, hdl.range_high) == (LabMarker.HDL, 50, None)
    assert rows["GLUCOSE"].marker is LabMarker.OTHER                # the report says FASTING:NO
    assert rows["EGFR"].unit == "mL/min/1.73m2" and rows["EGFR"].range_low == 60


def test_quest_flags_and_glued_flags_are_not_read_as_units_or_values():
    rows = _rows(QUEST)
    assert (rows["HEMATOCRIT"].range_low, rows["HEMATOCRIT"].range_high, rows["HEMATOCRIT"].unit) == (35, 45, "%")
    assert (rows["MPV"].unit, rows["MPV"].range_high) == ("fL", 12.5)
    assert rows["FOLATE, SERUM"].unit == "ng/mL"                    # not "g/mL" after taking the n for a flag


def test_quest_takes_a_range_from_the_line_below_and_skips_notes_and_garbage():
    rows = _rows(QUEST)
    assert rows["LDL-CHOLESTEROL"].range_high == 100 and rows["LDL-CHOLESTEROL"].unit == "mg/dL"
    assert rows["CHOL/HDLC RATIO"].unit is None
    for skipped in ("BUN/CREATININE RATIO", "NEUTROPHILS", "CLIENT SERVICES"):
        assert not any(name.startswith(skipped) for name in rows)
    assert reports.parse_quest(QUEST).drawn_at is None             # Quest's date is not in the text


def test_a_quest_report_with_a_fasting_yes_reads_glucose_as_fasting_glucose():
    rows = _rows(QUEST.replace("FASTING:NO", "FASTING:YES"))
    assert rows["GLUCOSE"].marker is LabMarker.FASTING_GLUCOSE


def test_the_general_reader_finds_name_result_unit_range_and_the_date():
    parsed = reports.parse_generic(GENERIC)
    assert parsed.drawn_at == date(2026, 2, 3)
    rows = {r.name: r for r in parsed.rows}
    assert set(rows) == {"Total Testosterone", "Vitamin D", "Estradiol"}
    t = rows["Total Testosterone"]
    assert (t.marker, t.value, t.unit, t.range_low, t.range_high) == (LabMarker.TOTAL_TESTOSTERONE, 540, "ng/dL", 264, 916)
    assert (rows["Estradiol"].range_low, rows["Estradiol"].range_high) == (None, 40)


# ---------------------------------------------------------------- the route and the page

def _import(client, monkeypatch, lab, text, **form):
    monkeypatch.setattr(reports, "extract_text", lambda raw: text)
    return client.post("/labs/import", data={"lab": lab, **form},
                       files={"report": ("r.pdf", b"%PDF-1.4 made up", "application/pdf")}, follow_redirects=False)


def test_a_quest_report_needs_a_draw_date_then_becomes_a_panel(client, db, monkeypatch):
    me = _tester_id()
    try:
        refused = _import(client, monkeypatch, "quest", QUEST)
        assert refused.status_code == 422 and "does not say when the blood was drawn" in refused.text
        ok = _import(client, monkeypatch, "quest", QUEST, drawn_at="2026-05-06")
        assert ok.status_code == 303
        with SessionLocal() as s:
            [panel] = s.scalars(select(LabPanel).where(LabPanel.owner_id == me)).all()
            assert panel.drawn_at == date(2026, 5, 6) and panel.notes == "Imported from a Quest Diagnostics report."
            assert any(r.marker is LabMarker.HDL for r in panel.results)
    finally:
        _clear_lab_panels(me)


def test_another_lab_uses_the_general_reader_and_an_unknown_lab_is_refused(client, db, monkeypatch):
    me = _tester_id()
    try:
        assert _import(client, monkeypatch, "everlywell", GENERIC).status_code == 303
        with SessionLocal() as s:
            [panel] = s.scalars(select(LabPanel).where(LabPanel.owner_id == me)).all()
            assert panel.drawn_at == date(2026, 2, 3) and len(panel.results) == 3
        assert "Choose which lab" in _import(client, monkeypatch, "nonsense", GENERIC).text
    finally:
        _clear_lab_panels(me)


def test_the_labs_page_has_an_import_button_and_popup_and_charts_start_collapsed(client, db):
    me = _tester_id()
    try:
        from tests.test_labcorp_import import _post_panel
        _post_panel(client, [("LDL", "90", "mg/dL")])
        client.post("/labs/panels", data={"drawn_at": "2026-06-01", "marker[]": ["LDL"], "value[]": ["95"], "unit[]": ["mg/dL"],
                                          "range_low[]": [""], "range_high[]": [""], "marker_other[]": [""]}, follow_redirects=False)
        page = client.get("/measurements?tab=labs").text
        assert 'data-action="open-lab-import"' in page and 'id="lab-import-dialog"' in page
        for name in ("LabCorp", "Quest Diagnostics", "Any Lab Test Now!", "myLAB Box", "Direct Labs", "Rythm Health", "Function Health",
                     "Everlywell", "RxHomeTest", "DrSays", "SiPhox Health", "LetsGetChecked", "Superpower", "Jinfiniti"):
            assert name in page
        assert 'class="lab-charts"' in page and "id=\"lab-charts\" open" not in page
    finally:
        _clear_lab_panels(me)

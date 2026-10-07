"""Labs entry: results like <5 and >100, every marker listed inline, and older panels entered for a past date."""

import html
import json
import re
from datetime import date

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import LabMarker, LabPanel, LabResult


@pytest.fixture(autouse=True)
def _clean(client, me):
    def wipe():
        with SessionLocal() as s:
            s.query(LabPanel).filter_by(owner_id=me).delete()
            s.commit()
    wipe()
    yield
    wipe()


def post(client, rows, drawn_at="2025-03-02"):
    data = {"drawn_at": drawn_at, "marker[]": [r[0] for r in rows], "value[]": [r[1] for r in rows], "unit[]": [r[2] if len(r) > 2 else "" for r in rows],
            "range_low[]": [""] * len(rows), "range_high[]": [""] * len(rows), "marker_other[]": [r[3] if len(r) > 3 else "" for r in rows]}
    return client.post("/labs/panels", data=data, follow_redirects=False)


def results(me):
    with SessionLocal() as s:
        return [(r.marker, r.value, r.qualifier) for p in s.scalars(select(LabPanel).where(LabPanel.owner_id == me)) for r in p.results]


@pytest.mark.parametrize("raw,value,qualifier", [("<5", 5.0, "<"), (">100", 100.0, ">"), ("> 7.5", 7.5, ">"), ("0", 0.0, None), ("-3", -3.0, None), ("12.5", 12.5, None)])
def test_a_result_can_be_less_than_greater_than_zero_or_negative(client, db, me, raw, value, qualifier):
    assert post(client, [("TSH", raw)]).status_code == 303
    assert results(me) == [(LabMarker.TSH, value, qualifier)]


@pytest.mark.parametrize("raw", ["<", "<>5", "abc", "5<", "1e999", "nan"])
def test_nonsense_values_are_refused(client, db, me, raw):
    assert post(client, [("TSH", raw)]).status_code == 422
    assert results(me) == []


def test_a_qualified_result_shows_its_symbol_and_is_never_flagged_out_of_range(client, db, me):
    post(client, [("TSH", "<0.01", "mIU/L")])
    page = html.unescape(client.get("/measurements", params={"tab": "labs"}).text)
    assert "&lt;0.01" in client.get("/measurements", params={"tab": "labs"}).text or "<0.01" in page
    assert "lab-out-of-range" not in page


def test_editing_a_panel_gives_back_the_symbol(client, db, me):
    post(client, [("TSH", ">100")])
    page = client.get("/measurements", params={"tab": "labs"}).text
    data = json.loads(html.unescape(re.search(r'id="lab-panels-edit-data">(.*?)</script>', page, re.S).group(1)))
    assert data[0]["rows"][0]["value"] == ">100"


def test_the_value_box_takes_text_so_a_symbol_can_be_typed(client, db):
    row = client.get("/measurements", params={"tab": "labs"}).text.split('id="lab-row-template"')[1].split("</template>")[0]
    value_input = re.search(r'<input name="value\[\]"[^>]*>', row).group(0)
    assert 'type="number"' not in value_input and 'inputmode="decimal"' in value_input


def test_the_sheet_lists_every_marker_inline_and_has_an_add_marker_button(client, db):
    js = client.get("/static/js/labs.js").text
    page = client.get("/measurements", params={"tab": "labs"}).text
    assert "addMarkerRows" in js and "Add marker" in page


def test_an_older_panel_can_be_entered_for_a_past_date_and_charted(client, db, me):
    post(client, [("TSH", "2.1")], drawn_at="2022-06-01")
    post(client, [("TSH", "2.8")], drawn_at="2024-06-01")
    page = client.get("/measurements", params={"tab": "labs"}).text
    assert "<polyline" in page.split('id="labs-charts-heading"')[1]


def test_a_unit_that_is_too_long_is_flagged_on_its_own_field(client, db):
    r = post(client, [("TSH", "2", "x" * 25)])
    assert r.status_code == 422
    error_data = json.loads(html.unescape(re.search(r'id="lab-error-data">(.*?)</script>', r.text, re.S).group(1)))
    assert error_data["errors"]["unit_0"]

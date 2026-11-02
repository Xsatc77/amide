"""The Log A Measurement dialog shows the last value beside each box, and a note can be attached to any measurement, shown as an info icon."""

import html
import re

import pytest

from app.db import SessionLocal
from app.models import BodyMeasurement


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(BodyMeasurement).filter_by(owner_id=me).delete()
        s.commit()


def dialog(client) -> str:
    return html.unescape(client.get("/measurements").text.split('id="measurement-dialog"')[1].split("</dialog>")[0])


def prior_of(text: str, name: str) -> str | None:
    found = re.search(rf'data-prior-for="{name}"[^>]*>([^<]*)<', text)
    return found.group(1).strip() if found else None


def test_the_last_logged_value_shows_beside_each_box(client):
    client.post("/measurements", data={"measured_at": "2026-09-20", "weight_lbs": "181", "waist_in": "40", "biceps_l_in": "15.25"})
    client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "180.5", "systolic": "120", "diastolic": "80"})
    text = dialog(client)
    assert prior_of(text, "weight_lbs") == "180.5"
    assert prior_of(text, "waist_in") == "40"                  # the newest entry that has a waist, not the newest entry
    assert prior_of(text, "biceps_l_in") == "15.25"
    assert prior_of(text, "systolic") == "120" and prior_of(text, "diastolic") == "80"
    assert prior_of(text, "calf_r_in") is None                 # never logged: nothing to show


def test_the_arms_and_legs_get_a_line_to_the_box_and_neck_waist_hips_sit_above_or_below(client):
    client.post("/measurements", data={"measured_at": "2026-09-20", "neck_in": "17", "waist_in": "40", "hips_in": "41", "biceps_l_in": "15", "quad_r_in": "29"})
    text = dialog(client)
    assert text.count('class="mini-prior-line"') == 2                                       # biceps L and quad R only
    assert re.search(r'class="mini-prior prior-left"[^>]*data-prior-for="biceps_l_in"', text)
    assert re.search(r'class="mini-prior prior-right"[^>]*data-prior-for="quad_r_in"', text)
    assert re.search(r'class="mini-prior prior-above"[^>]*data-prior-for="neck_in"', text)
    assert re.search(r'class="mini-prior prior-above"[^>]*data-prior-for="waist_in"', text)
    assert re.search(r'class="mini-prior prior-below"[^>]*data-prior-for="hips_in"', text)


def test_a_note_is_saved_with_the_entry_and_shown_as_an_info_icon_beside_that_measurement(client):
    client.post("/measurements", data={"measured_at": "2026-09-20", "waist_in": "40", "note__waist_in": "Measured after lunch", "note__weight_lbs": "  "})
    with SessionLocal() as s:
        row = s.query(BodyMeasurement).one()
        assert row.notes == {"waist_in": "Measured after lunch"}                               # a blank note is not kept
    page = html.unescape(client.get("/measurements").text)
    assert re.search(r'class="silhouette-note"[^>]*title="Measured after lunch"', page)          # beside Waist on the silhouette
    assert re.search(r'class="note-icon"[^>]*title="Waist: Measured after lunch"', page)         # on the entry's row


def test_the_note_popup_offers_every_measurement(client):
    page = html.unescape(client.get("/measurements").text)
    popup = page.split('id="note-dialog"')[1].split("</dialog>")[0]
    assert "Which Measurement" in popup and "<textarea" in popup
    for label in ("Weight", "Heart Rate", "Systolic", "Diastolic", "Neck", "Waist", "Hips", "Biceps L", "Biceps R", "Forearm L", "Forearm R", "Quad L", "Quad R", "Calf L", "Calf R"):
        assert f">{label}<" in popup
    assert 'data-action="open-note-dialog"' in page


def test_a_note_comes_back_after_a_failed_submit_and_is_kept_to_300_characters(client):
    r = client.post("/measurements", data={"measured_at": "2026-09-20", "waist_in": "oops", "note__waist_in": "kept for retry"})
    assert r.status_code == 422 and 'name="note__waist_in" value="kept for retry"' in r.text
    client.post("/measurements", data={"measured_at": "2026-09-21", "waist_in": "40", "note__waist_in": "x" * 400})
    with SessionLocal() as s:
        assert len(s.query(BodyMeasurement).one().notes["waist_in"]) == 300


def test_a_note_belongs_to_the_entry_the_value_came_from(client):
    client.post("/measurements", data={"measured_at": "2026-09-20", "waist_in": "40", "note__waist_in": "old note"})
    client.post("/measurements", data={"measured_at": "2026-09-28", "weight_lbs": "180", "note__weight_lbs": "fasted"})
    page = html.unescape(client.get("/measurements").text)
    assert re.search(r'class="silhouette-note"[^>]*title="old note"', page)                      # waist still comes from the 20th

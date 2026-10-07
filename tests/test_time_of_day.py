"""The fuller list of times of day: Fasting, Waking, AM, Pre-workout, Post-workout, PM, Before bed, Bedtime, Any."""

import html
import re
from datetime import date, timedelta

import pytest

from app.db import SessionLocal
from app.models import DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TimeOfDay

TODAY = date.today()


def test_the_nine_times_of_day_in_order_with_their_labels():
    assert [(m.value, m.label) for m in TimeOfDay] == [
        ("fasting", "Fasting"), ("waking", "Waking"), ("am", "AM"), ("pre_workout", "Pre-workout"), ("post_workout", "Post-workout"),
        ("pm", "PM"), ("before_bed", "Before bed"), ("bedtime", "Bedtime"), ("any", "Any time")]


@pytest.fixture
def protocol_id(me):
    """One daily dose in each of the given slots, named after the slot."""
    made = {}

    def build(*slots):
        with SessionLocal() as s:
            p = Protocol(name="Slots Test", start_date=TODAY - timedelta(days=2), owner_id=me)
            s.add(p)
            s.flush()
            for n, slot in enumerate(slots):
                pep = Peptide(name=f"Slotpep {slot.value}", source=PeptideSource.CUSTOM)
                s.add(pep)
                s.flush()
                s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=1, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, time_of_day=slot, route=Route.SUBQ, position=n))
                made.setdefault("peptides", []).append(pep.id)
            s.commit()
            made["protocol"] = p.id
        return made["protocol"]

    yield build
    with SessionLocal() as s:
        if "protocol" in made:
            s.query(Protocol).filter_by(id=made["protocol"]).delete()
        s.query(Peptide).filter(Peptide.id.in_(made.get("peptides", [0]))).delete()
        s.commit()


def test_the_week_view_shows_only_the_slots_in_use_in_order(client, db, protocol_id):
    protocol_id(TimeOfDay.POST_WORKOUT, TimeOfDay.WAKING, TimeOfDay.ANY)
    page = client.get("/calendar", params={"view": "week", "date": TODAY.isoformat()}).text
    rows = re.findall(r'data-slot="([a-z_]+)"', page)
    assert rows == ["waking", "post_workout", "any"]                     # the unused AM, PM and the rest take no room


def test_the_day_view_lists_the_slots_in_order(client, db, protocol_id):
    protocol_id(TimeOfDay.BEFORE_BED, TimeOfDay.FASTING, TimeOfDay.PM)
    page = html.unescape(client.get("/calendar", params={"view": "day", "date": TODAY.isoformat()}).text)
    order = [page.index(f"Slotpep {v}") for v in ("fasting", "pm", "before_bed")]
    assert order == sorted(order)


def test_the_today_page_and_dashboard_list_doses_in_time_of_day_order(client, db, protocol_id):
    protocol_id(TimeOfDay.BEDTIME, TimeOfDay.WAKING, TimeOfDay.PRE_WORKOUT)
    for url, marker in (("/today", "Slotpep"), ("/dashboard", "Slotpep")):
        page = html.unescape(client.get(url).text)
        order = [page.index(f"Slotpep {v}") for v in ("waking", "pre_workout", "bedtime")]
        assert order == sorted(order), url


def test_the_builder_offers_every_time_of_day(client, db):
    page = html.unescape(client.get("/protocols/new").text)
    for label in ("Fasting", "Waking", "Pre-workout", "Post-workout", "Before bed"):
        assert label in page, label


def test_an_item_can_be_saved_with_a_new_time_of_day(client, db, me):
    with SessionLocal() as s:
        pep = Peptide(name="Slotpep save", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.commit()
        pep_id = pep.id
    try:
        r = client.post("/protocols", data={"name": "Save Slot Test", "start_date": TODAY.isoformat(), "goal": ["muscle-recovery"], "items-0-peptide_id": str(pep_id), "items-0-dose": "1",
                                            "items-0-dose_unit": "mg", "items-0-frequency": "daily", "items-0-time_of_day": "pre_workout"}, follow_redirects=False)
        assert r.status_code == 303, r.text[:300]
        with SessionLocal() as s:
            item = s.query(ProtocolItem).filter_by(peptide_id=pep_id).one()
            assert item.time_of_day is TimeOfDay.PRE_WORKOUT
    finally:
        with SessionLocal() as s:
            s.query(Protocol).filter_by(name="Save Slot Test").delete()
            s.query(Peptide).filter_by(id=pep_id).delete()
            s.commit()

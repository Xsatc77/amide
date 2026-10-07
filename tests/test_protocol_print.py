"""A printable one-page view of a protocol."""

import html
from datetime import date, timedelta

import pytest

from app.db import SessionLocal
from app.models import DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, ProtocolItemCycleOff, Route, TimeOfDay, TitrationStep
from photo_helpers import other_client


@pytest.fixture
def protocol_id(me):
    with SessionLocal() as s:
        pep = Peptide(name="Printable Test Peptide", source=PeptideSource.CUSTOM)
        other = Peptide(name="Printable Second", source=PeptideSource.CUSTOM)
        s.add_all([pep, other])
        s.flush()
        p = Protocol(name="Print Me Protocol", start_date=date.today() - timedelta(days=3), end_date=date.today() + timedelta(days=60), owner_id=me, titration_enabled=True,
                     notes="Take with food")
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=250, dose_unit=DoseUnit.MCG, frequency=Frequency.WEEKDAYS, weekdays="MWF", time_of_day=TimeOfDay.AM,
                           route=Route.SUBQ, notes="Rotate sites", position=0,
                           steps=[TitrationStep(start_week=1, end_week=2, dose=250), TitrationStep(start_week=3, end_week=None, dose=500)],
                           cycle_offs=[ProtocolItemCycleOff(start_week=5, end_week=6)]))
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=other.id, dose=2, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.IM, position=1))
        s.commit()
        pid, ids = p.id, [pep.id, other.id]
    yield pid
    with SessionLocal() as s:
        s.query(Protocol).filter_by(id=pid).delete()
        s.query(Peptide).filter(Peptide.id.in_(ids)).delete()
        s.commit()


def page(client, pid):
    return html.unescape(client.get(f"/protocols/{pid}/print").text)


def test_the_print_page_lists_every_item_with_its_schedule_steps_and_notes(client, db, protocol_id):
    text = page(client, protocol_id)
    for expected in ("Print Me Protocol", "Printable Test Peptide", "250 mcg", "Mon, Wed, Fri", "AM", "SubQ", "Rotate sites", "Printable Second", "2 mg", "IM",
                     "Take with food", "Weeks 1-2", "Week 3 onward", "500 mcg", "Off in weeks 5-6"):
        assert expected in text, expected


def test_the_print_page_has_dates_and_a_print_button_and_no_navigation_when_printed(client, db, protocol_id):
    text = page(client, protocol_id)
    assert (date.today() + timedelta(days=60)).strftime("%m/%d/%Y") in text
    assert "data-print" in text and "Research use only" in text.replace("Research Use Only", "Research use only")


def test_someone_elses_protocol_is_not_found(client, db, protocol_id):
    with other_client("printother") as member:
        assert member.get(f"/protocols/{protocol_id}/print").status_code == 404
    assert client.get("/protocols/99999999/print").status_code == 404


def test_the_protocol_list_links_to_the_print_page(client, db, protocol_id):
    assert f'href="/protocols/{protocol_id}/print"' in client.get("/protocols").text

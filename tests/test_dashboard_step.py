"""The Dashboard's Today's Schedule says which titration step a dose is on."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TitrationStep, User


@pytest.fixture
def protocol_ids(me):
    ids = []
    with SessionLocal() as s:
        pep = Peptide(name="Stepwise Test", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        titrated = Protocol(name="Titrated Step Test", start_date=date.today() - timedelta(days=15), owner_id=me, titration_enabled=True)
        plain = Protocol(name="Plain Step Test", start_date=date.today() - timedelta(days=15), owner_id=me)
        s.add_all([titrated, plain])
        s.flush()
        s.add(ProtocolItem(protocol_id=titrated.id, peptide_id=pep.id, dose=1.0, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ,
                           steps=[TitrationStep(start_week=1, end_week=2, dose=1.0), TitrationStep(start_week=3, end_week=None, dose=2.5)]))
        s.commit()
        ids = [titrated.id, plain.id, pep.id]
        plain_id = plain.id
    yield ids
    with SessionLocal() as s:
        s.query(Protocol).filter(Protocol.id.in_(ids[:2])).delete()
        s.query(Peptide).filter(Peptide.id == ids[2]).delete()
        s.commit()


def schedule_html(client):
    page = client.get("/dashboard").text
    return page.split('id="schedule-heading"')[1].split("</section>")[0]


def test_a_titrated_dose_shows_its_step_and_the_dose_of_that_step(client, db, protocol_ids):
    box = schedule_html(client)
    assert "Stepwise Test" in box and "2.5" in box and "Step 2 of 2" in box


def test_a_dose_with_one_dose_all_the_way_shows_no_step(client, db, me, protocol_ids):
    with SessionLocal() as s:
        titrated = s.get(Protocol, protocol_ids[0])
        titrated.titration_enabled = False
        s.commit()
    import re
    assert not re.search(r"Step d", schedule_html(client))

"""The calendar's click-through dialog offers 'Pick site and log dose' for doses due today that are not logged yet."""

import html
import json
import re
from datetime import date, timedelta

import pytest

from app.db import SessionLocal
from app.models import DoseLog, DoseStatus, DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TimeOfDay

TODAY = date.today()


@pytest.fixture
def protocol(me):
    with SessionLocal() as s:
        pep = Peptide(name="Calendar Log Test", source=PeptideSource.CUSTOM)
        s.add(pep)
        s.flush()
        p = Protocol(name="Calendar Log Protocol", start_date=TODAY - timedelta(days=3), owner_id=me)
        s.add(p)
        s.flush()
        item = ProtocolItem(protocol_id=p.id, peptide_id=pep.id, dose=1, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ)
        s.add(item)
        s.commit()
        ids = (p.id, item.id, pep.id)
    yield ids
    with SessionLocal() as s:
        s.query(DoseLog).filter(DoseLog.protocol_id == ids[0]).delete()
        s.query(Protocol).filter_by(id=ids[0]).delete()
        s.query(Peptide).filter_by(id=ids[2]).delete()
        s.commit()


def occurrence(client, protocol_id, day):
    page = client.get("/calendar", params={"view": "day", "date": day.isoformat()}).text
    data = json.loads(re.search(r'<script type="application/json" id="cal-data">(.*?)</script>', page, re.S).group(1))
    return data["occurrences"][f"{protocol_id}|{day.isoformat()}"]


def test_a_dose_due_today_carries_what_the_dialog_needs_to_offer_logging(client, db, protocol):
    occ = occurrence(client, protocol[0], TODAY)
    assert occ["today"] is True and occ["items"][0]["item_id"] == protocol[1] and occ["items"][0]["logged"] is False


def test_a_logged_dose_is_marked_logged(client, db, me, protocol):
    with SessionLocal() as s:
        s.add(DoseLog(owner_id=me, protocol_id=protocol[0], protocol_item_id=protocol[1], peptide_id=protocol[2], peptide_name="Calendar Log Test", dose_value=1,
                      dose_unit=DoseUnit.MG, route="subq", scheduled_date=TODAY, scheduled_time_of_day=TimeOfDay.ANY,
                      status=DoseStatus.ON_TIME))
        s.commit()
    assert occurrence(client, protocol[0], TODAY)["items"][0]["logged"] is True


def test_an_earlier_day_is_not_today(client, db, protocol):
    assert occurrence(client, protocol[0], TODAY - timedelta(days=1))["today"] is False


def test_the_today_page_has_an_anchor_for_each_dose_row(client, db, protocol):
    assert f'id="dose-{protocol[1]}"' in client.get("/today").text

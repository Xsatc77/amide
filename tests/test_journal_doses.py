"""The Journal changes the record after the fact: doses a day had due and nothing logged show up there to mark taken, skip, undo or mark all
taken; quick notes can be edited or deleted. Invented protocol name only."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import DoseLog, DoseStatus, JournalEntry, JournalQuickNote, Protocol
from tests.test_calendar_page import make, peptide_id

JOURNAL = "/measurements?tab=journal"
NAME = "Jrnldose Plan"


@pytest.fixture
def two_a_day(client, db, me):
    start = (date.today() - timedelta(days=3)).isoformat()
    pid = make(client, name=NAME, start=start, items={
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am",
        "items-1-peptide_id": str(peptide_id("TB-500")), "items-1-dose": "2", "items-1-dose_unit": "mg",
        "items-1-frequency": "daily", "items-1-time_of_day": "pm",
    })
    yield pid
    db.rollback()
    db.query(DoseLog).filter(DoseLog.protocol_id == pid).delete()
    db.query(JournalQuickNote).delete()
    db.query(JournalEntry).filter(JournalEntry.owner_id == me).delete()
    db.query(Protocol).filter(Protocol.id == pid).delete()
    db.commit()


def yesterday():
    return date.today() - timedelta(days=1)


def offered(page, day):
    return page.count(f'name="scheduled_date" value="{day.isoformat()}"')


def test_a_day_with_doses_due_and_nothing_logged_is_offered_with_taken_skip_and_mark_all(client, two_a_day):
    page = client.get(JOURNAL).text
    assert offered(page, yesterday()) == 4                                  # two doses, a Taken and a Skip form each
    assert f'name="date" value="{yesterday().isoformat()}"' in page and "Mark all taken" in page and "Not logged" in page


def test_mark_all_taken_logs_the_day_and_undo_puts_one_back(client, db, two_a_day):
    r = client.post("/journal/doses/mark-all", data={"date": yesterday().isoformat(), "next": JOURNAL}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == JOURNAL
    logs = db.scalars(select(DoseLog).where(DoseLog.protocol_id == two_a_day, DoseLog.scheduled_date == yesterday())).all()
    assert len(logs) == 2 and {d.status for d in logs} == {DoseStatus.LATE}
    page = client.get(JOURNAL).text
    assert offered(page, yesterday()) == 0 and 'action="/today/undo"' in page
    again = client.post("/journal/doses/mark-all", data={"date": yesterday().isoformat()}, follow_redirects=False)       # nothing new: no duplicates
    assert again.status_code == 303 and len(db.scalars(select(DoseLog).where(DoseLog.scheduled_date == yesterday(), DoseLog.protocol_id == two_a_day)).all()) == 2
    r = client.post("/today/undo", data={"dose_log_id": str(logs[0].id), "next": JOURNAL}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == JOURNAL
    assert offered(client.get(JOURNAL).text, yesterday()) == 2                  # one dose is back to Not logged


def test_one_dose_can_be_marked_taken_or_skipped_from_the_journal(client, db, two_a_day):
    protocol = db.scalar(select(Protocol).where(Protocol.id == two_a_day))
    first, second = protocol.items[0], protocol.items[1]
    base = {"protocol_id": str(two_a_day), "scheduled_date": yesterday().isoformat(), "next": JOURNAL}
    assert client.post("/today/log", data={**base, "protocol_item_id": str(first.id)}, follow_redirects=False).headers["location"] == JOURNAL
    assert client.post("/today/skip", data={**base, "protocol_item_id": str(second.id)}, follow_redirects=False).headers["location"] == JOURNAL
    states = {d.protocol_item_id: d.status for d in db.scalars(select(DoseLog).where(DoseLog.protocol_id == two_a_day, DoseLog.scheduled_date == yesterday()))}
    assert states == {first.id: DoseStatus.LATE, second.id: DoseStatus.SKIPPED}


def test_next_only_goes_to_the_journal_and_never_off_site_and_a_future_day_is_refused(client, db, two_a_day):
    protocol = db.scalar(select(Protocol).where(Protocol.id == two_a_day))
    data = {"protocol_id": str(two_a_day), "protocol_item_id": str(protocol.items[0].id), "scheduled_date": yesterday().isoformat()}
    assert client.post("/today/skip", data={**data, "next": "//evil.example"}, follow_redirects=False).headers["location"] == "/today"
    future = (date.today() + timedelta(days=2)).isoformat()
    assert client.post("/journal/doses/mark-all", data={"date": future}).status_code == 422
    assert client.post("/journal/doses/mark-all", data={"date": "someday"}).status_code == 422


def test_a_quick_note_can_be_edited_and_deleted_and_others_are_refused(client, db, me, two_a_day):
    client.post("/journal/quick-note", data={"text": "first thought"})
    note_id = db.scalars(select(JournalQuickNote)).all()[-1].id
    assert f'action="/journal/quick-notes/{note_id}/edit"' in client.get(JOURNAL).text
    assert client.post(f"/journal/quick-notes/{note_id}/edit", data={"text": "better thought"}, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(JournalQuickNote, note_id).text == "better thought"
    client.post(f"/journal/quick-notes/{note_id}/edit", data={"text": "   "})                    # blank changes nothing
    db.expire_all()
    assert db.get(JournalQuickNote, note_id).text == "better thought"
    assert client.post(f"/journal/quick-notes/{note_id}/delete", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.scalar(select(JournalQuickNote).where(JournalQuickNote.id == note_id)) is None
    assert client.post("/journal/quick-notes/99999999/edit", data={"text": "x"}).status_code == 404
    assert client.post("/journal/quick-notes/99999999/delete").status_code == 404

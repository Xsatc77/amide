"""The Calendar marks a day that has a completed journal entry with an icon that opens the entry."""

import pytest

from app import clock
from app.db import SessionLocal
from app.models import JournalEntry, JournalQuickNote

TODAY = clock.today()


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(JournalEntry).delete()
        s.commit()


def entry(uid, **fields):
    with SessionLocal() as s:
        s.add(JournalEntry(owner_id=uid, entry_date=TODAY, **fields))
        s.commit()


def link(day=TODAY):
    return f'href="/measurements?tab=journal&amp;edit={day.isoformat()}"'


@pytest.mark.parametrize("view", ["month", "week", "day"])
def test_a_completed_entry_shows_an_icon_that_opens_it(client, me, view):
    entry(me, mood=4)
    page = client.get(f"/calendar?view={view}").text
    assert "📓" in page and f"edit={TODAY.isoformat()}" in page


def test_no_icon_without_an_entry_or_with_only_a_quick_note(client, me):
    assert "📓" not in client.get("/calendar?view=month").text
    entry(me)                                                  # a quick note makes an empty row: nothing rated, so not completed
    assert "📓" not in client.get("/calendar?view=month").text


def test_someone_elses_entry_is_not_marked(client, me):
    from photo_helpers import other_client
    with other_client("caljournalother") as member:
        member.post("/journal/entries", data={"entry_date": TODAY.isoformat(), "mood": "3"})
    assert "📓" not in client.get("/calendar?view=month").text


def test_an_entry_from_an_earlier_month_is_marked_too(client, me):
    from datetime import timedelta
    old = TODAY.replace(day=1) - timedelta(days=10)
    with SessionLocal() as s:
        s.add(JournalEntry(owner_id=me, entry_date=old, energy=3))
        s.commit()
    page = client.get(f"/calendar?view=month&date={old.isoformat()}").text
    assert "📓" in page and f"edit={old.isoformat()}" in page

"""Journal entries can be added for, or edited on, an earlier day."""

import html
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import JournalEntry

TODAY = date.today()
PAST = TODAY - timedelta(days=4)


@pytest.fixture(autouse=True)
def _clean(client, me):
    def wipe():
        with SessionLocal() as s:
            s.query(JournalEntry).filter(JournalEntry.owner_id == me).delete()
            s.commit()
    wipe()
    yield
    wipe()


def entries(me):
    with SessionLocal() as s:
        return {e.entry_date: (e.mood, e.energy, e.notes) for e in s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me))}


def test_an_entry_can_be_saved_for_an_earlier_day_without_touching_today(client, db, me):
    r = client.post("/journal/entries", data={"entry_date": PAST.isoformat(), "mood": "4", "energy": "3", "notes": "Looking back"}, follow_redirects=False)
    assert r.status_code == 303 and entries(me) == {PAST: (4, 3, "Looking back")}


def test_saving_the_same_day_again_edits_the_entry_in_place(client, db, me):
    client.post("/journal/entries", data={"entry_date": PAST.isoformat(), "mood": "2"})
    client.post("/journal/entries", data={"entry_date": PAST.isoformat(), "mood": "5", "notes": "Better"})
    assert entries(me) == {PAST: (5, None, "Better")}


def test_without_a_date_it_is_still_today(client, db, me):
    client.post("/journal/entries", data={"mood": "3"})
    assert list(entries(me)) == [TODAY]


@pytest.mark.parametrize("raw", [(TODAY + timedelta(days=1)).isoformat(), "1999-12-31", "not-a-date"])
def test_a_future_ancient_or_garbled_date_is_refused(client, db, me, raw):
    r = client.post("/journal/entries", data={"entry_date": raw, "mood": "3"})
    assert r.status_code == 422 and entries(me) == {}


def test_editing_a_past_entry_opens_the_dialog_filled_in_for_that_day(client, db, me):
    client.post("/journal/entries", data={"entry_date": PAST.isoformat(), "mood": "4", "notes": "Old note text"})
    page = html.unescape(client.get("/measurements", params={"tab": "journal", "edit": PAST.isoformat()}).text)
    dialog = page.split('id="journal-dialog"')[1].split("</dialog>")[0]
    assert dialog.startswith(" class=\"dialog\" open") or " open" in dialog[:60]
    assert f'name="entry_date" value="{PAST.isoformat()}"' in dialog and "Old note text" in dialog
    assert f"Entry for {PAST.strftime('%m/%d/%Y')}" in dialog


def test_the_default_dialog_is_today_and_closed(client, db, me):
    page = html.unescape(client.get("/measurements", params={"tab": "journal"}).text)
    dialog = page.split('id="journal-dialog"')[1].split("</dialog>")[0]
    assert f'name="entry_date" value="{TODAY.isoformat()}"' in dialog and "Today's entry" in dialog and not dialog.lstrip().startswith("open")


def test_each_own_entry_has_an_edit_link_and_there_is_a_day_picker(client, db, me):
    client.post("/journal/entries", data={"entry_date": PAST.isoformat(), "mood": "4"})
    page = html.unescape(client.get("/measurements", params={"tab": "journal"}).text)
    assert f"edit={PAST.isoformat()}" in page and 'name="edit"' in page


# ---------------------------------------------------------------- trend charts

def test_mood_energy_and_sleep_get_trend_charts_once_there_are_two_days(client, db, me):
    for n, (mood, energy, sleep) in enumerate([(2, 3, 4), (4, 3, 2), (5, 4, 3)]):
        client.post("/journal/entries", data={"entry_date": (TODAY - timedelta(days=n + 1)).isoformat(), "mood": str(mood), "energy": str(energy), "sleep_quality": str(sleep)})
    page = html.unescape(client.get("/measurements", params={"tab": "journal"}).text)
    section = page.split('id="journal-trends-heading"')[1].split("</section>")[0]
    for label in ("Mood", "Energy", "Sleep quality"):
        assert f"<h3>{label}</h3>" in section
    assert section.count("<polyline") == 3


def test_one_day_is_not_a_trend_and_a_metric_never_rated_has_no_chart(client, db, me):
    client.post("/journal/entries", data={"entry_date": PAST.isoformat(), "mood": "3"})
    assert "journal-trends-heading" not in client.get("/measurements", params={"tab": "journal"}).text
    client.post("/journal/entries", data={"entry_date": (PAST + timedelta(days=1)).isoformat(), "mood": "4"})
    section = html.unescape(client.get("/measurements", params={"tab": "journal"}).text).split('id="journal-trends-heading"')[1].split("</section>")[0]
    assert "<h3>Mood</h3>" in section and "<h3>Energy</h3>" not in section and "<h3>Sleep quality</h3>" not in section

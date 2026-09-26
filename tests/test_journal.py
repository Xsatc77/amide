import html

from sqlalchemy import select

from app.db import SessionLocal
from app.models import JournalEntry, JournalSideEffect, Share, ShareCategory, User

from tests.test_measurements_page import _logged_in_client, _text


def _clear_journal_entries(*owner_ids: int) -> None:
    with SessionLocal() as s:
        for oid in owner_ids:
            s.query(JournalEntry).filter_by(owner_id=oid).delete()
        s.commit()


def _tester_id() -> int:
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


def test_new_entry_creates_todays_journal_entry(client, db):
    me = _tester_id()
    try:
        r = client.post("/journal/entries", data={
            "mood": "4", "energy": "3", "sleep_quality": "5",
            "side_effects": ["Headache", "Fatigue"], "side_effects_other": "mild nausea",
            "notes": "Felt good overall today.",
        }, follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [entry] = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            assert entry.mood == 4 and entry.energy == 3 and entry.sleep_quality == 5
            assert entry.side_effects_other == "mild nausea"
            assert {se.side_effect for se in entry.side_effects} == {
                JournalSideEffect.HEADACHE, JournalSideEffect.FATIGUE}
            assert entry.notes == "Felt good overall today."
    finally:
        _clear_journal_entries(me)


def test_resubmitting_today_edits_the_same_entry_not_a_duplicate(client, db):
    me = _tester_id()
    try:
        client.post("/journal/entries", data={"mood": "2", "notes": "rough start"})
        client.post("/journal/entries", data={"mood": "4", "notes": "better after lunch"})
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            rows = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            assert len(rows) == 1
            assert rows[0].mood == 4 and rows[0].notes == "better after lunch"
    finally:
        _clear_journal_entries(me)


def test_rejects_out_of_range_mood(client, db):
    me = _tester_id()
    try:
        r = client.post("/journal/entries", data={"mood": "6"})
        assert r.status_code == 422
    finally:
        _clear_journal_entries(me)


def test_all_fields_optional(client, db):
    me = _tester_id()
    try:
        r = client.post("/journal/entries", data={}, follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [entry] = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            assert entry.mood is None and entry.notes is None
    finally:
        _clear_journal_entries(me)


def test_journal_tab_shows_entries_and_respects_personal_data_sharing(client, db):
    me = _tester_id()
    other = _logged_in_client("JournalSharePartner")
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "journalsharepartner"))
    try:
        r = other.post("/journal/entries", data={"mood": "3", "notes": "partner's private note"},
                       follow_redirects=False)
        assert r.status_code == 303

        # No Share yet -- the signed-in test user must not see the other user's journal entry.
        t_before = _text(client.get("/measurements?tab=journal"))
        assert "partner's private note" not in t_before

        with SessionLocal() as s:
            s.add(Share(owner_id=other_id, grantee_id=me, category=ShareCategory.PERSONAL_DATA))
            s.commit()

        # Now visible, tagged with the owner's name.
        t_after = _text(client.get("/measurements?tab=journal"))
        assert "partner's private note" in t_after
        assert "JournalSharePartner" in t_after
    finally:
        with SessionLocal() as s:
            s.query(Share).filter_by(owner_id=other_id, grantee_id=me,
                                     category=ShareCategory.PERSONAL_DATA).delete()
            s.commit()
        _clear_journal_entries(me, other_id)


def test_journal_tab_loads_with_empty_state(client, db):
    r = client.get("/measurements?tab=journal")
    assert r.status_code == 200
    assert "No journal entries yet" in r.text or "New Entry" in r.text


def test_quick_note_creates_todays_entry_if_none_exists(client, db):
    me = _tester_id()
    try:
        r = client.post("/journal/quick-note", data={"text": "felt a bit foggy after lunch"},
                        follow_redirects=False)
        assert r.status_code == 303
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [entry] = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            assert entry.mood is None  # auto-created, ratings left blank
            [note] = entry.quick_notes
            assert note.text == "felt a bit foggy after lunch"
    finally:
        _clear_journal_entries(me)


def test_quick_note_appends_to_todays_existing_entry(client, db):
    me = _tester_id()
    try:
        client.post("/journal/entries", data={"mood": "4", "notes": "good day"})
        client.post("/journal/quick-note", data={"text": "quick note one"})
        client.post("/journal/quick-note", data={"text": "quick note two"})
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [entry] = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            assert entry.mood == 4  # unaffected
            assert [n.text for n in entry.quick_notes] == ["quick note one", "quick note two"]
    finally:
        _clear_journal_entries(me)


def test_empty_quick_note_is_a_noop(client, db):
    me = _tester_id()
    try:
        r = client.post("/journal/quick-note", data={"text": "   "})
        assert r.status_code in (303, 422)  # implementer's choice of status, but no row must be created
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            assert s.scalar(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)) is None
    finally:
        _clear_journal_entries(me)


def test_quick_notes_never_overwrite_the_notes_field(client, db):
    me = _tester_id()
    try:
        client.post("/journal/entries", data={"notes": "the real daily entry"})
        client.post("/journal/quick-note", data={"text": "a quick aside"})
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [entry] = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            assert entry.notes == "the real daily entry"
            assert [n.text for n in entry.quick_notes] == ["a quick aside"]
    finally:
        _clear_journal_entries(me)


def test_journal_tab_shows_quick_notes_under_the_main_entry(client, db):
    me = _tester_id()
    try:
        client.post("/journal/entries", data={"notes": "main entry text"})
        client.post("/journal/quick-note", data={"text": "2pm quick note"})
        t = client.get("/measurements?tab=journal").text
        assert "main entry text" in t and "2pm quick note" in t
    finally:
        _clear_journal_entries(me)

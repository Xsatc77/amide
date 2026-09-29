import html
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    DoseLog, DoseStatus, DoseUnit, Frequency, JournalEntry, JournalQuickNote, JournalSideEffect, Peptide, Protocol,
    ProtocolItem, Route, Share, ShareCategory, TimeOfDay, User,
)

from tests.test_measurements_page import _logged_in_client, _text


def _clear_journal_entries(*owner_ids: int) -> None:
    with SessionLocal() as s:
        for oid in owner_ids:
            s.query(JournalEntry).filter_by(owner_id=oid).delete()
        s.commit()


def _tester_id() -> int:
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


def _seed_dose_log(owner_id: int, peptide_name: str, scheduled_date: date) -> None:
    """Mirrors tests/test_dashboard.py's test_adherence_pct_counts_unlogged_missed_doses_in_denominator
    DoseLog-seeding pattern: a minimal Protocol + ProtocolItem backing one DoseLog row, reusing the
    seeded "Retatrutide" Peptide (Peptide.name is unique) but with our own peptide_name so different
    doses are distinguishable in rendered output. Cleaned up by the autouse `clean` fixture, which
    deletes Protocol rows (cascading to ProtocolItem/DoseLog)."""
    with SessionLocal() as s:
        peptide = s.scalar(select(Peptide).where(Peptide.name == "Retatrutide"))
        if peptide is None:
            peptide = Peptide(name="Retatrutide")
            s.add(peptide)
            s.flush()
        protocol = Protocol(name=f"Journal Test Protocol {peptide_name}", start_date=scheduled_date,
                            owner_id=owner_id)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                             frequency=Frequency.EVERY_N_DAYS, every_n_days=5, route=Route.SUBQ)
        s.add(pitem)
        s.flush()
        s.add(DoseLog(owner_id=owner_id, protocol_id=protocol.id, protocol_item_id=pitem.id,
                      peptide_id=peptide.id, peptide_name=peptide_name, dose_value=pitem.dose,
                      dose_unit=pitem.dose_unit, route=pitem.route.value, scheduled_date=scheduled_date,
                      scheduled_time_of_day=TimeOfDay.ANY, status=DoseStatus.ON_TIME,
                      logged_at=datetime.now(timezone.utc)))
        s.commit()


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
        # It's a real re-rendered Journal tab page with a clear error next to the field, not a bare
        # JSON error body -- follows this app's errors-dict-and-`err()`-macro convention (see
        # app/routers/measurements.py's own entry form).
        assert "must be between 1 and 5" in r.text
        assert 'id="journal-dialog"' in r.text
    finally:
        _clear_journal_entries(me)


def test_invalid_mood_does_not_touch_todays_entry(client, db):
    """A validation failure must not create or corrupt today's row -- the user just sees the form
    again with the error, and can fix the field and resubmit."""
    me = _tester_id()
    try:
        client.post("/journal/entries", data={"mood": "4", "notes": "morning check-in"})
        r = client.post("/journal/entries", data={"mood": "4", "energy": "not-a-number", "notes": "morning check-in"})
        assert r.status_code == 422
        assert "whole number between 1 and 5" in r.text
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [entry] = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            assert entry.mood == 4 and entry.notes == "morning check-in"
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
        r = client.post("/journal/quick-note", data={"text": "   "}, follow_redirects=False)
        # A silent no-op redirect back to the Dashboard, same as a real quick note's success path --
        # never a raw JSON 422 error page (spec's "Quick-capture box" section).
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
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


def test_full_entry_form_shows_todays_doses(client, db):
    me = _tester_id()
    _seed_dose_log(me, "TodayFormPeptide", date.today())
    try:
        t = _text(client.get("/measurements?tab=journal"))
        assert "TodayFormPeptide" in t
    finally:
        _clear_journal_entries(me)


def _dialog_html(page_text: str) -> str:
    """Scopes an assertion to just the "New Entry" dialog's markup, not the entries table below it
    (both can legitimately contain the same values)."""
    return page_text.split('id="journal-dialog"', 1)[1].split("</dialog>", 1)[0]


def test_new_entry_dialog_prefills_todays_existing_entry(client, db):
    me = _tester_id()
    try:
        client.post("/journal/entries", data={
            "mood": "4", "energy": "3", "sleep_quality": "2",
            "side_effects": ["Headache", "Fatigue"], "side_effects_other": "mild nausea",
            "notes": "Felt good overall today.",
        })
        dialog = _dialog_html(client.get("/measurements?tab=journal").text)
        assert 'name="mood"' in dialog and 'value="4"' in dialog
        assert 'value="3"' in dialog  # energy
        assert 'value="2"' in dialog  # sleep_quality
        assert 'value="Headache" checked' in dialog
        assert 'value="Fatigue" checked' in dialog
        # An unselected side effect must not come back checked.
        assert 'value="Nausea" checked' not in dialog
        assert 'value="mild nausea"' in dialog
        assert "Felt good overall today." in dialog
    finally:
        _clear_journal_entries(me)


def test_resaving_prefilled_entry_does_not_wipe_it(client, db):
    """Reproduces the critical bug: opening "New Entry" again and saving without touching a field
    used to send that field as blank, silently wiping the morning's data. Now the dialog is
    pre-filled, so a browser resubmitting the same (unedited) values must round-trip them intact."""
    me = _tester_id()
    try:
        client.post("/journal/entries", data={
            "mood": "4", "energy": "3", "sleep_quality": "5",
            "side_effects": ["Headache", "Fatigue"], "notes": "Felt good overall today.",
        })
        # Simulate re-submitting the pre-filled form to just add a line to notes.
        client.post("/journal/entries", data={
            "mood": "4", "energy": "3", "sleep_quality": "5",
            "side_effects": ["Headache", "Fatigue"], "notes": "Felt good overall today. Update: still good.",
        })
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [entry] = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            assert entry.mood == 4 and entry.energy == 3 and entry.sleep_quality == 5
            assert {se.side_effect for se in entry.side_effects} == {
                JournalSideEffect.HEADACHE, JournalSideEffect.FATIGUE}
            assert entry.notes == "Felt good overall today. Update: still good."
    finally:
        _clear_journal_entries(me)


def test_new_entry_dialog_shows_todays_quick_notes_in_prefill_area(client, db):
    me = _tester_id()
    try:
        client.post("/journal/entries", data={"mood": "4", "notes": "main entry"})
        client.post("/journal/quick-note", data={"text": "a quick aside from this afternoon"})
        dialog = _dialog_html(client.get("/measurements?tab=journal").text)
        assert "a quick aside from this afternoon" in dialog
    finally:
        _clear_journal_entries(me)


def test_new_entry_dialog_blank_when_nothing_logged_today(client, db):
    me = _tester_id()
    try:
        dialog = _dialog_html(client.get("/measurements?tab=journal").text)
        assert 'name="mood" type="number" min="1" max="5" step="1" inputmode="numeric" value=""' in dialog
    finally:
        _clear_journal_entries(me)


def test_quick_note_time_displays_in_viewers_timezone_not_utc(client, db):
    me = _tester_id()
    try:
        client.post("/settings/timezone", data={"mode": "manual", "timezone": "America/Chicago"})
        client.post("/journal/entries", data={"notes": "tz test entry"})
        with SessionLocal() as s:
            me_user = s.scalar(select(User).where(User.username_key == "tester"))
            [entry] = s.scalars(select(JournalEntry).where(JournalEntry.owner_id == me_user.id)).all()
            # 18:30 UTC is 13:30 in America/Chicago (CDT, UTC-5) -- must not display as raw UTC.
            s.add(JournalQuickNote(entry_id=entry.id, text="afternoon note",
                                   noted_at=datetime(2026, 6, 1, 18, 30, tzinfo=timezone.utc)))
            s.commit()
        t = client.get("/measurements?tab=journal").text
        assert "13:30" in t
        assert "18:30" not in t
    finally:
        _clear_journal_entries(me)
        client.post("/settings/timezone", data={"mode": "system", "timezone": ""})


def test_workouts_for_returns_completed_workouts_on_that_date(db):
    from app.models import WorkoutExerciseLog, WorkoutLog, WorkoutPlan, WorkoutPlanDay, WorkoutSource
    from app.routers.journal import workouts_for

    me = _tester_id()
    plan = WorkoutPlan(owner_id=me, name="Test Plan", source=WorkoutSource.MANUAL, started_on=date.today())
    plan.days = [WorkoutPlanDay(position=0, label="Leg Day")]
    db.add(plan)
    db.flush()
    log = WorkoutLog(owner_id=me, plan_day_id=plan.days[0].id, log_date=date(2026, 1, 8))
    db.add(log)
    db.commit()

    result = workouts_for(db, me, date(2026, 1, 8))
    assert len(result) == 1
    assert result[0]["label"] == "Leg Day"

    assert workouts_for(db, me, date(2026, 1, 9)) == []  # wrong date


def test_past_entry_shows_that_days_doses_not_todays(client, db):
    me = _tester_id()
    past_date = date(2026, 9, 20)
    _seed_dose_log(me, "PastDayPeptide", past_date)
    _seed_dose_log(me, "TodayOnlyPeptide", date.today())
    with SessionLocal() as s:
        s.add(JournalEntry(owner_id=me, entry_date=past_date, notes="notes from a past day"))
        s.commit()
    try:
        t = _text(client.get("/measurements?tab=journal"))
        # Scope the assertion to the entries table (the entry-form dialog above it separately shows
        # "Today's doses", where TodayOnlyPeptide legitimately appears -- that's not what's under test).
        entries_section = t.split('id="journal-entries-heading"', 1)[1]
        assert "PastDayPeptide" in entries_section
        assert "TodayOnlyPeptide" not in entries_section
    finally:
        _clear_journal_entries(me)

# Journal Design (Roadmap Phase 6, part 2 of 3)

**Goal:** the second of three Phase 6 sub-projects (Weight & Measurements shipped; Journal here;
Labs next). Daily journaling: mood/energy/sleep ratings, a side-effect checklist, and free-form
notes, plus a Dashboard quick-capture box for jotting timestamped notes through the day that fold
into that day's entry. Read-only visibility into that day's logged doses, so a user can see what
they took alongside how they felt — no charts yet, no back-dating, one entry per day.

**Deferred, explicitly out of scope for this spec:** mood/energy/sleep trend charts (a plain
reverse-chronological list for now; charts are a natural fast-follow once there's real data);
back-dating or editing a past day's entry (today's entry only, editable in place); a user-extensible
side-effect list (the checklist below is fixed for this build); a stored `JournalEntry`↔`DoseLog`
relationship (dose visibility is a query-time join by date, not a new foreign key).

## Data model

```python
class JournalSideEffect(str, enum.Enum):
    INJECTION_SITE_REACTION = "Injection site reaction"
    HEADACHE = "Headache"
    NAUSEA = "Nausea"
    FATIGUE = "Fatigue"
    BLOATING = "Bloating / water retention"
    GI_UPSET = "GI upset"
    JOINT_PAIN = "Joint pain"
    INSOMNIA = "Insomnia"
    APPETITE_CHANGE = "Appetite change"
    FLUSHING_DIZZINESS = "Flushing / dizziness"


class JournalEntry(Base):
    """One row per user per calendar day. Auto-created by the first quick note of the day if no
    full entry exists yet (mood/energy/sleep_quality/side effects left null/empty); a full-form
    save on a day that already has a row edits it in place rather than creating a duplicate."""
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("owner_id", "entry_date", name="uq_journal_entry_owner_date"),
        CheckConstraint("mood IS NULL OR mood BETWEEN 1 AND 5", name="ck_journal_entry_mood_range"),
        CheckConstraint("energy IS NULL OR energy BETWEEN 1 AND 5", name="ck_journal_entry_energy_range"),
        CheckConstraint("sleep_quality IS NULL OR sleep_quality BETWEEN 1 AND 5", name="ck_journal_entry_sleep_range"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    mood: Mapped[int | None] = mapped_column(Integer)
    energy: Mapped[int | None] = mapped_column(Integer)
    sleep_quality: Mapped[int | None] = mapped_column(Integer)
    side_effects_other: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    side_effects: Mapped[list["JournalEntrySideEffect"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan")
    quick_notes: Mapped[list["JournalQuickNote"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="JournalQuickNote.noted_at")


class JournalEntrySideEffect(Base):
    """One flag row per checked side-effect tag -- a fixed enum, not a user-addable lookup table
    (unlike Vendor's payment-method-types), since this build's checklist is fixed."""
    __tablename__ = "journal_entry_side_effects"
    __table_args__ = (
        UniqueConstraint("entry_id", "side_effect", name="uq_journal_entry_side_effect"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    side_effect: Mapped[JournalSideEffect] = mapped_column(_enum_column(JournalSideEffect))

    entry: Mapped["JournalEntry"] = relationship(back_populates="side_effects")


class JournalQuickNote(Base):
    """A single dashboard quick-capture note, timestamped to when it was written. Displayed
    underneath the day's main `notes` field, never merged into it."""
    __tablename__ = "journal_quick_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    noted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    text: Mapped[str] = mapped_column(Text)

    entry: Mapped["JournalEntry"] = relationship(back_populates="quick_notes")
```

Sharing: `JournalEntry` (and its `side_effects`/`quick_notes` children) fold into the existing
`ShareCategory.PERSONAL_DATA` category — the same one Weight & Measurements and Protocols already
use. Visibility follows the same `_measurement_query`/`_shared_measurement_query`-style pair
established in `app/routers/measurements.py`, mirrored here as `_journal_query`/
`_shared_journal_query`.

## Full entry form

Today's date only — no back-dating in this build. The "New Entry" button on the Journal tab opens
a form (mirroring the Weight & Measurements entry-dialog pattern) with:
- Mood, Energy, Sleep quality: each a 1–5 rating (radio buttons or a simple `<select>` of 1-5 —
  implementer's choice, matching whatever compact rating-input convention this app already uses
  elsewhere, if any; otherwise plain radios).
- Side effects: checkboxes for the 10 `JournalSideEffect` values above, plus a free-text
  "Other" field (`side_effects_other`).
- Notes: a `<textarea>` that grows with content, no line cap, no character limit beyond whatever
  this app's existing free-text fields already use (if any).

If today already has a `JournalEntry` row (from an earlier save this session, or auto-created by a
quick note), the form opens pre-filled with the existing values and saving updates that same row in
place — never a duplicate, never an error. All fields are optional except `entry_date` itself
(defaults to today, not user-editable in this build).

## Dose linking (query-time, no stored relationship)

The full entry form and the Journal tab's list view both show a read-only "That day's doses"
section, populated by querying `DoseLog` for rows matching that same date for the signed-in user
(and anyone sharing with them, consistent with how doses are already scoped elsewhere in this app).
On the full-entry form (today only) this is always today's doses; in the list view it's scoped to
whichever past entry's own `entry_date` is being displayed, never defaulting back to today's doses
for an older entry. No new foreign key, no join table — a plain date-matched query, so it can never
go stale relative to the actual dosing history.

## Quick-capture box (Dashboard)

A small text input plus an "Add" button on the Dashboard (a new small card/section, alongside the
existing placeholder-card precedent this app already uses for reserved-but-unbuilt widgets — this
one is real, not a placeholder). Submitting a non-empty note creates one `JournalQuickNote` row
timestamped to the moment it's written (`noted_at = utcnow()`), auto-creating today's
`JournalEntry` first if one doesn't exist yet (mood/energy/sleep/side effects left null/empty). An
empty/whitespace-only submission is a no-op — no blank timestamped row.

When the day's full entry is later viewed (on the Journal tab, or re-opened via "New Entry" to
edit), that day's accumulated quick notes render underneath the main `notes` text as a simple
timestamped list (e.g. "2:14 PM — felt a bit foggy after lunch"), in `noted_at` order. They remain
their own rows in the database — never merged into or overwriting the `notes` field itself.

## Journal tab page

The Journal tab on the Weight & Measurements page (currently a "Coming in a future update"
placeholder card) becomes a real section: a reverse-chronological list of past `JournalEntry` rows,
each showing its date, mood/energy/sleep ratings, side-effect tags (+ "Other" text if present),
notes, that day's quick notes (if any), and that day's doses (per the query-time join above) — plus
the "New Entry" button, which always targets today regardless of which past entry is currently in
view. Entries shared via `PERSONAL_DATA` appear in the same list, each tagged with the sharing
user's name, exactly matching the display convention already established on the Weight &
Measurements page — never blended into the signed-in user's own identity.

## Validation

- `mood`/`energy`/`sleep_quality`: each constrained to 1–5 at the database level (`CheckConstraint`)
  and rejected with a clear 422 error at the form level if out of range.
- A quick-capture submission with empty/whitespace-only text is silently ignored (no row created,
  no error) rather than creating a blank timestamped note.
- Side-effect checkboxes and the "Other" text field are both optional; no side effects at all is a
  valid, common case (a day with nothing noteworthy).

## Review focus

1. A same-day full-entry resubmission must edit the existing row in place, never create a second
   `JournalEntry` for the same `owner_id`/`entry_date` (the `UniqueConstraint` is the backstop, but
   the form/route logic must find-and-update, not blindly insert and hit a 500 on the constraint).
2. The first quick note of a day with no existing entry must auto-create that day's `JournalEntry`
   correctly (today's date, all rating/side-effect fields left null/empty) — never crash, never
   silently drop the note.
3. Quick notes must never be concatenated into or overwrite the `notes` field — they stay their own
   rows and are only ever displayed alongside it.
4. An empty or whitespace-only quick-capture submission must be a no-op, not a blank timestamped
   entry cluttering the day's quick-notes list.
5. The "Today's doses" / "that day's doses" query-time join must scope correctly per viewed entry's
   own `entry_date` (not always "today's" doses) when browsing past entries in the list view, and
   must respect the same dose-visibility scoping already used elsewhere in this app (not a global
   view across users).

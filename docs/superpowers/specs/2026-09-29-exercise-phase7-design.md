# Phase 7 — Exercise: Workout Plans + Fitness Test — Design

**Spec status:** approved in chat; pending written-spec review.

## Problem

Phase 7 (Exercise) is currently three unspec'd roadmap bullets: "Build plans
(days → exercises → sets/reps/weight)", "Log workouts, track progression and
personal records", "Show workouts alongside dosing and body metrics." This
spec turns that into two concrete, related features:

1. **Workout Plans** — import a premade workout PDF (from sites like Muscle
   & Strength) or build one manually, assign real days of the week to it,
   and log completion (per-exercise done/weight/reps) day by day.
2. **Fitness Test** — a small, fixed, no-equipment bodyweight assessment
   (push-ups, sit-ups, squats, plank) the user can retake anytime, with a
   per-exercise trend chart, independent of any imported plan.

Both were approved together as one combined spec, despite being separable
in principle — see Global Constraints for how they share infrastructure.

## Real-world input variance (confirmed from 3 real Muscle & Strength PDFs)

The three sample PDFs the owner provided (`8weekbeginnerfatlossworkout.pdf`,
`8weekfatlossprogramforadults402b.pdf`, `4dayathomeglutebuildingworkout.pdf`)
already disagree on:

- Day/workout header style: `"Workout #1 - Upper Body Workout A"`,
  `"Day 1: Upper Body"`, and plain `"Workout 1"` (no label at all).
- Table columns: two have a `Rest` column, one doesn't.
- Rep format: fixed (`10`), a range (`10 - 12`), or a range with a
  per-side qualifier (`10 - 12 Each Leg`, `10, Each Side`).
- Footnotes attached to specific exercises (`Close Grip Push Up 2-3 10-12*`
  with an `Author's Note: *Go to failure...` at the bottom).
- A "Workout Summary" info box (goal, level, days/week, duration, equipment,
  author) that sits visually at the top of the page in all three — but
  **extracts as text *after* every table**, not before. `pypdf`'s text
  extraction follows the PDF's internal content-stream order, not visual
  reading order, and the day/workout labels live inside this
  summary block. Confirmed directly (not assumed) against all 3 real
  files: every exercise table appears before any day/workout label in the
  extracted text, in every one of them — see "PDF import & parsing" below
  for what this means for how the parser must associate a table with its
  label.

This confirms parsing must be **best-effort and positional**, never
pattern-matching on exact header text — the same lesson already learned
building the peptide sheet parser earlier in this project.

## Global Constraints

- No stored relationship from `JournalEntry` to workout data — Journal
  reads completed workouts **query-time**, the same pattern
  `app/routers/journal.py`'s existing `doses_for(session, owner_id, date)`
  already uses for dose data. A new `workouts_for(session, owner_id, date)`
  follows the identical shape.
- Only one `WorkoutPlan` may be Active at a time (a deliberate divergence
  from Protocols, which does allow multiple simultaneous active
  protocols — a workout program is followed one at a time, unlike peptide
  protocols). Activating a plan automatically ends whichever plan was
  previously Active.
- `WorkoutPlan`'s Active/Ended state is **derived from an `ended_on`
  date column**, not a separate status enum — mirrors `Protocol`'s own
  `ended_on`/`paused` pattern in `app/models.py:697-712` exactly.
- The PDF parser never raises on a format it doesn't fully recognize — it
  extracts what it can positionally and leaves the rest blank, exactly
  like `app/library/sheet_parser.py`'s established philosophy. A completely
  unparseable PDF (zero recognized structure) still creates a plan with
  zero days pre-filled, opening straight into the manual-edit screen — it
  is never rejected outright.
- No calorie-burn data is stored or displayed in this build (see Out of
  Scope).
- Chart rendering reuses the existing hand-drawn SVG trend-chart pattern
  from Weight & Measurements / Labs (same 7-day-to-lifetime range
  selector convention) — no new charting library.
- New dependency: `pypdf` (text extraction only) added to
  `requirements.txt`. The three sample PDFs already extract as clean,
  line-ordered text (confirmed directly), so no table-aware library is
  needed.

## Data model

```python
class WorkoutSource(str, enum.Enum):
    PDF = "pdf"
    MANUAL = "manual"


class WeightUnit(LabeledEnum):
    LB = ("lb", "lb")
    KG = ("kg", "kg")


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[WorkoutSource] = mapped_column(_enum_column(WorkoutSource))
    source_pdf_filename: Mapped[str | None] = mapped_column(String(100))  # uploads.py convention
    started_on: Mapped[date] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date)  # Active while None, mirrors Protocol
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    days: Mapped[list["WorkoutPlanDay"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", passive_deletes=True,
        order_by="WorkoutPlanDay.position")


class WorkoutPlanDay(Base):
    __tablename__ = "workout_plan_days"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("workout_plans.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # Day 1, Day 2... within the plan
    label: Mapped[str] = mapped_column(String(200))  # "Upper Body Workout A", or "Day 1" if unparsed
    weekdays: Mapped[str | None] = mapped_column(String(7))  # reuses ProtocolItem's own convention exactly:
    # a subset of WEEKDAY_LETTERS ("MTWRFSU", Monday-first), set at scheduling time, None until assigned

    plan: Mapped["WorkoutPlan"] = relationship(back_populates="days")
    exercises: Mapped[list["WorkoutExercise"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="WorkoutExercise.position")


class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_id: Mapped[int] = mapped_column(ForeignKey("workout_plan_days.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    sets_text: Mapped[str | None] = mapped_column(String(50))     # "2", "2 - 3"
    reps_text: Mapped[str | None] = mapped_column(String(50))     # "10", "10 - 12 Each Leg"
    rest_text: Mapped[str | None] = mapped_column(String(50))     # "45 Sec", None if the PDF has no Rest column


class WorkoutLog(Base):
    """One completed instance of a WorkoutPlanDay, on a specific calendar date."""
    __tablename__ = "workout_logs"
    __table_args__ = (UniqueConstraint("plan_day_id", "log_date", name="uq_workout_log_day_date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_day_id: Mapped[int] = mapped_column(ForeignKey("workout_plan_days.id", ondelete="CASCADE"))
    log_date: Mapped[date] = mapped_column(Date, index=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    plan_day: Mapped["WorkoutPlanDay"] = relationship()
    exercise_logs: Mapped[list["WorkoutExerciseLog"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True)


class WorkoutExerciseLog(Base):
    __tablename__ = "workout_exercise_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_log_id: Mapped[int] = mapped_column(ForeignKey("workout_logs.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("workout_exercises.id", ondelete="CASCADE"))
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    weight_value: Mapped[float | None] = mapped_column(Float)
    weight_unit: Mapped[WeightUnit | None] = mapped_column(_enum_column(WeightUnit))
    reps_value: Mapped[int | None] = mapped_column(Integer)


class FitnessTestExerciseName(str, enum.Enum):
    """Fixed, app-provided -- not a user-editable lookup table (mirrors JournalSideEffect's own
    fixed-enum precedent, not GoalPeptide's free-text pattern)."""
    MAX_PUSHUPS = "max_pushups"
    MAX_SITUPS = "max_situps"
    MAX_BODYWEIGHT_SQUATS = "max_bodyweight_squats"
    PLANK_HOLD_SECONDS = "plank_hold_seconds"


class FitnessTestResult(Base):
    __tablename__ = "fitness_test_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exercise: Mapped[FitnessTestExerciseName] = mapped_column(_enum_column(FitnessTestExerciseName))
    value: Mapped[float] = mapped_column(Float)  # a count (reps) or seconds, per exercise
    tested_at: Mapped[date] = mapped_column(Date, index=True)
```

## PDF import & parsing

New module `app/workouts/pdf_parser.py`, structured like
`app/library/sheet_parser.py`:

- `extract_text(pdf_bytes: bytes) -> str` — thin wrapper over `pypdf`'s
  page text extraction, joined with newlines.
- `parse_workout_pdf(text: str) -> dict` — pure function, text in,
  structured dict out (`{"name": str, "days": [{"label": str, "exercises":
  [{"name", "sets_text", "reps_text", "rest_text"}, ...]}, ...]}`).
  - **Tables and day/workout labels are found independently and zipped by
    position — never by textual adjacency.** Confirmed directly against
    all 3 of the owner's real sample PDFs: every table ("Exercise Sets
    Reps[ Rest]" header, then its rows) appears *before* any of the
    day/workout labels in the extracted text. The labels live in a
    separate "Workout Summary" block later in the document, in the same
    order as their tables but never adjacent to them. (An earlier version
    of this spec assumed a label directly precedes its own table's rows —
    that assumption does not hold against any of the 3 real files and was
    corrected while writing the implementation plan.)
  - Table boundaries are found by scanning for the literal row
    `r"^Exercise\s+Sets\s+Reps(\s+Rest)?$"` — the one structural marker
    confirmed exactly consistent across every real file, unlike the
    labels themselves.
  - Day/workout labels are found by scanning for any of three known
    shapes, wherever they appear in the document: `r"^Workout #\d+ - (.+)$"`,
    `r"^Day \d+: (.+)$"`, `r"^Workout \d+$"` (label defaults to `"Day N"`
    for the last, bare case, or for any table with no corresponding label
    at its position).
  - Within a table's slice, rows are recovered by matching a single
    right-anchored pattern per line — real rows are single-space-separated
    with no reliable delimiter between the (possibly multi-word) exercise
    name and its numeric columns, so left-to-right whitespace splitting is
    ambiguous. Anchoring on the optional trailing Rest ("45 Sec"/"2 Min"),
    then Reps (a number or range, optionally with a trailing "*" footnote
    marker or an "Each Leg"/"Each Arm"/"Each Side"/bare "Each" qualifier),
    then Sets (a number or range), with everything left over as the name,
    correctly parses every row shape confirmed across all 3 real files.
  - No table found anywhere → returns `{"name": ..., "days": []}` (never
    raises) — the upload still succeeds, opening straight into an empty
    manual-edit screen.

## Shared create/review/edit screen

One screen serves three entry points: after a PDF parse (pre-filled,
blanks where parsing couldn't tell), starting a manual plan (opens empty),
and later editing an existing plan. A day is an editable card with an
"Add exercise" row; each exercise row has four plain text inputs (name,
sets, reps, rest) — deliberately free text, not structured numeric
fields, since real programs use "2-3" and "10-12 Each Leg" as their actual
values, not clean integers. Saving creates/updates the `WorkoutPlan` and
its `days`/`exercises` in one transaction (full replace of children, the
same "clear and re-add" idiom already used by `save_protocol` and
`load_sheets`).

## Scheduling & Today/Calendar integration

After save, a small follow-up step assigns each `WorkoutPlanDay` to one or
more real days of the week, reusing `ProtocolItem.weekdays`'s exact
existing convention (`WEEKDAY_LETTERS`/`WEEKDAY_NAMES` in `app/models.py`,
a `String(7)` subset like `"MWF"`) rather than inventing a parallel one —
independent of the not-yet-built Protocols day-of-week *frequency*
feature from the owner's wishlist, which is a different concept
(recurrence rules for dosing, not a plain weekday multi-select). `/today`'s
existing route gains a "Workouts due today" section listing any Active
plan's day(s) matching today's weekday with no `WorkoutLog` yet for
today's date, linking to the completion screen. Calendar gets a plain
marker on scheduled days — no new color language; that belongs to the
already-planned Beautification phase, not this build.

## Completion logging

Opening a due workout shows its exercises as a checklist: a Complete
checkbox per exercise, plus optional weight (value + lb/kg) and reps
inputs. Saving writes one `WorkoutLog` and its `WorkoutExerciseLog` rows
(one per exercise, `completed=False` for anything left unchecked — the
log always exists once opened and saved, partial completion is a normal,
representable state, not an error).

## Journal integration

`app/routers/journal.py` gains `workouts_for(session, owner_id, date) ->
list[dict]`, shaped exactly like the existing `doses_for` (same
ownership-check reasoning in its docstring, same "the entry's own owner,
not the viewer" note for shared entries). The Journal tab's per-day view
gains a new "Exercise" section rendering this, read-only, below the
existing dose section — same visual family, no stored link.

## Fitness Test

A fixed set of four exercises (see `FitnessTestExerciseName` above) shown
on a new Exercise-area page, independent of any `WorkoutPlan`. "Log a new
test" opens a small form (today's date, a value per exercise — attempted
exercises only, none are required). Each exercise gets its own hand-drawn
SVG trend chart (reusing the Weight & Measurements/Labs pattern exactly,
including the 7-day-to-lifetime range selector). A gentle, dismissible
suggestion to retest appears once **28 days** have passed since the last
test (matching the 8-week/4-week program-length convention already
present in the sample PDFs themselves) — never a hard gate; retesting on
any other schedule the user wants is always available via "Log a new
test."

## Out of scope

- **Calories burned per exercise/rep.** No reliable, well-sourced public
  dataset for this is known to exist — most published "calories per rep"
  figures are rough estimates rather than measured data, and quality
  varies enormously by source. Worth a dedicated feasibility spike later
  if the owner wants to pursue it; not part of this build.
- **Personal-record detection/highlighting.** `WorkoutExerciseLog` stores
  everything a PR-detection feature would need (weight, reps, date, per
  exercise), so this is a pure fast-follow on top of the same data —
  deliberately not built now, per the "log + plain history" scope
  decision.
- **Manual builder as a distinct UI from the review screen.** Explicitly
  the same component (see above) — not a second thing to design or build.

## Testing

- `pdf_parser.py` gets synthetic-text unit tests covering all three real
  header shapes, a Rest-column-present/absent variant, and a
  completely-unrecognized-format case (zero days, no raise) — following
  the sheet-parser test-suite's own synthetic-fixture convention (no
  verbatim Muscle & Strength copy in test fixtures).
- Route tests for: PDF upload → review screen pre-fill; manual creation;
  day-of-week scheduling; a due-today workout appearing on `/today`;
  completion logging (checked/unchecked mix, weight/reps persisted);
  activating a new plan auto-ending the previous Active one;
  `workouts_for` appearing in the Journal tab for the right date only.
- Fitness Test: logging a result, the retest-suggestion appearing at 28
  days and not before, and the trend chart rendering with 1 and with
  multiple data points.

## Review Focus

1. A PDF with zero recognized exercise tables must still produce a plan
   (empty, editable) rather than a rejected upload — the "never raise"
   constraint above, pinned by a dedicated parser test. (A table found with
   no corresponding day/workout label is a *different*, fully-parseable
   case — it still gets its exercises, just with a default "Day N" label.)
2. A workout day scheduled for a weekday that's already been logged today
   must not reappear in "Workouts due today" (no duplicate-logging prompt).
3. Weight/reps left blank on some exercises but not others in the same
   completion save must persist as a real partial-completion state, not
   silently drop the whole log or force all-or-nothing validation.
4. Activating a second Workout Plan while one is already Active must set
   the first plan's `ended_on` in the same transaction — never leave two
   plans simultaneously Active.
5. The Fitness Test's 28-day retest suggestion must use each exercise's
   *own* last-tested date independently (a user might log only Push-ups
   today and Sit-ups three weeks ago) — never a single shared
   "last tested" timestamp across all four exercises.

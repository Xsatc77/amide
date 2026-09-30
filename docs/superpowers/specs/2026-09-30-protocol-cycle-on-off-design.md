# Protocol Cycle On/Off — Design

## Context

The owner wants a "total quantity needed for the whole course" popup on the Protocols page
(a separate, follow-on feature — see the deferred spec this one unblocks). Building that
correctly requires knowing exactly which days a protocol item is actually due across its full
course. Today, `is_due()` (`app/calendar/schedule.py`) only understands a continuous schedule at
a fixed frequency, optionally with titration (dose changes over week-ranges via `TitrationStep`).
It has no concept of a peptide running for some weeks, stopping for a stretch, then resuming —
e.g. a 3-weeks-on/3-weeks-off cycle inside a longer protocol.

Without that, "how much do I need for this whole course" overcounts: a 12-week protocol with a
peptide that's only actually taken 6 of those weeks would otherwise be costed as if every week
were dosed.

This is scoped as its own sub-project, built and verified before the totals popup, because it
changes `is_due()` — the function Today, Calendar, and Dashboard adherence all already call. The
totals popup (next spec) then just reads the corrected schedule; it needs no scheduling logic of
its own.

## Goal

A protocol item can have one or more "cycle off" periods — week-ranges, relative to the
protocol's `start_date`, during which that item is never due, regardless of its frequency or any
titration step that would otherwise apply. Everywhere the app already asks "is this item due
today" (Today, Calendar month/week/day, Dashboard's schedule/adherence widgets) respects this
automatically, because they all route through `is_due()`.

## Data model

One new table, holding only the off-periods (on-periods are just "not covered by an off-period" —
no separate row type needed):

```python
class ProtocolItemCycleOff(Base):
    __tablename__ = "protocol_item_cycle_offs"
    __table_args__ = (
        CheckConstraint("start_week >= 1", name="ck_cycle_off_start_week"),
        CheckConstraint("end_week >= start_week", name="ck_cycle_off_end_week"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    protocol_item_id: Mapped[int] = mapped_column(ForeignKey("protocol_items.id", ondelete="CASCADE"))
    start_week: Mapped[int] = mapped_column(Integer)
    end_week: Mapped[int] = mapped_column(Integer)
```

`ProtocolItem` gains:
```python
cycle_offs: Mapped[list["ProtocolItemCycleOff"]] = relationship(
    cascade="all, delete-orphan", passive_deletes=True, order_by="ProtocolItemCycleOff.start_week")
```

Unlike `TitrationStep.end_week` (nullable — "onward"), `end_week` here is **never** null: a cycle
off is always a bounded duration (the builder asks "how many weeks off," not "off from week N
onward"). `start_week >= 1` and `end_week >= start_week` are the only constraints; overlap between
cycle-offs, and between a cycle-off and the item's own titration steps, is a form-validation
concern (see below), not a DB constraint — the same division of labor `TitrationStep` already
uses (DB only enforces what's true in isolation; cross-row rules live in `app/protocols/forms.py`).

Migration: new table only, no changes to existing columns.

## Scheduling engine

`app/protocols/status.py`'s `current_step(steps, week)` already does exactly the lookup needed —
"which of these start_week/end_week-bearing rows covers this week" — generically. Cycle-offs need
the same lookup, just asking "is *any* row covering this week" rather than "return the step that
does." Rename the shared logic to make that reuse explicit:

```python
def _covering(ranges, week: int | None):
    """The first of `ranges` (each with start_week/end_week, end_week possibly None meaning
    onward) whose range includes `week`, or None."""
    if week is None:
        return None
    for r in ranges:
        if r.start_week <= week and (r.end_week is None or week <= r.end_week):
            return r
    return None

def current_step(steps, week: int | None):
    return _covering(steps, week)

def is_cycled_off(cycle_offs, week: int | None) -> bool:
    return _covering(cycle_offs, week) is not None
```

`app/calendar/schedule.py`'s `is_due()` gains the check. Its signature changes from
`is_due(item, start, day)` to `is_due(item, start, day)` unchanged in shape, but now also
consults `item.cycle_offs`:

```python
def is_due(item, start: date, day: date) -> bool:
    days = (day - start).days
    if days < 0:
        return False
    if is_cycled_off(item.cycle_offs, current_week(start, day)):
        return False
    freq = item.frequency
    ...  # unchanged from here
```

`_due_item()` (also in `schedule.py`) is untouched — titration step lookup already only matters
for days `is_due()` already said yes to, and an off week now returns False before titration is
ever consulted.

`as_needed()` is untouched too: `Frequency.AS_NEEDED` items are never claimed by `is_due()`
regardless, and cycling an as-needed item on/off has no defined meaning (there's no schedule to
suppress) — the builder's "+ Cycle off" control is simply not offered for an item whose frequency
is "As needed."

### Every call site that loads `ProtocolItem.steps` needs the same eager-load for `cycle_offs`

`is_due()` is only as correct as the `cycle_offs` relationship being loaded. Grep-confirmed list of
`selectinload(ProtocolItem.steps)` call sites needing a parallel
`selectinload(ProtocolItem.cycle_offs)`:

- `app/routers/backup.py` (export/restore — the new table also needs its own backup/restore
  handling, matching however `TitrationStep` rows are currently exported/imported there)
- `app/routers/calendar.py`
- `app/routers/dashboard.py` (three occurrences)
- `app/routers/dosing.py`
- `app/routers/protocols.py` (two occurrences)

(`app/routers/calculator.py` loads `ProtocolItem.peptide` only, no `.steps` — it doesn't compute
due-dates, so it needs no change.)

## Builder UI

Mirrors the existing titration-step row UI (`protocol-builder.js`'s `stepRow()` /
`"+ Add step"`) as closely as possible, per the owner's own description:

- A **"+ Cycle off"** button per item (next to "+ Add step", not gated behind the protocol's
  "Titration" checkbox — cycling is a scheduling concept, independent of whether doses ramp within
  an on-stretch). Not shown for an item whose frequency is "As needed."
- Clicking it appends a cycle-off row with a single visible input: **"Weeks off"** (a plain number,
  matching the owner's ask — no start/end week shown to the user). `start_week` is computed the
  same way a new titration step's is today: one past wherever that item's schedule currently ends
  (the later of its last titration step's `end_week` and its last cycle-off's `end_week`, or week 1
  if neither exists yet) — computed client-side in JS, submitted as a hidden field alongside
  `weeks`, and re-validated server-side exactly like a titration step's `start_week` is today.
- While an item has an open (most-recently-added) cycle-off row, its button reads **"+ Cycle on"**
  instead of "+ Cycle off". Clicking it doesn't add a cycle-off row — it's the same "+ Add step"
  affordance, relabeled, since resuming dosing after an off period is just adding the next
  titration step (or, if titration is off, nothing further is needed — the item is simply due
  again once `current_week` passes the open cycle-off's `end_week`). The button reverts to
  "+ Cycle off" once that next step exists (mirroring "is there an unclosed gap right now").
- Each cycle-off row gets the same small "×" remove button titration step rows already have.

Form field naming (extends the scheme `app/protocols/forms.py` already documents):
```
items-{i}-cycle_offs-{j}-start_week, -weeks
```
`ParsedCycleOff(start_week: int, end_week: int)` (end_week computed as
`start_week + weeks - 1` during parsing, same file). `ParsedItem` gains
`cycle_offs: list[ParsedCycleOff] = field(default_factory=list)`.

### Validation (`app/protocols/forms.py`)

New `_parse_cycle_offs(item_state, prefix, errors)`, modeled on `_parse_steps`:
- `start_week >= 1` and `weeks >= 1` (a blank/invalid weeks value is a per-row error, same style as
  a titration step's blank dose today).
- No two cycle-off rows for the same item may overlap.
- A cycle-off may not overlap a titration step for the same item (each week is either "on" — with
  or without a titration step applying — or "off," never both; the builder's own sequencing
  already prevents this by construction as long as `start_week` stays auto-computed, but the
  server still re-validates it independently, the same trust posture every other builder field
  already has).

### Save (`app/routers/protocols.py`'s `save_protocol`)

`ProtocolItem(...)` construction gains
`cycle_offs=[ProtocolItemCycleOff(start_week=c.start_week, end_week=c.end_week) for c in
it.cycle_offs]`, alongside the existing `steps=[...]` argument — same clear-and-rebuild handling,
no special DoseLog-repointing concerns beyond what already exists (cycle-offs carry no DoseLog
relationship of their own).

### Edit / Repeat

`state_from_protocol()` (wherever it already serializes `it.steps` into the JSON the builder page
embeds) gains the parallel `it.cycle_offs` serialization, so editing or repeating a protocol shows
its existing cycle-off rows exactly as it already does for titration steps.

## Review Focus

- An item cycled off for its *entire* remaining course (an off-period with no further on-stretch)
  must show 0 due days after it starts, not error or fall back to "always due."
- A day that's simultaneously outside the protocol's own `start_date`/`end_date` window **and**
  inside a cycle-off must not double-count or throw — `is_due()`'s existing `days < 0` early return
  still takes priority, cycle-off is just one more condition checked after it.
- Editing a protocol to shorten it (new `end_date` earlier than an existing cycle-off's computed
  calendar dates) must not orphan anything — cycle-offs are pure week-numbers relative to
  `start_date`, so they never need adjusting when `end_date` changes; only whether they still fall
  inside `[start_date, end_date]` changes, which `is_due()`'s own date-range check already handles.
- Titration enabled + a cycle-off covering part of a titration step's range: the step is simply
  never reached for those weeks (covered above), but must not cause the *next* step (after the
  cycle-off) to misfire early or late — `current_step()` is keyed purely on absolute week number,
  unaffected by cycle-offs.
- Backup/restore round-trip: a protocol item's cycle-offs must survive an export → restore cycle,
  same as its titration steps do today.

---

**For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
(recommended) or superpowers:executing-plans to implement the plan built from this spec.

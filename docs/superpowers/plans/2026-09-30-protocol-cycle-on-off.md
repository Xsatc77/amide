# Protocol Cycle On/Off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A protocol item can have one or more "cycle off" week-ranges during which it is never due, regardless of its frequency or any titration step — and every existing page that asks "is this item due today" (Today, Calendar, Dashboard) respects it automatically.

**Architecture:** One new table (`protocol_item_cycle_offs`, off-periods only — "on" is just "not covered by an off-period"). `app/calendar/schedule.py`'s `is_due()` gains one more check before its existing frequency logic. Every router that already eager-loads `ProtocolItem.steps` gains a parallel `ProtocolItem.cycle_offs` load. The builder's form-parsing module (`app/protocols/forms.py`) and its client-side renderer (`protocol-builder.js`) each gain a small, self-contained parallel path next to the existing titration-step code they already have, mirroring it field-for-field.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Jinja2, vanilla JS (no build step), pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-protocol-cycle-on-off-design.md`

## Global Constraints

- `ProtocolItemCycleOff.end_week` is **never** null (unlike `TitrationStep.end_week`, which allows "onward") — a cycle-off is always a bounded duration.
- `start_week >= 1` and `end_week >= start_week` are DB-level constraints; overlap between cycle-offs, and between a cycle-off and a titration step, is validated in `app/protocols/forms.py`, not the DB — same division of labor `TitrationStep` already uses.
- The "+ Cycle off" control is never shown for an item whose frequency is "As needed" (`Frequency.AS_NEEDED`) — there's no schedule to suppress.
- "+ Cycle off" is independent of the protocol-level "Titration" checkbox — always visible, always submitted, regardless of whether titration is on.
- Every `selectinload(ProtocolItem.steps)` call site must gain a parallel `selectinload(ProtocolItem.cycle_offs)` — confirmed list: `app/routers/backup.py`, `app/routers/calendar.py`, `app/routers/dashboard.py` (×3), `app/routers/dosing.py`, `app/routers/protocols.py` (×2).

## Review Focus

- An item cycled off for its entire remaining course (an off-period with no further on-stretch after it) must show zero due days from then on, not error or fall back to "always due."
- A day outside `[start_date, end_date]` **and** inside a cycle-off's week range must not double-count or throw — the existing `days < 0` early-return in `is_due()` still takes priority.
- Titration step immediately following a cycle-off must fire on schedule (keyed on absolute week number, unaffected by the gap before it) — not shifted or skipped.
- A cycle-off's `start_week` submitted by the client must be re-validated server-side (never trusted blindly) — same posture as titration step overlap validation today.
- Backup export → import must round-trip a protocol item's cycle-offs, the same way it already round-trips titration steps.

---

### Task 1: `ProtocolItemCycleOff` model + migration

**Files:**
- Modify: `app/models.py` (add `ProtocolItemCycleOff` class near `TitrationStep`; add `cycle_offs` relationship to `ProtocolItem`)
- Create: `migrations/versions/0027_protocol_item_cycle_offs.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `ProtocolItemCycleOff(id, protocol_item_id, start_week, end_week)`; `ProtocolItem.cycle_offs: list[ProtocolItemCycleOff]` (relationship, cascade delete, ordered by `start_week`).

- [ ] **Step 1: Add the model**

In `app/models.py`, immediately after the `TitrationStep` class (currently ends around line 771), add:

```python
class ProtocolItemCycleOff(Base):
    """A week-range, relative to the protocol's start_date, during which this item is never due --
    regardless of its frequency or any titration step that would otherwise apply. "On" is simply
    "not covered by any row here"; there is no separate "on" row type."""
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

Then add this relationship to the `ProtocolItem` class, immediately after its existing `steps` relationship:

```python
    cycle_offs: Mapped[list["ProtocolItemCycleOff"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="ProtocolItemCycleOff.start_week")
```

- [ ] **Step 2: Write the migration**

Create `migrations/versions/0027_protocol_item_cycle_offs.py`:

```python
"""protocol_item_cycle_offs: week-ranges during which a protocol item is never due

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0027'
down_revision: Union[str, None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'protocol_item_cycle_offs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('protocol_item_id', sa.Integer(), sa.ForeignKey('protocol_items.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('start_week', sa.Integer(), nullable=False),
        sa.Column('end_week', sa.Integer(), nullable=False),
        sa.CheckConstraint('start_week >= 1', name='ck_cycle_off_start_week'),
        sa.CheckConstraint('end_week >= start_week', name='ck_cycle_off_end_week'),
    )
    op.create_index('ix_protocol_item_cycle_offs_protocol_item_id', 'protocol_item_cycle_offs', ['protocol_item_id'])


def downgrade() -> None:
    op.drop_table('protocol_item_cycle_offs')
```

- [ ] **Step 3: Write the failing test**

Add to `tests/test_migrations.py` (check the top of that file for its existing helper pattern — it runs every migration against a throwaway SQLite file and asserts the final schema; follow the same style already there, e.g. a test that migrates to head and then inserts a row):

```python
def test_protocol_item_cycle_offs_table_exists_after_migration(tmp_path):
    engine = _migrated_engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(sa.text(
            "INSERT INTO protocol_item_cycle_offs (protocol_item_id, start_week, end_week) "
            "VALUES (1, 4, 6)"))
        conn.commit()
        row = conn.execute(sa.text("SELECT start_week, end_week FROM protocol_item_cycle_offs")).fetchone()
        assert row == (4, 6)
```

(If `tests/test_migrations.py` uses a different helper name or fixture than `_migrated_engine(tmp_path)`/`sa`, read the file first and match its actual existing pattern exactly — this step's code illustrates the assertion, not the exact scaffolding.)

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_migrations.py -k cycle_offs -v`
Expected: FAIL — table `protocol_item_cycle_offs` doesn't exist yet (migration not written) or import error (model not added).

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_migrations.py -v`
Expected: all pass, including the new test.

- [ ] **Step 5: Commit**

```bash
git add app/models.py migrations/versions/0027_protocol_item_cycle_offs.py tests/test_migrations.py
git commit -m "feat: add ProtocolItemCycleOff model and migration"
```

---

### Task 2: Scheduling engine — `is_due()` respects cycle-offs

**Files:**
- Modify: `app/protocols/status.py:34-46` (rename shared lookup, add `is_cycled_off`)
- Modify: `app/calendar/schedule.py:43-58` (`is_due()`)
- Test: `tests/test_calendar_schedule.py`, `tests/test_protocol_status.py` (check if this file exists; if not, add the new tests to `tests/test_protocols.py` instead — search first with `grep -rl "current_step\|current_week" tests/*.py` to find where `app.protocols.status` is already tested and add alongside)

**Interfaces:**
- Consumes: `ProtocolItemCycleOff.start_week`/`.end_week` from Task 1.
- Produces: `is_cycled_off(cycle_offs, week: int | None) -> bool` in `app/protocols/status.py`; `is_due(item, start, day)` in `app/calendar/schedule.py` now also checks `item.cycle_offs` (item must have this attribute — test doubles need updating, see Step 1).

- [ ] **Step 1: Update the test helper and write the failing test**

In `tests/test_calendar_schedule.py`, the `item()` helper (around line 8) builds a `SimpleNamespace` standing in for a real `ProtocolItem`. Add a `cycle_offs=()` parameter and pass it through:

```python
def item(name="BPC-157", freq=Frequency.DAILY, dose=250.0, unit=DoseUnit.MCG, every_n=None, weekdays=None,
         tod=TimeOfDay.AM, steps=(), cycle_offs=(), inventory=None, pid=1, item_id=None):
    if item_id is None:
        item_id = pid
    return NS(id=item_id, peptide=NS(id=pid, name=name), peptide_id=pid, dose=dose, dose_unit=unit, frequency=freq,
              every_n_days=every_n, weekdays=weekdays, time_of_day=tod, route=Route.SUBQ,
              inventory_item=NS(name=inventory) if inventory else None, steps=list(steps),
              cycle_offs=list(cycle_offs))
```

Then add this test, near `test_occurrence_groups_items_and_titration`:

```python
def test_cycle_off_suppresses_due_days_during_its_week_range():
    from types import SimpleNamespace as NS
    # Weeks 1-3 on (week 1 = days 0-6 from start), week 4-6 off, week 7 on again.
    offs = [NS(start_week=4, end_week=6)]
    p = proto(start=date(2026, 9, 1), items=[item(freq=Frequency.DAILY, cycle_offs=offs)])
    week1_day = date(2026, 9, 1)         # week 1
    week5_day = date(2026, 9, 1) + timedelta(days=28)  # week 5 (inside the off range)
    week7_day = date(2026, 9, 1) + timedelta(days=42)  # week 7 (resumed)
    assert due_dates(p, week1_day, week1_day) == [week1_day]
    assert due_dates(p, week5_day, week5_day) == []
    assert due_dates(p, week7_day, week7_day) == [week7_day]


def test_cycle_off_with_no_resume_stays_off_for_the_rest_of_the_course():
    from types import SimpleNamespace as NS
    offs = [NS(start_week=2, end_week=999)]  # off from week 2 onward, never resumes
    p = proto(start=date(2026, 9, 1), end=date(2026, 12, 1),
              items=[item(freq=Frequency.DAILY, cycle_offs=offs)])
    week1_day = date(2026, 9, 1)
    late_day = date(2026, 11, 1)
    assert due_dates(p, week1_day, week1_day) == [week1_day]
    assert due_dates(p, late_day, late_day) == []


def test_cycle_off_does_not_affect_the_before_start_date_early_return():
    """A day before the protocol's own start_date is never due regardless of cycle-offs -- the
    existing `days < 0` check must still short-circuit before cycle-off logic runs at all (not
    crash trying to compute a negative/undefined week number against the off-range)."""
    from types import SimpleNamespace as NS
    offs = [NS(start_week=1, end_week=5)]
    p = proto(start=date(2026, 9, 10), items=[item(freq=Frequency.DAILY, cycle_offs=offs)])
    before_start = date(2026, 9, 5)
    assert due_dates(p, before_start, before_start) == []


def test_titration_step_right_after_a_cycle_off_fires_on_its_own_schedule():
    """The step that resumes dosing after an off period must fire exactly on its own start_week --
    not shifted later because of the gap before it, and not skipped."""
    from types import SimpleNamespace as NS
    offs = [NS(start_week=2, end_week=3)]
    steps = [NS(start_week=1, end_week=1, dose=100.0), NS(start_week=4, end_week=None, dose=200.0)]
    p = proto(start=date(2026, 9, 1), titration=True,
              items=[item(freq=Frequency.DAILY, steps=steps, cycle_offs=offs)])
    week2_day = date(2026, 9, 1) + timedelta(days=7)   # inside the off period
    week4_day = date(2026, 9, 1) + timedelta(days=21)  # first day back on, step 2 should apply
    assert due_dates(p, week2_day, week2_day) == []
    [occ] = schedule.occurrences([p], week4_day, week4_day)
    assert (occ.items[0].dose, occ.items[0].step) == (200.0, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_calendar_schedule.py -k cycle_off -v`
Expected: FAIL — `is_due()` doesn't check `cycle_offs` yet, so both cycled-off days still show as due.

- [ ] **Step 3: Implement**

In `app/protocols/status.py`, replace `current_step` (lines 39-46) with:

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
    """The titration step covering `week` (an open end_week means "onward"), or None."""
    return _covering(steps, week)


def is_cycled_off(cycle_offs, week: int | None) -> bool:
    """True when `week` falls inside any of this item's cycle-off ranges."""
    return _covering(cycle_offs, week) is not None
```

In `app/calendar/schedule.py`, update the imports (line 10) and `is_due()` (lines 43-58):

```python
from app.protocols.status import current_step, current_week, is_cycled_off
```

```python
def is_due(item, start: date, day: date) -> bool:
    days = (day - start).days
    if days < 0:
        return False
    if is_cycled_off(item.cycle_offs, current_week(start, day)):
        return False
    freq = item.frequency
    if freq is Frequency.DAILY:
        return True
    if freq is Frequency.EOD:
        return days % 2 == 0
    if freq is Frequency.EVERY_N_DAYS:
        return bool(item.every_n_days) and days % item.every_n_days == 0
    if freq is Frequency.WEEKDAYS:
        return WEEKDAY_LETTERS[day.weekday()] in (item.weekdays or "")
    if freq is Frequency.WEEKLY:
        return days % 7 == 0
    return False  # as needed: never scheduled
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_calendar_schedule.py -v`
Expected: all pass, including both new tests.

- [ ] **Step 5: Commit**

```bash
git add app/protocols/status.py app/calendar/schedule.py tests/test_calendar_schedule.py
git commit -m "feat: is_due() respects an item's cycle-off week ranges"
```

---

### Task 3: Eager-load `cycle_offs` everywhere `steps` is loaded

**Files:**
- Modify: `app/routers/backup.py:96-97`
- Modify: `app/routers/calendar.py:131-133`
- Modify: `app/routers/dashboard.py:51-53`, `:81-83`
- Modify: `app/routers/dosing.py:93-95`
- Modify: `app/routers/protocols.py:38-39`, `:49-50`

**Interfaces:**
- Consumes: `ProtocolItem.cycle_offs` from Task 1.
- Produces: nothing new — this task only makes Task 2's `is_due()` change actually correct in every place it's called from a real database query (without this, `item.cycle_offs` would raise `DetachedInstanceError` or lazy-load per row outside an open session, depending on SQLAlchemy's session state at read time).

- [ ] **Step 1: Write the failing test**

This task has no new behavior of its own to unit-test in isolation — it's plumbing that Task 2's logic depends on when run through a real request. Prove it with an integration test that would fail (or warn) without the eager-load: add to `tests/test_calendar_page.py`:

```python
def test_month_view_respects_a_cycle_off(client, db):
    from app.models import Frequency, ProtocolItemCycleOff
    pid = make(client, name="Cycled", items={
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am",
    })
    with SessionLocal() as s:
        item = s.scalar(select(ProtocolItem).where(ProtocolItem.protocol_id == pid))
        s.add(ProtocolItemCycleOff(protocol_item_id=item.id, start_week=1, end_week=52))
        s.commit()
    t = page(client, view="month", date="2026-09-15")
    assert 'data-key="{}|2026-09-15"'.format(pid) not in t
```

(Match this test's exact imports/helpers — `make`, `peptide_id`, `page` — to however `tests/test_calendar_page.py` already defines them; read the top of that file first. `pid` above assumes `make()` returns the new protocol's id directly; if it doesn't, fetch it via `SessionLocal()` the same way this file's other tests do.)

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_calendar_page.py -k cycle_off -v`
Expected: FAIL — the mark for "2026-09-15" is still present, because `calendar.py`'s query doesn't load `cycle_offs` yet so `Occurrence`/`is_due()` never actually sees the off-period taking effect end-to-end (or the item was still queried as due some other way the test surfaces).

- [ ] **Step 3: Implement**

Add `selectinload(ProtocolItem.cycle_offs)` next to every existing `selectinload(ProtocolItem.steps)`:

`app/routers/backup.py:96-97`:
```python
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs))
```

`app/routers/calendar.py:131-133`:
```python
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
```

`app/routers/dashboard.py:51-53` and again at `:81-83` (both occurrences, same pattern):
```python
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
```

`app/routers/dosing.py:93-95`:
```python
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
```

`app/routers/protocols.py:38-39` and `:49-50` (both occurrences):
```python
        selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
        selectinload(Protocol.items).selectinload(ProtocolItem.steps),
        selectinload(Protocol.items).selectinload(ProtocolItem.cycle_offs),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_calendar_page.py -v`
Expected: all pass, including the new test.

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `py -m pytest -q`
Expected: all pass (this task only adds an eager-load option; it shouldn't change any existing query's results).

- [ ] **Step 6: Commit**

```bash
git add app/routers/backup.py app/routers/calendar.py app/routers/dashboard.py app/routers/dosing.py app/routers/protocols.py tests/test_calendar_page.py
git commit -m "feat: eager-load cycle_offs everywhere titration steps are loaded"
```

---

### Task 4: Form parsing — `ParsedCycleOff` and validation

**Files:**
- Modify: `app/protocols/forms.py`
- Test: `tests/test_protocol_forms.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure form-parsing module, no DB access).
- Produces: `ParsedCycleOff(start_week: int, end_week: int)`; `ParsedItem.cycle_offs: list[ParsedCycleOff]`; `state_from_form()`/`state_from_protocol()` both include `item["cycle_offs"]` (list of `{"start_week": str, "weeks": str}` dicts — the UI only ever shows/submits a weeks *count*, never `end_week` directly).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_protocol_forms.py`:

```python
def test_cycle_off_happy_path():
    p, e = parse_protocol_form(base(
        items__0__cycle_offs__0__start_week="4", items__0__cycle_offs__0__weeks="3"), **IDS)
    assert e == {}
    assert [(c.start_week, c.end_week) for c in p.items[0].cycle_offs] == [(4, 6)]


def test_cycle_off_requires_weeks():
    _, e = parse_protocol_form(base(
        items__0__cycle_offs__0__start_week="4", items__0__cycle_offs__0__weeks=""), **IDS)
    assert "items-0-cycle_offs-0-weeks" in e


def test_cycle_offs_cannot_overlap_each_other():
    _, e = parse_protocol_form(base(
        items__0__cycle_offs__0__start_week="1", items__0__cycle_offs__0__weeks="4",
        items__0__cycle_offs__1__start_week="3", items__0__cycle_offs__1__weeks="2"), **IDS)
    assert "items-0-cycle_offs-1-start_week" in e


def test_cycle_off_cannot_overlap_a_titration_step():
    _, e = parse_protocol_form(base(titration="1",
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="6", items__0__steps__0__dose="1",
        items__0__cycle_offs__0__start_week="4", items__0__cycle_offs__0__weeks="2"), **IDS)
    assert "items-0-cycle_offs-0-start_week" in e


def test_cycle_off_not_offered_state_still_round_trips_when_absent():
    form = base()
    s = state_from_form(form)
    assert s["items"][0]["cycle_offs"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_protocol_forms.py -k cycle_off -v`
Expected: FAIL — `KeyError: 'cycle_offs'` or similar, since none of this exists yet.

- [ ] **Step 3: Implement**

In `app/protocols/forms.py`:

Update the module docstring's field list (after line 7) to add:
```
  items-{i}-cycle_offs-{j}-start_week, -weeks
```

Add the constant and regex near `STEP_FIELDS`/`_STEP_KEY` (lines 23, 26):
```python
CYCLE_OFF_FIELDS = ("start_week", "weeks")
```
```python
_CYCLE_OFF_KEY = re.compile(r"^items-(\d+)-cycle_offs-(\d+)-(\w+)$")
```

Add the dataclass near `ParsedStep` (line 30-33):
```python
@dataclass
class ParsedCycleOff:
    start_week: int
    end_week: int
```

Add `cycle_offs` to `ParsedItem` (line 49), after `steps`:
```python
    cycle_offs: list["ParsedCycleOff"] = field(default_factory=list)
```

In `state_from_form()` (lines 79-112), the parsing loop needs a third branch. Replace the loop body (lines 81-93):
```python
    for key in form:
        if m := _STEP_KEY.match(key):
            i, j, name = int(m[1]), int(m[2]), m[3]
            if name in STEP_FIELDS:
                item = items.setdefault(i, {"steps": {}, "cycle_offs": {}})
                item["steps"].setdefault(j, {})[name] = _first(form, key)
        elif m := _CYCLE_OFF_KEY.match(key):
            i, j, name = int(m[1]), int(m[2]), m[3]
            if name in CYCLE_OFF_FIELDS:
                item = items.setdefault(i, {"steps": {}, "cycle_offs": {}})
                item["cycle_offs"].setdefault(j, {})[name] = _first(form, key)
        elif m := _ITEM_KEY.match(key):
            i, name = int(m[1]), m[2]
            item = items.setdefault(i, {"steps": {}, "cycle_offs": {}})
            if name == "weekdays":
                item["weekdays"] = "".join(v.strip() for v in form[key])
            elif name in ITEM_FIELDS:
                item[name] = _first(form, key)
```

And the item-building loop right after (lines 95-101):
```python
    out_items = []
    for i in sorted(items):
        raw = items[i]
        item = {f: raw.get(f, "") for f in ITEM_FIELDS}
        item["weekdays"] = raw.get("weekdays", "")
        item["steps"] = [{f: raw["steps"][j].get(f, "") for f in STEP_FIELDS} for j in sorted(raw["steps"])]
        item["cycle_offs"] = [{f: raw["cycle_offs"][j].get(f, "") for f in CYCLE_OFF_FIELDS}
                              for j in sorted(raw["cycle_offs"])]
        out_items.append(item)
```

In `state_from_protocol()` (lines 115-149), add `cycle_offs` to each item dict (after the `"steps"` line, line 145):
```python
                "cycle_offs": [{"start_week": str(c.start_week), "weeks": str(c.end_week - c.start_week + 1)}
                              for c in it.cycle_offs],
```

Add the parser function near `_parse_steps` (after line 230):
```python
def _parse_cycle_offs(item_state: dict, prefix: str, errors: dict,
                      steps: list[ParsedStep]) -> list["ParsedCycleOff"]:
    """Cycle-offs are always validated (unlike steps, which are only validated when titration is
    on) -- cycling is independent of the titration toggle."""
    parsed: list[tuple[int, ParsedCycleOff]] = []
    for j, raw in enumerate(item_state["cycle_offs"]):
        if not any(raw.values()):
            continue
        key = f"{prefix}-cycle_offs-{j}"
        start = _parse_int(raw["start_week"], f"{key}-start_week", errors, minimum=1, label="Start week")
        weeks = _parse_int(raw["weeks"], f"{key}-weeks", errors, minimum=1, label="Weeks off")
        if start is None and f"{key}-start_week" not in errors:
            errors[f"{key}-start_week"] = "Start week is required."
        if weeks is None and f"{key}-weeks" not in errors:
            errors[f"{key}-weeks"] = "Weeks off is required."
        if start is None or weeks is None:
            continue
        parsed.append((j, ParsedCycleOff(start, start + weeks - 1)))

    parsed.sort(key=lambda pair: pair[1].start_week)
    for (ja, a), (jb, b) in zip(parsed, parsed[1:]):
        if b.start_week <= a.end_week:
            errors[f"{prefix}-cycle_offs-{jb}-start_week"] = "Overlaps the previous cycle-off."

    for j, off in parsed:
        for k, step in enumerate(steps):
            step_end = step.end_week if step.end_week is not None else off.end_week
            if off.start_week <= step_end and step.start_week <= off.end_week:
                errors[f"{prefix}-cycle_offs-{j}-start_week"] = "Overlaps a titration step."
                break

    return [off for _, off in parsed]
```

In `parse_protocol_form()`, the `ParsedItem(...)` construction (around line 309-322) gains the new field, and `_parse_cycle_offs` must run after `steps` is computed so it can check overlap against it. Change:
```python
        steps = _parse_steps(raw, key, titration, errors)
        items.append(ParsedItem(
            peptide_id=peptide_id,
            new_name=new_name,
            dose=_parse_dose(raw["dose"], f"{key}-dose", errors),
            dose_unit=_parse_choice(DoseUnit, raw["dose_unit"], DoseUnit.MG, f"{key}-dose_unit", errors),
            frequency=frequency,
            every_n_days=every_n_days,
            weekdays=weekdays,
            time_of_day=_parse_choice(TimeOfDay, raw["time_of_day"], TimeOfDay.ANY, f"{key}-time_of_day", errors),
            route=_parse_choice(Route, raw["route"], Route.SUBQ, f"{key}-route", errors),
            inventory_item_id=inventory_item_id,
            notes=notes,
            steps=steps,
            cycle_offs=_parse_cycle_offs(raw, key, errors, steps),
        ))
```

(This replaces the existing `steps=_parse_steps(raw, key, titration, errors),` line inside the same `ParsedItem(...)` call — don't create a second call.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m pytest tests/test_protocol_forms.py -v`
Expected: all pass, including the five new tests.

- [ ] **Step 5: Commit**

```bash
git add app/protocols/forms.py tests/test_protocol_forms.py
git commit -m "feat: parse and validate protocol item cycle-offs"
```

---

### Task 5: Persist cycle-offs on save, and round-trip through Backup

**Files:**
- Modify: `app/routers/protocols.py` (`save_protocol`, around line 253-258)
- Modify: `app/routers/backup.py` (`_protocol_row`, `_import_protocol_row`)
- Test: `tests/test_protocols.py`, `tests/test_backup.py`

**Interfaces:**
- Consumes: `ParsedItem.cycle_offs` from Task 4; `ProtocolItemCycleOff` from Task 1.
- Produces: nothing new for later tasks — this is the last piece needed before the builder UI (Task 6) can actually save anything.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_protocols.py` (check its existing imports/helpers first — it already has a protocol-creation helper used by `test_card_shows_current_titration_step`; reuse that same helper/pattern):

```python
def test_saving_a_protocol_persists_cycle_offs(client, db):
    from app.models import Protocol
    client.post("/protocols", data={
        "name": "Cycled", "start_date": "2026-09-01", "weeks": "12", "goal": "fat-loss",
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am", "items-0-route": "subq",
        "items-0-cycle_offs-0-start_week": "4", "items-0-cycle_offs-0-weeks": "3",
    })
    p = db.scalar(select(Protocol).where(Protocol.name == "Cycled"))
    assert [(c.start_week, c.end_week) for c in p.items[0].cycle_offs] == [(4, 6)]
```

(Match the exact existing form-POST field set and helper names — `peptide_id()`, goal slug, etc. — used by this file's other protocol-creation tests; read a neighboring test first and mirror its setup exactly rather than guessing field names.)

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_protocols.py -k cycle_offs -v`
Expected: FAIL — `p.items[0].cycle_offs == []`, since `save_protocol` doesn't persist them yet.

- [ ] **Step 3: Implement**

In `app/routers/protocols.py`, import `ProtocolItemCycleOff` at the top (wherever `TitrationStep` is already imported from `app.models`), then update the `ProtocolItem(...)` construction inside `save_protocol` (line 253-258):

```python
        new_item = ProtocolItem(
            peptide_id=peptide_id, position=position, dose=it.dose, dose_unit=it.dose_unit,
            frequency=it.frequency, every_n_days=it.every_n_days, weekdays=it.weekdays,
            time_of_day=it.time_of_day, route=it.route, inventory_item_id=it.inventory_item_id, notes=it.notes,
            steps=[TitrationStep(start_week=s.start_week, end_week=s.end_week, dose=s.dose) for s in it.steps],
            cycle_offs=[ProtocolItemCycleOff(start_week=c.start_week, end_week=c.end_week)
                       for c in it.cycle_offs],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_protocols.py -v`
Expected: all pass, including the new test.

- [ ] **Step 5: Write the failing backup round-trip test**

Add to `tests/test_backup.py` (find its existing protocol-export/import test and mirror its setup exactly):

```python
def test_backup_round_trips_cycle_offs(client, db):
    from app.models import Protocol, ProtocolItemCycleOff
    client.post("/protocols", data={
        "name": "Cycled Backup", "start_date": "2026-09-01", "weeks": "12", "goal": "fat-loss",
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am", "items-0-route": "subq",
        "items-0-cycle_offs-0-start_week": "4", "items-0-cycle_offs-0-weeks": "3",
    })
    export = client.get("/backup/export.json").json()
    with SessionLocal() as s:
        s.query(Protocol).filter_by(name="Cycled Backup").delete()
        s.commit()
    client.post("/backup/import", files={"file": ("backup.json", json.dumps(export), "application/json")})
    with SessionLocal() as s:
        p = s.scalar(select(Protocol).where(Protocol.name == "Cycled Backup"))
        assert [(c.start_week, c.end_week) for c in p.items[0].cycle_offs] == [(4, 6)]
```

(Match this file's actual existing import-call convention — multipart file upload field name, any required confirmation field, JSON serialization — to a neighboring backup round-trip test; read it first.)

- [ ] **Step 6: Run test to verify it fails**

Run: `py -m pytest tests/test_backup.py -k cycle_offs -v`
Expected: FAIL — the re-imported protocol's `cycle_offs` is empty, since `_protocol_row`/`_import_protocol_row` don't handle the field yet.

- [ ] **Step 7: Implement**

In `app/routers/backup.py`, add `cycle_offs` to `_protocol_row()`'s item dict (after the `"steps"` line, around line 75):
```python
                "cycle_offs": [{"start_week": c.start_week, "end_week": c.end_week} for c in it.cycle_offs],
```

Import `ProtocolItemCycleOff` at the top (alongside the existing `TitrationStep` import). Add to `_import_protocol_row()`'s `ProtocolItem(...)` construction (around line 226-227):
```python
            steps=[TitrationStep(start_week=s["start_week"], end_week=s.get("end_week"), dose=s["dose"])
                  for s in item.get("steps", [])],
            cycle_offs=[ProtocolItemCycleOff(start_week=c["start_week"], end_week=c["end_week"])
                       for c in item.get("cycle_offs", [])],
```

- [ ] **Step 8: Run test to verify it passes**

Run: `py -m pytest tests/test_backup.py -v`
Expected: all pass, including the new test.

- [ ] **Step 9: Commit**

```bash
git add app/routers/protocols.py app/routers/backup.py tests/test_protocols.py tests/test_backup.py
git commit -m "feat: persist and back up protocol item cycle-offs"
```

---

### Task 6: Builder UI — "+ Cycle off" / "+ Cycle on"

**Files:**
- Modify: `app/static/js/protocol-builder.js`
- Modify: `app/static/css/app.css`
- Test: manual verification via an isolated TestClient render (this is client-side JS with no build step and no existing JS test harness in this repo — verify by rendering the builder page and reading the emitted HTML/JSON, matching how this session has verified other JS-driven pages all along)

**Interfaces:**
- Consumes: `it.cycle_offs` (each `{start_week, weeks}`, string values) already present in every item's state object once Task 4 ships (since `newItem()`'s spread-default pattern and `state_from_form`/`state_from_protocol` all now include it) — but `newItem()` itself (line 20-25) needs `cycle_offs: []` added to its defaults, or a freshly-added item has no key at all.
- Produces: submitted form fields `items-{i}-cycle_offs-{j}-start_week`, `-weeks`.

- [ ] **Step 1: Add `cycle_offs` to `newItem()`'s defaults**

In `app/static/js/protocol-builder.js`, update `newItem()` (lines 20-25):
```python
```
```javascript
  const newItem = (fields = {}) => ({
    uid: ++uid, peptide_id: "", new_name: "", dose: "", dose_unit: "mg", frequency: "daily", every_n_days: "",
    weekdays: "", time_of_day: "any", route: "subq", inventory_item_id: "", notes: "", steps: [], cycle_offs: [],
    // Blank values from a re-shown form fall back to the defaults above.
    ...Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== "")),
  });
```

Also update the two places items are hydrated from server data (line 28, inside the module's top-level setup):
```javascript
  let items = data.state.items.map((it) => newItem({
    ...it, steps: it.steps.map((s) => ({ ...s })), cycle_offs: (it.cycle_offs || []).map((c) => ({ ...c })),
  }));
```

- [ ] **Step 2: Add the cycle-off row renderer and the item-card section**

Add this function immediately after `stepRow()` (after line 207):
```javascript
  function cycleOffRow(it, i, j, off) {
    const p = `items-${i}-cycle_offs-${j}`;
    const num = (name, value, placeholder) => h("input", {
      name: `${p}-${name}`, type: "number", min: "1", step: "1", inputmode: "numeric", value, placeholder,
      "aria-label": name.replace("_", " "), oninput: (e) => (off[name] = e.target.value),
    });
    return h("div", { class: "cycle-off-row" },
      h("span", { class: "small muted", text: `Off ${j + 1}` }),
      h("div", { class: "field" }, h("span", { class: "small", text: "Starts week" }), num("start_week", off.start_week, "")),
      h("div", { class: "field" }, h("span", { class: "small", text: "Weeks off" }), num("weeks", off.weeks, "")),
      h("button", { type: "button", class: "btn btn-ghost btn-icon", "aria-label": "Remove cycle-off",
        onclick: () => { it.cycle_offs.splice(j, 1); changed(); } }, "×"));
  }
```

Then, inside `itemCard()`, add a cycle-off section right after the existing `h("div", { class: "steps" }, ...)` block (after line 269, still before `syncFreq(); return card;` on lines 270-271). The new block is gated on frequency — never shown for "as needed":
```javascript
      it.frequency === "as_needed" ? null : h("div", { class: "cycle-offs" },
        h("div", { class: "steps-head" }, h("strong", { class: "small", text: "Cycle on/off" })),
        it.cycle_offs.map((c, j) => cycleOffRow(it, i, j, c)),
        h("button", { type: "button", class: "btn btn-ghost", onclick: () => {
          const lastOff = it.cycle_offs[it.cycle_offs.length - 1];
          const lastStep = it.steps[it.steps.length - 1];
          const afterOff = lastOff ? Number(lastOff.start_week) + Number(lastOff.weeks) : 0;
          const afterStep = lastStep && lastStep.end_week ? Number(lastStep.end_week) + 1 : 0;
          const next = Math.max(afterOff, afterStep, 1);
          it.cycle_offs.push({ start_week: String(next), weeks: "" });
          changed();
        } }, it.cycle_offs.length && !it.cycle_offs[it.cycle_offs.length - 1].weeks ? "+ Cycle on" : "+ Cycle off")));
```

This is the same `.reduce`-free, "last row still blank means we're mid-entry" convention the rest of this function already leans on (e.g. `unitLabel` lookups) — the button reads "+ Cycle on" only while the most recently added row hasn't had its "Weeks off" filled in yet, then reverts to "+ Cycle off" once it has (since at that point the gap is fully specified and a new one would start a fresh off-period, not resume the open one). This matches the spec's "the button reverts to '+ Cycle off' once that next step exists" — here, "next step exists" is read as "the open row has been filled in," since this codebase has no separate concept of confirming a row.

Since `itemCard()` returns a single `h("div", {class: "item-card"}, ...)` with several children already, insert the new expression as one more child in that same children list — immediately after the existing `h("div", { class: "steps" }, ...)` element and before the closing `);` of the `h("div", { class: "item-card" }, ...)` call.

- [ ] **Step 3: Add CSS — always visible, independent of `.titration-on`**

In `app/static/css/app.css`, immediately after the existing `.step-row` rules (around line 609), add:
```css
.cycle-offs { margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--border); }
.cycle-off-row { display: grid; grid-template-columns: 56px minmax(0, 1fr) minmax(0, 1fr) auto; gap: 10px; align-items: start; margin: 8px 0; }
.cycle-off-row > .small { padding-top: 34px; }
.cycle-off-row > .btn-icon { margin-top: 26px; }
```

(Deliberately no `display: none` / `.titration-on` gating — `.steps` hides by default and only shows when titration is on; `.cycle-offs` must always render, so it gets no such rule.)

- [ ] **Step 4: Verify by rendering the builder page**

There's no existing JS unit-test harness in this repo for `protocol-builder.js` (confirm by checking for a `tests/*.js` or similar — if none exists, this step is the verification). Run:

```bash
py -c "
import tempfile, os
d = tempfile.mkdtemp()
os.environ['AMIDE_DATA_DIR'] = d
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as c:
    c.post('/notice', data={'understand': '1'})
    c.post('/register', data={'username': 'tester', 'password': 'Test1!', 'confirm': 'Test1!'})
    t = c.get('/protocols/new').text
    assert 'js/protocol-builder.js' in t
    print('builder page renders OK')
"
```

Expected: prints `builder page renders OK` with no traceback. Then use the Browser pane against this same isolated TestClient-backed page (never the user's real running instance) to click "Add" on a peptide, click "+ Cycle off", confirm the row appears with "Starts week"/"Weeks off" inputs and the button now reads "+ Cycle on", fill in a weeks value, confirm the button reverts to "+ Cycle off", and confirm the section is visible whether or not the protocol-level "Titration" checkbox is checked.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/protocol-builder.js app/static/css/app.css
git commit -m "feat: add Cycle on/off UI to the protocol builder"
```

---

### Task 7: Edit and Repeat show existing cycle-offs

**Files:**
- Test: `tests/test_protocols.py`

**Interfaces:**
- Consumes: `state_from_protocol()` from Task 4 (already serializes `cycle_offs` — this task only proves the edit/repeat pages actually receive and embed it).

- [ ] **Step 1: Write the failing test**

```python
def test_edit_page_shows_existing_cycle_offs(client, db):
    from app.models import Protocol, ProtocolItemCycleOff
    client.post("/protocols", data={
        "name": "Edit Cycle Test", "start_date": "2026-09-01", "weeks": "12", "goal": "fat-loss",
        "items-0-peptide_id": str(peptide_id("BPC-157")), "items-0-dose": "250", "items-0-dose_unit": "mcg",
        "items-0-frequency": "daily", "items-0-time_of_day": "am", "items-0-route": "subq",
        "items-0-cycle_offs-0-start_week": "4", "items-0-cycle_offs-0-weeks": "3",
    })
    with SessionLocal() as s:
        pid = s.scalar(select(Protocol.id).where(Protocol.name == "Edit Cycle Test"))
    t = client.get(f"/protocols/{pid}/edit").text
    assert '"start_week": "4"' in t and '"weeks": "3"' in t
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `py -m pytest tests/test_protocols.py -k edit_page_shows_existing_cycle_offs -v`
Expected: this should already PASS if Tasks 1-5 are complete and correct (the edit page already calls `state_from_protocol()`, which Task 4 updated) — this task is a confirmation step, not new implementation. If it fails, the failure points at a gap in Task 4 or 5 to go back and fix (do not add new code here to force it green; find and fix the real gap in its owning task).

- [ ] **Step 3: Commit**

```bash
git add tests/test_protocols.py
git commit -m "test: confirm the edit page round-trips existing cycle-offs"
```

---

## Final Verification

After all tasks are complete, run the full suite once more and confirm CSS brace balance (the established convention in this codebase for every CSS change this session):

```bash
py -m pytest -q
py -c "
css = open('app/static/css/app.css', encoding='utf-8').read()
print('open:', css.count('{'), 'close:', css.count('}'))
"
```

Both counts must match, and every test must pass.

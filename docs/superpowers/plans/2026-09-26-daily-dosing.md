# Daily Dosing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the core dosing loop from the roadmap's Phase 3 — a Today view of what's due, logging a
dose against an Active Vial (which now actually depletes), injection-site rotation guidance,
skip/missed/late tracking, and peptide-pen support.

**Architecture:** `app/calendar/schedule.py`'s existing `occurrences()`/`DueItem`/`is_due()` stay
the single source of truth for "what's due when" — this plan extends `DueItem` with the one field
it's missing (`protocol_item_id`) rather than duplicating scheduling logic. A new `DoseLog` model
records outcomes (logged/skipped, and missed is inferred rather than stored). `ActiveVial` gains
real depletion (`volume_remaining_ml`) and a dispensing-method flag (`Syringe`/`Pen`).

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic (SQLite) + Jinja2 + vanilla JS.

**Spec:** `docs/superpowers/specs/2026-09-26-daily-dosing-design.md`

## Global Constraints

- Money is not involved in this plan; standard money-as-cents rules don't apply here.
- `DoseLog` denormalizes peptide/dose/route at log time and never hard-depends on
  `protocol_item_id` staying valid — editing a saved Protocol clears and rebuilds all its
  `ProtocolItem` rows (`save_protocol` in `app/routers/protocols.py`), so history must survive that.
- Ownership checks follow this app's existing pattern everywhere (`_get_protocol`,
  `_own_item`-style helpers) — never trust a posted id without one.
- `volume_ml` for a dose is only computed when the dose's unit is mg or mcg and a vial is actually
  available to draw from (mirrors the existing Calculator's own limitation: IU doses have no
  universal mg conversion, so `app/calculator/reconstitution.py`'s `compute()` already can't
  handle them either — this plan does not fix that pre-existing gap, just doesn't crash on it).
- No priming-loss modeling, no pen-cartridge capacity cap, no cycles/titration-templates — all
  explicitly deferred per the spec.

## Review Focus

- Logging a dose against a protocol/vial you don't own always 404s.
- Two open Active Vials for the same inventory item: logging always draws from the
  earliest-`discard_by` one, never the newer one.
- `volume_remaining_ml` reaching `<= 0` triggers the empty-vial prompt and blocks further logging
  against that vial, rather than silently going negative.
- A dose logged any time on its scheduled day is `ON_TIME`, regardless of `time_of_day` — "missed
  at end of due day" means the whole day counts.
- Editing a saved Protocol (which clears and rebuilds its `ProtocolItem` rows) never deletes or
  corrupts existing `DoseLog` history.

---

### Task 1: `DoseLog` model, `ActiveVial` additions, migration

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0014_dose_logging.py`
- Modify: `tests/test_active_vials.py` (model-level tests, alongside existing `ActiveVial` tests)
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `DispensingMethod`, `InjectionSite`, `DoseStatus` enums; `DoseLog` model;
  `ActiveVial.dispensing_method`/`.volume_remaining_ml` — every later task depends on these exact
  names.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_active_vials.py`:

```python
def test_active_vial_volume_remaining_defaults_and_depletes(db, me):
    from app.models import ActiveVial, DispensingMethod

    item = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                         medium=Medium.LYOPHILIZED, vial_size_mg=10)
    db.add(item)
    db.flush()
    vial = ActiveVial(owner_id=me, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0,
                      dose_value=2.0, dose_unit=DoseUnit.MG, doses_total=5,
                      date_mixed=date(2026, 9, 1), discard_by=date(2026, 9, 29),
                      volume_remaining_ml=2.0)
    db.add(vial)
    db.commit()
    assert vial.dispensing_method == DispensingMethod.SYRINGE  # default
    vial.volume_remaining_ml -= 0.4
    db.commit()
    db.refresh(vial)
    assert vial.volume_remaining_ml == 1.6


def test_dose_log_model_constraints_and_denormalization(db, me):
    from app.models import DoseLog, DoseStatus, InjectionSite, Peptide, Protocol, ProtocolItem, Route

    peptide = Peptide(name="Retatrutide")
    db.add(peptide)
    db.flush()
    protocol = Protocol(name="Fat Loss", start_date=date(2026, 9, 1), owner_id=me)
    db.add(protocol)
    db.flush()
    item = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                        route=Route.SUBQ)
    db.add(item)
    db.commit()

    log = DoseLog(
        owner_id=me, protocol_id=protocol.id, protocol_item_id=item.id, peptide_id=peptide.id,
        peptide_name="Retatrutide", dose_value=2.0, dose_unit=DoseUnit.MG, route="subq",
        scheduled_date=date(2026, 9, 25), scheduled_time_of_day=TimeOfDay.AM, status=DoseStatus.ON_TIME,
        logged_at=datetime.now(timezone.utc), injection_site=InjectionSite.ABDOMEN_L, volume_ml=0.4,
    )
    db.add(log)
    db.commit()
    assert log.peptide_name == "Retatrutide"  # denormalized, doesn't depend on peptide relationship

    # Deleting the ProtocolItem (as save_protocol does on every edit) must not delete the log.
    db.delete(item)
    db.commit()
    db.refresh(log)
    assert log.protocol_item_id is None  # SET NULL, not cascaded
    assert log.peptide_name == "Retatrutide"  # history intact

    with pytest.raises(IntegrityError):
        db.add(DoseLog(owner_id=me, protocol_id=protocol.id, peptide_id=peptide.id, peptide_name="X",
                       dose_unit=DoseUnit.MG, route="subq", scheduled_date=date(2026, 9, 25),
                       scheduled_time_of_day=TimeOfDay.AM, status=DoseStatus.SKIPPED, volume_ml=-1))
        db.flush()
    db.rollback()
```

Add `from datetime import datetime, timezone` and `from app.models import TimeOfDay` to the test
file's imports if not already present (check the top of the file first) — `pytest` and
`IntegrityError` too, matching this session's established pattern in other test files.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_active_vials.py -k "volume_remaining or dose_log_model" -v`
Expected: FAIL — `AttributeError`/`ImportError` (none of these names exist yet).

- [ ] **Step 3: Add the enums and model changes**

In `app/models.py`, add directly after the `PeptideSource` enum (near the other `LabeledEnum`
definitions, before `WEEKDAY_LETTERS`):

```python
class DispensingMethod(LabeledEnum):
    SYRINGE = ("syringe", "Syringe")
    PEN = ("pen", "Peptide pen")


class InjectionSite(LabeledEnum):
    ABDOMEN_L = ("abdomen_l", "Left abdomen")
    ABDOMEN_R = ("abdomen_r", "Right abdomen")
    THIGH_L = ("thigh_l", "Left thigh")
    THIGH_R = ("thigh_r", "Right thigh")
    ARM_L = ("arm_l", "Left upper arm")
    ARM_R = ("arm_r", "Right upper arm")
    GLUTE_L = ("glute_l", "Left glute")
    GLUTE_R = ("glute_r", "Right glute")


class DoseStatus(LabeledEnum):
    ON_TIME = ("on_time", "On time")
    LATE = ("late", "Logged late")
    MISSED = ("missed", "Missed")  # never written to the DB -- inferred at read time; kept here so
                                   # DoseLog.status and a computed "missed" display value share one type
    SKIPPED = ("skipped", "Skipped")
```

Add two columns to `ActiveVial` (in `app/models.py`, right after `doses_total`):

```python
    dispensing_method: Mapped[DispensingMethod] = mapped_column(_enum_column(DispensingMethod), default=DispensingMethod.SYRINGE)
    volume_remaining_ml: Mapped[float] = mapped_column(Float)
```

Add the `DoseLog` class directly after `ActiveVial` (before the `# ---------------------------------------------------------------- protocols` comment):

```python
class DoseLog(Base):
    """One due-item-on-one-date outcome: logged (on time or late) or skipped. A day that passes
    with nothing logged is "missed" -- inferred at read time by diffing occurrences() against
    existing rows here, never written as its own row (see app.calendar.schedule.missed_items).
    Denormalizes peptide/dose/route/time_of_day at log time rather than trusting
    protocol_item_id to keep meaning -- editing a saved Protocol clears and rebuilds all its
    ProtocolItem rows (see app.protocols' save_protocol), so a hard FK there would silently lose
    history on every edit. protocol_item_id is kept as a nullable, best-effort deep link only."""

    __tablename__ = "dose_logs"
    __table_args__ = (
        CheckConstraint("dose_value IS NULL OR dose_value > 0", name="ck_dose_log_dose_pos"),
        CheckConstraint("volume_ml IS NULL OR volume_ml > 0", name="ck_dose_log_volume_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    protocol_id: Mapped[int] = mapped_column(ForeignKey("protocols.id", ondelete="CASCADE"), index=True)
    protocol_item_id: Mapped[int | None] = mapped_column(ForeignKey("protocol_items.id", ondelete="SET NULL"))
    active_vial_id: Mapped[int | None] = mapped_column(ForeignKey("active_vials.id", ondelete="SET NULL"))
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="RESTRICT"))
    peptide_name: Mapped[str] = mapped_column(String(120))
    dose_value: Mapped[float | None] = mapped_column(Float)
    dose_unit: Mapped[DoseUnit] = mapped_column(_enum_column(DoseUnit))
    route: Mapped[str] = mapped_column(String(20))
    scheduled_date: Mapped[date] = mapped_column(Date)
    scheduled_time_of_day: Mapped[TimeOfDay] = mapped_column(_enum_column(TimeOfDay))
    status: Mapped[DoseStatus] = mapped_column(_enum_column(DoseStatus))
    logged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    injection_site: Mapped[InjectionSite | None] = mapped_column(_enum_column(InjectionSite))
    volume_ml: Mapped[float | None] = mapped_column(Float)

    protocol: Mapped["Protocol"] = relationship()
    active_vial: Mapped["ActiveVial | None"] = relationship()
```

Note `DoseLog` references `Protocol`/`ProtocolItem`/`Peptide`/`TimeOfDay`, all defined further down
in this same file — SQLAlchemy resolves string-quoted relationship targets lazily, and the
`ForeignKey`/column type references only need the referenced *table names* (already fixed strings)
and the enums/`TimeOfDay`, which are defined above `Protocol` — this is fine as-is, no reordering
needed.

- [ ] **Step 4: Run model tests to verify they pass**

Run: `pytest tests/test_active_vials.py -k "volume_remaining or dose_log_model" -v`
Expected: FAIL — `sqlalchemy.exc.OperationalError: no such table/column` (model exists, schema
doesn't yet — Steps 5-8 add the migration).

- [ ] **Step 5: Write the failing migration test**

Add to `tests/test_migrations.py`, directly after `test_0013_splits_orders_into_order_items`:

```python
def test_0014_adds_dose_logging(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0013")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        c.execute("insert into inventory_items(name,count,vial_size_unit,category,created_at,"
                  "updated_at,owner_id) values ('Retatrutide',0,'mg','Medicine','2026-09-25',"
                  "'2026-09-25',1)")
        item_id = c.execute("select id from inventory_items where name='Retatrutide'").fetchone()[0]
        c.execute("insert into active_vials(owner_id,inventory_item_id,concentration_mg_ml,water_ml,"
                  "dose_value,dose_unit,doses_total,date_mixed,discard_by,created_at) values "
                  "(1,?,5.0,2.0,2.0,'mg',5,'2026-09-01','2026-09-29','2026-09-01')", (item_id,))
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(dose_logs)")}
        assert {"owner_id", "protocol_id", "protocol_item_id", "active_vial_id", "peptide_id",
               "peptide_name", "dose_value", "dose_unit", "route", "scheduled_date",
               "scheduled_time_of_day", "status", "logged_at", "injection_site", "volume_ml"} <= cols
        vial_cols = {r[1] for r in c.execute("pragma table_info(active_vials)")}
        assert {"dispensing_method", "volume_remaining_ml"} <= vial_cols
        method, remaining = c.execute(
            "select dispensing_method, volume_remaining_ml from active_vials").fetchone()
        assert method == "syringe" and remaining == 2.0  # backfilled from water_ml
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into dose_logs(owner_id,protocol_id,peptide_id,peptide_name,dose_unit,"
                      "route,scheduled_date,scheduled_time_of_day,status,volume_ml) values "
                      "(1,1,1,'X','mg','subq','2026-09-25','am','skipped',-1)")
    command.downgrade(cfg, "0013")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "dose_logs" not in tables
        vial_cols = {r[1] for r in c.execute("pragma table_info(active_vials)")}
        assert "dispensing_method" not in vial_cols and "volume_remaining_ml" not in vial_cols
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_migrations.py::test_0014_adds_dose_logging -v`
Expected: FAIL — no revision `0014` exists yet.

- [ ] **Step 7: Write the migration**

Create `migrations/versions/0014_dose_logging.py`:

```python
"""dose logging: DoseLog table, ActiveVial dispensing_method + volume_remaining_ml

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'dose_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('protocol_id', sa.Integer(), nullable=False),
        sa.Column('protocol_item_id', sa.Integer(), nullable=True),
        sa.Column('active_vial_id', sa.Integer(), nullable=True),
        sa.Column('peptide_id', sa.Integer(), nullable=False),
        sa.Column('peptide_name', sa.String(length=120), nullable=False),
        sa.Column('dose_value', sa.Float(), nullable=True),
        sa.Column('dose_unit', sa.Enum('mg', 'mcg', 'IU', name='dose_unit', native_enum=False, length=20), nullable=False),
        sa.Column('route', sa.String(length=20), nullable=False),
        sa.Column('scheduled_date', sa.Date(), nullable=False),
        sa.Column('scheduled_time_of_day', sa.Enum('am', 'pm', 'bedtime', 'any', name='time_of_day', native_enum=False, length=20), nullable=False),
        sa.Column('status', sa.Enum('on_time', 'late', 'missed', 'skipped', name='dose_status', native_enum=False, length=20), nullable=False),
        sa.Column('logged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('injection_site', sa.Enum('abdomen_l', 'abdomen_r', 'thigh_l', 'thigh_r', 'arm_l', 'arm_r', 'glute_l', 'glute_r', name='injection_site', native_enum=False, length=20), nullable=True),
        sa.Column('volume_ml', sa.Float(), nullable=True),
        sa.CheckConstraint('dose_value IS NULL OR dose_value > 0', name='ck_dose_log_dose_pos'),
        sa.CheckConstraint('volume_ml IS NULL OR volume_ml > 0', name='ck_dose_log_volume_pos'),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['protocol_id'], ['protocols.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['protocol_item_id'], ['protocol_items.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['active_vial_id'], ['active_vials.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['peptide_id'], ['peptides.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dose_logs_owner_id', 'dose_logs', ['owner_id'])
    op.create_index('ix_dose_logs_protocol_id', 'dose_logs', ['protocol_id'])

    with op.batch_alter_table('active_vials', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'dispensing_method', sa.Enum('syringe', 'pen', name='dispensing_method', native_enum=False, length=20),
            nullable=False, server_default='syringe'))
        batch_op.add_column(sa.Column('volume_remaining_ml', sa.Float(), nullable=True))

    bind.execute(text("UPDATE active_vials SET volume_remaining_ml = water_ml"))

    with op.batch_alter_table('active_vials', schema=None) as batch_op:
        batch_op.alter_column('volume_remaining_ml', nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('active_vials', schema=None) as batch_op:
        batch_op.drop_column('volume_remaining_ml')
        batch_op.drop_column('dispensing_method')
    op.drop_index('ix_dose_logs_protocol_id', table_name='dose_logs')
    op.drop_index('ix_dose_logs_owner_id', table_name='dose_logs')
    op.drop_table('dose_logs')
```

- [ ] **Step 8: Run both tests to verify they pass**

Run: `pytest tests/test_migrations.py::test_0014_adds_dose_logging tests/test_active_vials.py -k "volume_remaining or dose_log_model" -v`
Expected: All PASS.

- [ ] **Step 9: Run the full suite**

Run: `pytest`
Expected: All pass — `volume_remaining_ml` is a new required column, but every existing route that
creates an `ActiveVial` (only `app/routers/calculator.py`'s `reconstitute`) doesn't set it yet, so
this WILL break that route's tests (`OperationalError`/`IntegrityError` from a missing required
value). This is expected and is Task 7's job to fix (it wires the real reconstitution flow to set
`volume_remaining_ml=water_ml` and ask about pens) — note which tests fail here for Task 7's own
reference, but do not fix them in this task.

- [ ] **Step 10: Commit**

```bash
git add app/models.py migrations/versions/0014_dose_logging.py tests/test_active_vials.py tests/test_migrations.py
git commit -m "feat: add DoseLog model and ActiveVial dispensing_method/volume_remaining_ml"
```

---

### Task 2: Scheduling extensions and pure dosing logic

**Files:**
- Modify: `app/calendar/schedule.py`
- Create: `app/dosing/__init__.py` (empty)
- Create: `app/dosing/site.py`
- Modify: `tests/test_active_vials.py` (or a new `tests/test_dosing.py` — this plan creates one)
- Create: `tests/test_dosing.py`

**Interfaces:**
- Produces: `DueItem.protocol_item_id`, `missed_items()` in `app/calendar/schedule.py`;
  `app/dosing/site.py`'s `eligible_sites()`/`recommend()` — Task 3's routes and Task 6's Calendar
  integration both depend on `missed_items()`; Task 4's site picker depends on `site.py`.

This task is pure logic only — no database access beyond what `occurrences()` already receives as
plain Python objects, no new routes yet.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dosing.py`:

```python
from datetime import date

from app.calendar.schedule import DueItem, Occurrence, missed_items
from app.dosing.site import eligible_sites, recommend
from app.models import InjectionSite, Route


def _item(protocol_item_id=1, route="subq"):
    return DueItem(peptide="Retatrutide", peptide_id=1, protocol_item_id=protocol_item_id, dose=2.0,
                  unit="mg", step=None, time_of_day=None, route=route, inventory=None)


def test_due_item_carries_protocol_item_id():
    item = _item(protocol_item_id=42)
    assert item.protocol_item_id == 42


def test_missed_items_excludes_today_and_logged():
    occ_past_logged = Occurrence(date(2026, 9, 20), 1, "Fat Loss", [_item(protocol_item_id=1)])
    occ_past_unlogged = Occurrence(date(2026, 9, 21), 1, "Fat Loss", [_item(protocol_item_id=2)])
    occ_today = Occurrence(date(2026, 9, 25), 1, "Fat Loss", [_item(protocol_item_id=3)])
    logged = {(1, date(2026, 9, 20))}
    result = missed_items([occ_past_logged, occ_past_unlogged, occ_today], logged, today=date(2026, 9, 25))
    assert len(result) == 1
    occ, item = result[0]
    assert occ.date == date(2026, 9, 21) and item.protocol_item_id == 2


def test_eligible_sites_subq_excludes_glute():
    sites = eligible_sites("subq")
    assert InjectionSite.GLUTE_L not in sites and InjectionSite.GLUTE_R not in sites
    assert InjectionSite.ABDOMEN_L in sites


def test_eligible_sites_im_includes_glute():
    sites = eligible_sites("im")
    assert InjectionSite.GLUTE_L in sites


def test_eligible_sites_non_injection_route_is_empty():
    assert eligible_sites("oral") == []


def test_recommend_mirrors_opposite_side_same_body_part():
    assert recommend(InjectionSite.ABDOMEN_L, "subq") == InjectionSite.ABDOMEN_R
    assert recommend(InjectionSite.THIGH_R, "subq") == InjectionSite.THIGH_L


def test_recommend_none_when_no_prior_site():
    assert recommend(None, "subq") is None


def test_recommend_never_crosses_body_parts():
    # Even though Glute isn't eligible for subq, recommend() must never suggest a DIFFERENT body
    # part just because the mirrored one isn't available -- it returns None in that case, not a guess.
    assert recommend(InjectionSite.GLUTE_L, "subq") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dosing.py -v`
Expected: FAIL — `ImportError` (`app.dosing` doesn't exist, `DueItem` has no `protocol_item_id`,
`missed_items` doesn't exist).

- [ ] **Step 3: Extend `DueItem` and add `missed_items`**

In `app/calendar/schedule.py`, add `protocol_item_id: int` to the `DueItem` dataclass (right after
`peptide_id`):

```python
@dataclass
class DueItem:
    peptide: str
    peptide_id: int
    protocol_item_id: int
    dose: float | None
    unit: str
    step: int | None  # titration step number, when one applies
    time_of_day: TimeOfDay
    route: str
    inventory: str | None
```

Update `_due_item()` to pass it through:

```python
def _due_item(p, item, day: date) -> DueItem:
    dose, step_no = item.dose, None
    if p.titration_enabled and item.steps:
        step = current_step(item.steps, current_week(p.start_date, day))
        if step is not None:
            dose, step_no = step.dose, item.steps.index(step) + 1
    return DueItem(
        peptide=item.peptide.name, peptide_id=item.peptide_id, protocol_item_id=item.id, dose=dose,
        unit=item.dose_unit.value, step=step_no,
        time_of_day=item.time_of_day, route=item.route.label if hasattr(item.route, "label") else str(item.route),
        inventory=item.inventory_item.name if item.inventory_item else None,
    )
```

Add `missed_items` at the end of the file:

```python
def missed_items(occs: list[Occurrence], logged: set[tuple[int, date]], today: date) -> list[tuple[Occurrence, DueItem]]:
    """Every (occurrence, item) pair whose scheduled date has fully passed with nothing logged for
    it. `logged` is the set of (protocol_item_id, scheduled_date) pairs that already have a DoseLog
    row (any status) -- callers build this from the database; this function itself never touches
    one."""
    out: list[tuple[Occurrence, DueItem]] = []
    for occ in occs:
        if occ.date >= today:
            continue
        for item in occ.items:
            if (item.protocol_item_id, occ.date) not in logged:
                out.append((occ, item))
    return out
```

- [ ] **Step 4: Add `app/dosing/site.py`**

Create `app/dosing/__init__.py` (empty file).

Create `app/dosing/site.py`:

```python
"""Injection-site rotation. Pure functions, no database access."""

from app.models import InjectionSite

# Each site's mirrored opposite side of the same body part -- recommend() never crosses body parts.
_MIRROR = {
    InjectionSite.ABDOMEN_L: InjectionSite.ABDOMEN_R,
    InjectionSite.ABDOMEN_R: InjectionSite.ABDOMEN_L,
    InjectionSite.THIGH_L: InjectionSite.THIGH_R,
    InjectionSite.THIGH_R: InjectionSite.THIGH_L,
    InjectionSite.ARM_L: InjectionSite.ARM_R,
    InjectionSite.ARM_R: InjectionSite.ARM_L,
    InjectionSite.GLUTE_L: InjectionSite.GLUTE_R,
    InjectionSite.GLUTE_R: InjectionSite.GLUTE_L,
}

_SUBQ_SITES = [InjectionSite.ABDOMEN_L, InjectionSite.ABDOMEN_R, InjectionSite.THIGH_L,
              InjectionSite.THIGH_R, InjectionSite.ARM_L, InjectionSite.ARM_R]
_IM_SITES = _SUBQ_SITES + [InjectionSite.GLUTE_L, InjectionSite.GLUTE_R]

_ROUTE_SITES = {"subq": _SUBQ_SITES, "im": _IM_SITES}


def eligible_sites(route: str) -> list[InjectionSite]:
    """Sites this route can use. Empty for oral/nasal/topical/other -- those never show a picker."""
    return _ROUTE_SITES.get(route, [])


def recommend(last_site: InjectionSite | None, route: str) -> InjectionSite | None:
    """The site to highlight as recommended: the mirrored opposite side of the last-used site's
    body part. None if there's no prior site for this peptide, the route doesn't use sites at all,
    or the mirrored site isn't eligible for this route (never falls back to a different body part)."""
    sites = eligible_sites(route)
    if not sites or last_site is None:
        return None
    partner = _MIRROR.get(last_site)
    return partner if partner in sites else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dosing.py -v`
Expected: All PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass EXCEPT the calculator/reconstitute tests already broken by Task 1's Step 9 (not
this task's concern) — confirm no NEW failures beyond that known set (compare against Task 1's
noted failures).

- [ ] **Step 7: Commit**

```bash
git add app/calendar/schedule.py app/dosing/__init__.py app/dosing/site.py tests/test_dosing.py
git commit -m "feat: extend DueItem with protocol_item_id, add missed-item detection and site rotation logic"
```

---

### Task 3: Today view, Log and Skip routes

**Files:**
- Create: `app/routers/dosing.py`
- Create: `app/templates/dosing/today.html`
- Modify: `app/main.py` (register the new router)
- Modify: `app/templates/base.html` (nav link)
- Modify: `tests/test_dosing.py`

**Interfaces:**
- Consumes: `occurrences`, `missed_items`, `DueItem.protocol_item_id` from Task 2;
  `ActiveVial.volume_remaining_ml`/`.dispensing_method` from Task 1.
- Produces: `GET /today`, `POST /today/log`, `POST /today/skip` routes — Task 4's site-picker JS
  posts to `/today/log`; Task 5 reuses the same log route for catching up a missed dose.

This task's `today.html` is a plain, functional page (list + buttons + a basic dose-amount display)
with NO site picker UI yet — that's Task 4's job, layered on top without changing this task's route
contract. Submitting "Log dose" here works end-to-end already (creates a real `DoseLog`, depletes
the vial), just without the visual site picker.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dosing.py`:

```python
# ---------------------------------------------------------------- Today view + log/skip routes

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import ActiveVial, Category, DoseLog, DoseStatus, DoseUnit, Frequency, InventoryItem, Medium, Peptide, Protocol, ProtocolItem, Route, TimeOfDay, User


def _setup_protocol_with_vial(client, db, dose=2.0, quantity=2):
    """Module-level helper -- `Frequency` etc. must be imported at this file's top level (above),
    not just inside the test functions that call it; a callee doesn't see a caller's local
    imports."""
    with SessionLocal() as s:
        uid = s.scalar(select(User.id))
        peptide = Peptide(name="Retatrutide")
        s.add(peptide)
        s.flush()
        item = InventoryItem(owner_id=uid, name="Retatrutide", category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10)
        s.add(item)
        s.flush()
        vial = ActiveVial(owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=5.0, water_ml=2.0,
                          dose_value=2.0, dose_unit=DoseUnit.MG, doses_total=5, date_mixed=date.today(),
                          discard_by=date(2099, 1, 1), volume_remaining_ml=2.0)
        s.add(vial)
        protocol = Protocol(name="Fat Loss", start_date=date.today(), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=dose, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, route=Route.SUBQ, inventory_item_id=item.id)
        s.add(pitem)
        s.commit()
        return protocol.id, pitem.id, vial.id


def test_today_page_lists_due_items(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    t = html.unescape(client.get("/today").text)
    assert "Retatrutide" in t
    assert 'data-action="log-dose"' in t
    assert 'data-action="skip-dose"' in t


def test_log_dose_depletes_vial_and_creates_dose_log(client, db):
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db, dose=2.0)
    r = client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        vial = s.get(ActiveVial, vial_id)
        assert vial.volume_remaining_ml == pytest.approx(2.0 - 0.4)  # 2mg dose / 5mg/mL concentration = 0.4mL
        [log] = s.scalars(select(DoseLog)).all()
        assert log.status == DoseStatus.ON_TIME and log.peptide_name == "Retatrutide"
        assert log.volume_ml == pytest.approx(0.4)
        assert log.active_vial_id == vial_id


def test_skip_dose_creates_skipped_log_no_vial_change(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    r = client.post("/today/skip", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).volume_remaining_ml == 2.0  # untouched
        [log] = s.scalars(select(DoseLog)).all()
        assert log.status == DoseStatus.SKIPPED and log.volume_ml is None


def test_log_dose_picks_oldest_open_vial_when_two_exist(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, old_vial_id = _setup_protocol_with_vial(client, db)
    with SessionLocal() as s:
        uid = s.scalar(select(User.id))
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        newer_vial = ActiveVial(owner_id=uid, inventory_item_id=item_id, concentration_mg_ml=5.0, water_ml=2.0,
                                dose_value=2.0, dose_unit=DoseUnit.MG, doses_total=5, date_mixed=date.today(),
                                discard_by=date(2099, 6, 1), volume_remaining_ml=2.0)  # later discard_by
        s.add(newer_vial)
        s.commit()
        newer_vial_id = newer_vial.id

    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    with SessionLocal() as s:
        assert s.get(ActiveVial, old_vial_id).volume_remaining_ml < 2.0  # the older-discard_by one was drawn from
        assert s.get(ActiveVial, newer_vial_id).volume_remaining_ml == 2.0  # untouched


def test_log_dose_requires_ownership(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "DosingOther", "password": "DosingOther1!", "confirm": "DosingOther1!"})
    r = other.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    })
    assert r.status_code == 404
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).volume_remaining_ml == 2.0
```

Add `import html` and `from datetime import date` to the top of `tests/test_dosing.py` if not
already present from Task 2's additions.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dosing.py -k "today_page or log_dose or skip_dose" -v`
Expected: FAIL — `404 Not Found` (routes don't exist).

- [ ] **Step 3: Add the router**

Create `app/routers/dosing.py`:

```python
"""The Today view: what's due today, logging a dose (drawing down an Active Vial), skipping one."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.calculator.reconstitution import _DOSE_UNITS_TO_MG
from app.calendar.schedule import missed_items, occurrences
from app.db import get_session
from app.dosing.site import eligible_sites, recommend
from app.models import ActiveVial, DoseLog, DoseStatus, DoseUnit, InjectionSite, Protocol, ProtocolItem
from app.routers.protocols import get_today
from app.auth.deps import current_user_id
from app.templating import templates

router = APIRouter()


def _own_protocol_item(session: Session, protocol_id: int, protocol_item_id: int, uid: int) -> ProtocolItem | None:
    protocol = session.get(Protocol, protocol_id)
    if protocol is None or protocol.owner_id != uid:
        return None
    item = session.get(ProtocolItem, protocol_item_id)
    return item if item is not None and item.protocol_id == protocol.id else None


def _open_vial_for_item(session: Session, inventory_item_id: int) -> ActiveVial | None:
    """The earliest-discard_by open (non-discarded, non-empty) ActiveVial for this inventory item --
    mirrors app.routers.inventory._open_active_vials' ordering for a single item."""
    return session.scalar(
        select(ActiveVial)
        .where(ActiveVial.inventory_item_id == inventory_item_id, ActiveVial.discarded_at.is_(None),
              ActiveVial.volume_remaining_ml > 0)
        .order_by(ActiveVial.discard_by, ActiveVial.id)
    )


def _dose_volume_ml(dose_value: float | None, dose_unit: DoseUnit, vial: ActiveVial) -> float | None:
    """None when the dose can't be converted to mL (an IU dose has no universal mg conversion --
    the Calculator's own compute() has the same limitation) or there's no dose value at all."""
    factor = _DOSE_UNITS_TO_MG.get(dose_unit.value)
    if dose_value is None or factor is None:
        return None
    return (dose_value * factor) / vial.concentration_mg_ml


def _last_site_for_peptide(session: Session, uid: int, peptide_id: int) -> InjectionSite | None:
    log = session.scalar(
        select(DoseLog)
        .where(DoseLog.owner_id == uid, DoseLog.peptide_id == peptide_id, DoseLog.injection_site.is_not(None))
        .order_by(DoseLog.logged_at.desc())
    )
    return log.injection_site if log else None


@router.get("/today")
def today_page(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
              uid: int = Depends(current_user_id)):
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    occs = occurrences(protocols, today, today)
    due = [(occ, item) for occ in occs for item in occ.items]

    logged_ids = {
        (dl.protocol_item_id) for dl in session.scalars(
            select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date == today))
    }
    due = [(occ, item) for occ, item in due if item.protocol_item_id not in logged_ids]

    return templates.TemplateResponse(request, "dosing/today.html", {
        "due": due, "today": today, "today_iso": today.isoformat(),
    })


@router.post("/today/log")
async def log_dose(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    try:
        protocol_id, protocol_item_id = int(form.get("protocol_id", "")), int(form.get("protocol_item_id", ""))
        scheduled_date = date.fromisoformat(str(form.get("scheduled_date", "")))
    except (TypeError, ValueError):
        raise HTTPException(404)
    item = _own_protocol_item(session, protocol_id, protocol_item_id, uid)
    if item is None:
        raise HTTPException(404, "Protocol item not found")
    if scheduled_date > date.today():
        raise HTTPException(422, "Can't log a dose for a future date.")

    vial = None
    volume_ml = None
    site = None
    if item.inventory_item_id is not None:
        vial = _open_vial_for_item(session, item.inventory_item_id)
        if vial is not None:
            volume_ml = _dose_volume_ml(item.dose, item.dose_unit, vial)
            raw_site = str(form.get("injection_site") or "").strip()
            try:
                site = InjectionSite(raw_site) if raw_site else None
            except ValueError:
                site = None
            if site is None and eligible_sites(item.route.value):
                site = recommend(_last_site_for_peptide(session, uid, item.peptide_id), item.route.value)

    status = DoseStatus.ON_TIME if scheduled_date == date.today() else DoseStatus.LATE
    session.add(DoseLog(
        owner_id=uid, protocol_id=protocol_id, protocol_item_id=item.id, active_vial_id=vial.id if vial else None,
        peptide_id=item.peptide_id, peptide_name=item.peptide.name, dose_value=item.dose, dose_unit=item.dose_unit,
        route=item.route.value, scheduled_date=scheduled_date, scheduled_time_of_day=item.time_of_day,
        status=status, logged_at=datetime.now(timezone.utc), injection_site=site, volume_ml=volume_ml,
    ))
    if vial is not None and volume_ml is not None:
        vial.volume_remaining_ml -= volume_ml
    session.commit()
    return RedirectResponse("/today", status_code=303)


@router.post("/today/skip")
async def skip_dose(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    form = await request.form()
    try:
        protocol_id, protocol_item_id = int(form.get("protocol_id", "")), int(form.get("protocol_item_id", ""))
        scheduled_date = date.fromisoformat(str(form.get("scheduled_date", "")))
    except (TypeError, ValueError):
        raise HTTPException(404)
    item = _own_protocol_item(session, protocol_id, protocol_item_id, uid)
    if item is None:
        raise HTTPException(404, "Protocol item not found")

    session.add(DoseLog(
        owner_id=uid, protocol_id=protocol_id, protocol_item_id=item.id, peptide_id=item.peptide_id,
        peptide_name=item.peptide.name, dose_value=item.dose, dose_unit=item.dose_unit, route=item.route.value,
        scheduled_date=scheduled_date, scheduled_time_of_day=item.time_of_day, status=DoseStatus.SKIPPED,
        logged_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return RedirectResponse("/today", status_code=303)
```

Note: `_DOSE_UNITS_TO_MG` is currently a private (underscore-prefixed) name in
`app/calculator/reconstitution.py` — importing it directly across modules like this is not ideal
style, but matches this task's scope (no changes to that module). If a future cleanup wants to
promote it to a public name, that's a separate, unrequested refactor — leave it as-is for this task.

- [ ] **Step 4: Add the template**

Create `app/templates/dosing/today.html`:

```html
{% extends "base.html" %}
{% set active_nav = "today" %}
{% block title %}Today{% endblock %}

{% block content %}
<div class="page-head">
  <div>
    <h1>Today</h1>
    <p class="muted">{{ today.strftime('%A, %B %-d, %Y') }}</p>
  </div>
</div>

{% if not due %}
<div class="empty"><p><strong>Nothing due today.</strong></p></div>
{% else %}
<div class="table-wrap">
<table class="inv-table">
  <thead><tr><th>Peptide</th><th>Dose</th><th>Time</th><th>Route</th><th></th></tr></thead>
  <tbody>
    {% for occ, item in due %}
    <tr>
      <td>{{ item.peptide }}{% if item.step %} <span class="tag">Step {{ item.step }}</span>{% endif %}</td>
      <td>{{ item.dose }} {{ item.unit }}</td>
      <td>{{ item.time_of_day.label }}</td>
      <td>{{ item.route }}</td>
      <td>
        <form method="post" action="/today/log" class="inline">
          <input type="hidden" name="protocol_id" value="{{ occ.protocol_id }}">
          <input type="hidden" name="protocol_item_id" value="{{ item.protocol_item_id }}">
          <input type="hidden" name="scheduled_date" value="{{ today_iso }}">
          <button type="submit" class="btn btn-primary" data-action="log-dose">Log dose</button>
        </form>
        <form method="post" action="/today/skip" class="inline">
          <input type="hidden" name="protocol_id" value="{{ occ.protocol_id }}">
          <input type="hidden" name="protocol_item_id" value="{{ item.protocol_item_id }}">
          <input type="hidden" name="scheduled_date" value="{{ today_iso }}">
          <button type="submit" class="btn btn-ghost" data-action="skip-dose">Skip</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Register the router and nav link**

In `app/main.py`, find where other routers are included (e.g. `app.include_router(calendar.router)`)
and add the same for `dosing`:

```python
from app.routers import dosing
...
app.include_router(dosing.router)
```

In `app/templates/base.html`, find the nav (`<a href="/calendar" ...>Calendar</a>` per the
existing pattern already seen elsewhere) and add a "Today" link before it:

```html
<a href="/today" class="{{ 'active' if active_nav == 'today' }}">Today</a>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_dosing.py -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass except the still-known Task-1-Step-9 calculator/reconstitute failures (Task 7's
job). No NEW failures beyond that set.

- [ ] **Step 8: Commit**

```bash
git add app/routers/dosing.py app/templates/dosing/today.html app/main.py app/templates/base.html tests/test_dosing.py
git commit -m "feat: add Today view with Log/Skip dose routes"
```

---

### Task 4: Injection-site picker

**Files:**
- Modify: `app/templates/dosing/today.html`
- Modify: `app/routers/dosing.py`
- Modify: `app/static/js/dosing.js` (new file)
- Modify: `tests/test_dosing.py`

**Interfaces:**
- Consumes: `eligible_sites`/`recommend` from Task 2; `/today/log` from Task 3 (adds an
  `injection_site` field to the existing form, doesn't change the route's contract for non-SubQ/IM
  items).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dosing.py`:

```python
def test_today_page_shows_site_picker_for_subq_route(client, db):
    from app.models import Frequency
    _setup_protocol_with_vial(client, db)
    t = html.unescape(client.get("/today").text)
    assert 'data-injection-site-picker' in t
    assert 'value="abdomen_l"' in t and 'value="glute_l"' not in t  # SubQ excludes Glute


def test_log_dose_with_explicit_site_records_it(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(), "injection_site": "abdomen_r",
    }, follow_redirects=False)
    with SessionLocal() as s:
        [log] = s.scalars(select(DoseLog)).all()
        assert log.injection_site.value == "abdomen_r"


def test_second_log_recommends_mirrored_site(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db, quantity=4)
    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(), "injection_site": "abdomen_l",
    }, follow_redirects=False)
    t = html.unescape(client.get("/today").text)
    # A second occurrence isn't due again today for a DAILY item, so instead directly check the
    # recommendation surfaces via the API the JS reads -- assert the recommended site is embedded
    # in the page's site data for this peptide.
    assert '"recommended": "abdomen_r"' in t or '&#34;recommended&#34;: &#34;abdomen_r&#34;' in t
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dosing.py -k "site_picker or explicit_site or recommends_mirrored" -v`
Expected: FAIL — no site picker markup exists yet.

- [ ] **Step 3: Add site data to the Today route**

In `app/routers/dosing.py`'s `today_page`, add per-due-item site data. Replace the `due` filtering
block's end (right before the `return templates.TemplateResponse(...)` call) with:

```python
    site_data = {}
    for occ, item in due:
        sites = eligible_sites(item.route)
        if sites:
            last = _last_site_for_peptide(session, uid, item.peptide_id)
            site_data[item.protocol_item_id] = {
                "sites": [{"value": s.value, "label": s.label} for s in sites],
                "last": last.value if last else None,
                "recommended": recommend(last, item.route).value if recommend(last, item.route) else None,
            }

    return templates.TemplateResponse(request, "dosing/today.html", {
        "due": due, "today": today, "today_iso": today.isoformat(), "site_data": site_data,
    })
```

- [ ] **Step 4: Add the site picker to the template**

In `app/templates/dosing/today.html`, replace the `<form method="post" action="/today/log" ...>`
block (from Task 3) with a version that adds the site picker, a hidden `injection_site` input, and
a body-silhouette SVG with one dot per eligible site:

```html
        <form method="post" action="/today/log" class="inline" data-log-form>
          <input type="hidden" name="protocol_id" value="{{ occ.protocol_id }}">
          <input type="hidden" name="protocol_item_id" value="{{ item.protocol_item_id }}">
          <input type="hidden" name="scheduled_date" value="{{ today_iso }}">
          {% if item.protocol_item_id in site_data %}
          <input type="hidden" name="injection_site" value="{{ site_data[item.protocol_item_id].recommended or '' }}" data-site-input>
          <button type="button" class="btn btn-ghost" data-action="pick-site" data-item-id="{{ item.protocol_item_id }}">Pick site</button>
          {% endif %}
          <button type="submit" class="btn btn-primary" data-action="log-dose">Log dose</button>
        </form>
```

Add the site-picker dialog once, near the end of the `{% if not due %}...{% else %}...{% endif %}`
block (after the closing `</table></div>`), plus the JSON data blob and an inline SVG body
silhouette with positioned dots for each `InjectionSite`:

```html
<dialog id="site-dialog" class="dialog" data-injection-site-picker>
  <header class="dialog-head">
    <h2>Injection site</h2>
    <button type="button" class="btn btn-ghost btn-icon" data-action="close-site" aria-label="Close">×</button>
  </header>
  <svg viewBox="0 0 200 400" class="body-silhouette" aria-label="Body diagram with injection sites">
    <path d="M100 10 C80 10 70 30 70 50 C70 70 80 90 70 110 L60 180 L55 260 L60 340 L70 390 L90 390 L92 260 L100 200 L108 260 L110 390 L130 390 L140 340 L145 260 L140 180 L130 110 C120 90 130 70 130 50 C130 30 120 10 100 10 Z"
          fill="none" stroke="currentColor" stroke-width="2"/>
    <circle data-site="abdomen_l" cx="90" cy="150" r="8"></circle>
    <circle data-site="abdomen_r" cx="110" cy="150" r="8"></circle>
    <circle data-site="thigh_l" cx="82" cy="240" r="8"></circle>
    <circle data-site="thigh_r" cx="118" cy="240" r="8"></circle>
    <circle data-site="arm_l" cx="65" cy="120" r="8"></circle>
    <circle data-site="arm_r" cx="135" cy="120" r="8"></circle>
    <circle data-site="glute_l" cx="85" cy="200" r="8"></circle>
    <circle data-site="glute_r" cx="115" cy="200" r="8"></circle>
  </svg>
  <div class="site-legend">
    <span><i class="site-dot site-dot-last"></i> Last used</span>
    <span><i class="site-dot site-dot-recommended"></i> Recommended</span>
    <span><i class="site-dot site-dot-available"></i> Available</span>
  </div>
  <footer class="dialog-foot"><button type="button" class="btn" data-action="close-site">Cancel</button></footer>
</dialog>

<script type="application/json" id="site-data">{{ site_data | tojson }}</script>
```

(The silhouette's `<path>` coordinates are a simple, abstract front-facing body outline — not
polished art. This matches the roadmap's own already-accepted precedent from Phase 2, which notes
the Calculator's syringe visual "still uses its original CSS-drawn look" and defers real icon art
to a later polish pass; a nicer illustration can replace this path later without changing any of
the `data-site` circle logic.)

- [ ] **Step 5: Add the JS**

Create `app/static/js/dosing.js`:

```js
// Today view: injection-site picker dialog.
(() => {
  const dialog = document.getElementById("site-dialog");
  if (!dialog) return;  // No due items need a site today
  const siteDataEl = document.getElementById("site-data");
  const siteData = siteDataEl ? JSON.parse(siteDataEl.textContent) : {};
  let activeForm = null;

  function paintDots(itemId) {
    const data = siteData[itemId];
    dialog.querySelectorAll("circle[data-site]").forEach((circle) => {
      const site = circle.dataset.site;
      circle.classList.remove("site-dot-last", "site-dot-recommended", "site-dot-available", "site-dot-ineligible");
      if (!data || !data.sites.some((s) => s.value === site)) {
        circle.classList.add("site-dot-ineligible");
      } else if (data.last === site) {
        circle.classList.add("site-dot-last");
      } else if (data.recommended === site) {
        circle.classList.add("site-dot-recommended");
      } else {
        circle.classList.add("site-dot-available");
      }
    });
  }

  document.querySelectorAll('[data-action="pick-site"]').forEach((btn) => btn.addEventListener("click", () => {
    activeForm = btn.closest("form");
    paintDots(btn.dataset.itemId);
    dialog.showModal();
  }));

  dialog.querySelectorAll('circle[data-site]').forEach((circle) => circle.addEventListener("click", () => {
    if (circle.classList.contains("site-dot-ineligible") || !activeForm) return;
    const input = activeForm.querySelector("[data-site-input]");
    if (input) input.value = circle.dataset.site;
    dialog.close();
  }));

  dialog.querySelectorAll('[data-action="close-site"]').forEach((btn) => btn.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
})();
```

Add `<script src="{{ static_url('js/dosing.js') }}" defer></script>` to `today.html`'s
`{% block scripts %}` (add one if the template doesn't have one yet, matching the pattern in
`app/templates/inventory/detail.html`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_dosing.py -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass except the still-known Task-1 calculator/reconstitute failures.

- [ ] **Step 8: Commit**

```bash
git add app/templates/dosing/today.html app/routers/dosing.py app/static/js/dosing.js tests/test_dosing.py
git commit -m "feat: add injection-site picker to the Today view"
```

---

### Task 5: Missed-dose catch-up and Protocol page history

**Files:**
- Modify: `app/routers/protocols.py`
- Modify: `app/templates/protocols/builder.html`
- Modify: `tests/test_dosing.py` (or `tests/test_protocols.py` — this task adds to whichever file
  already covers `edit_protocol`/builder-page tests; check both, follow the existing convention)

**Interfaces:**
- Consumes: `missed_items` from Task 2; `/today/log` from Task 3 (reused for catch-up, passing a
  past `scheduled_date`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dosing.py`:

```python
def test_protocol_page_shows_dose_history(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    t = html.unescape(client.get(f"/protocols/{protocol_id}/edit").text)
    assert "Dose history" in t
    assert "On time" in t


def test_protocol_page_shows_catch_up_for_missed_dose(client, db):
    from app.models import Frequency
    with SessionLocal() as s:
        uid = s.scalar(select(User.id))
        peptide = Peptide(name="Retatrutide")
        s.add(peptide)
        s.flush()
        protocol = Protocol(name="Fat Loss", start_date=date(2020, 1, 1), owner_id=uid)
        s.add(protocol)
        s.flush()
        pitem = ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                             frequency=Frequency.DAILY, route=Route.SUBQ)
        s.add(pitem)
        s.commit()
        protocol_id = protocol.id
    t = html.unescape(client.get(f"/protocols/{protocol_id}/edit").text)
    assert 'data-action="log-dose"' in t  # a catch-up log action for a long-overdue day appears
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dosing.py -k "dose_history or catch_up" -v`
Expected: FAIL — no "Dose history" text, no catch-up markup.

- [ ] **Step 3: Add history/missed data to the edit route**

In `app/routers/protocols.py`'s `edit_protocol` route, add dose history and missed items to the
context `_render_builder` passes through. Read `_render_builder`'s current signature and body
first (it takes `state`, `errors`, `protocol`, `repeat_of`, `today`, `status_code`) — add two more
optional keyword args, `dose_history` and `missed`, defaulting to `None`, and include them in the
returned context dict (`"dose_history": dose_history or [], "missed": missed or []`). Update
`edit_protocol` to compute and pass them:

```python
@router.get("/protocols/{protocol_id}/edit")
def edit_protocol(protocol_id: int, request: Request, session: Session = Depends(get_session),
                  today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    p = _get_protocol(session, protocol_id, uid)
    dose_history = session.scalars(
        select(DoseLog).where(DoseLog.protocol_id == p.id).order_by(DoseLog.scheduled_date.desc())).all()
    occs = occurrences([p], p.start_date, today)
    logged = {(dl.protocol_item_id, dl.scheduled_date) for dl in dose_history if dl.protocol_item_id is not None}
    missed = missed_items(occs, logged, today)
    return _render_builder(request, session, state_from_protocol(p), protocol=p, today=today,
                           dose_history=dose_history, missed=missed)
```

Add the needed imports at the top of `app/routers/protocols.py`: `from app.calendar.schedule import
missed_items, occurrences` and `from app.models import DoseLog` (add `DoseLog` to whatever existing
`from app.models import (...)` line is already there rather than a second import line).

- [ ] **Step 4: Add the template sections**

In `app/templates/protocols/builder.html`, add a "Dose history" section right after the closing
`</div>` of `.page-head` (before `<form id="builder-form">`), gated on `{% if protocol %}` (never
shown for a brand-new/repeat-of protocol that hasn't been saved yet):

```html
{% if protocol %}
<section aria-labelledby="dose-history-heading">
  <h2 id="dose-history-heading" class="section-title">Dose history</h2>
  {% if missed %}
  <div class="table-wrap">
  <table class="inv-table">
    <thead><tr><th>Date</th><th>Peptide</th><th>Dose</th><th></th></tr></thead>
    <tbody>
      {% for occ, item in missed %}
      <tr>
        <td>{{ occ.date.strftime('%b %-d') }}</td>
        <td>{{ item.peptide }}</td>
        <td>{{ item.dose }} {{ item.unit }}</td>
        <td>
          <form method="post" action="/today/log" class="inline">
            <input type="hidden" name="protocol_id" value="{{ protocol.id }}">
            <input type="hidden" name="protocol_item_id" value="{{ item.protocol_item_id }}">
            <input type="hidden" name="scheduled_date" value="{{ occ.date.isoformat() }}">
            <button type="submit" class="btn btn-ghost" data-action="log-dose">Log now (late)</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% endif %}
  {% if dose_history %}
  <div class="table-wrap">
  <table class="inv-table">
    <thead><tr><th>Date</th><th>Peptide</th><th>Dose</th><th>Status</th><th>Site</th></tr></thead>
    <tbody>
      {% for log in dose_history %}
      <tr>
        <td>{{ log.scheduled_date.strftime('%b %-d, %Y') }}</td>
        <td>{{ log.peptide_name }}</td>
        <td>{{ log.dose_value }} {{ log.dose_unit.value if log.dose_value is not none else '' }}</td>
        <td><span class="tag status-{{ log.status.value }}">{{ log.status.label }}</span></td>
        <td>{{ log.injection_site.label if log.injection_site else '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="muted">No doses logged yet.</p>
  {% endif %}
</section>
{% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dosing.py -k "dose_history or catch_up" -v`
Expected: All PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass except the still-known Task-1 calculator/reconstitute failures.

- [ ] **Step 7: Commit**

```bash
git add app/routers/protocols.py app/templates/protocols/builder.html tests/test_dosing.py
git commit -m "feat: show dose history and missed-dose catch-up on the Protocol page"
```

---

### Task 6: Calendar adherence color dots

**Files:**
- Modify: `app/routers/calendar.py`
- Modify: `app/templates/calendar/_month.html`
- Modify: `app/templates/calendar/_week.html`
- Modify: `app/templates/calendar/_day.html`
- Modify: `tests/test_dosing.py` (or `tests/test_calendar.py` if this repo has one — check first
  and follow whichever file already covers `calendar_page` tests)

**Interfaces:**
- Consumes: `DoseLog`, `missed_items` from Tasks 1-2.

Adherence status is computed per (protocol_id, date) occurrence, aggregated across every item due
that day: all logged on-time → `on_time`; any logged late → `late`; any missed or skipped → `missed`
(the spec bundles Missed and Skipped into the same red display color — see the spec's Adherence
display section); nothing logged yet and the date hasn't happened → `upcoming`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dosing.py`:

```python
def test_calendar_shows_on_time_adherence_dot(client, db):
    from app.models import Frequency
    protocol_id, pitem_id, vial_id = _setup_protocol_with_vial(client, db)
    client.post("/today/log", data={
        "protocol_id": str(protocol_id), "protocol_item_id": str(pitem_id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=False)
    r = client.get("/calendar")
    assert r.status_code == 200
    assert "adherence-on_time" in r.text or '"on_time"' in r.text


def test_calendar_shows_missed_adherence_for_past_unlogged_day(client, db):
    from app.models import Frequency
    with SessionLocal() as s:
        uid = s.scalar(select(User.id))
        peptide = Peptide(name="Retatrutide")
        s.add(peptide)
        s.flush()
        protocol = Protocol(name="Fat Loss", start_date=date(2020, 1, 1), owner_id=uid)
        s.add(protocol)
        s.flush()
        s.add(ProtocolItem(protocol_id=protocol.id, peptide_id=peptide.id, dose=2.0, dose_unit=DoseUnit.MG,
                           frequency=Frequency.DAILY, route=Route.SUBQ))
        s.commit()
    r = client.get(f"/calendar?view=day&date={(date.today() - timedelta(days=1)).isoformat()}")
    assert r.status_code == 200
    assert "adherence-missed" in r.text or '"missed"' in r.text
```

Add `from datetime import timedelta` to the test file's imports if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dosing.py -k "calendar_shows" -v`
Expected: FAIL — no adherence markers present in the response yet.

- [ ] **Step 3: Compute adherence in `calendar_page`**

In `app/routers/calendar.py`, add an `_adherence` helper and wire it into `calendar_page`:

```python
def _adherence(session: Session, uid: int, occs: list[Occurrence], today: date) -> dict[str, str]:
    """One of 'on_time' | 'late' | 'missed' | 'upcoming' per '{protocol_id}|{date}' key -- an
    aggregate across every item due that occurrence, per the spec's calendar-color rules."""
    protocol_ids = {o.protocol_id for o in occs}
    if not protocol_ids:
        return {}
    logs = session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.protocol_id.in_(protocol_ids))).all()
    by_key: dict[tuple[int, date], list[DoseLog]] = {}
    for log in logs:
        by_key.setdefault((log.protocol_id, log.scheduled_date), []).append(log)

    result = {}
    for occ in occs:
        key = (occ.protocol_id, occ.date)
        day_logs = by_key.get(key, [])
        if not day_logs:
            result[f"{occ.protocol_id}|{occ.date.isoformat()}"] = "missed" if occ.date < today else "upcoming"
        elif any(l.status == DoseStatus.MISSED or l.status == DoseStatus.SKIPPED for l in day_logs):
            result[f"{occ.protocol_id}|{occ.date.isoformat()}"] = "missed"
        elif any(l.status == DoseStatus.LATE for l in day_logs):
            result[f"{occ.protocol_id}|{occ.date.isoformat()}"] = "late"
        else:
            result[f"{occ.protocol_id}|{occ.date.isoformat()}"] = "on_time"
    return result
```

Add `DoseStatus, DoseLog` to the `from app.models import (...)` line at the top of the file.

In `calendar_page`, after `occs = occurrences(protocols, first, last)`, add:

```python
    adherence = _adherence(session, uid, occs, today)
```

and add `"adherence": adherence` to the `ctx |= {...}` dict right after it (the same dict that
already sets `"title"`, `"prev_url"`, etc.).

- [ ] **Step 4: Render the dots**

In `app/templates/calendar/_month.html`, add a dot span inside each `cal-bar-day` button (the
per-date button inside a protocol's bar):

```html
      {% for d in b.dates %}<button type="button" class="cal-bar-day" data-key="{{ b.protocol_id }}|{{ d.isoformat() }}"
        aria-label="{{ b.name }}, {{ d.strftime('%B') }} {{ d.day }}"><span class="adherence-dot adherence-{{ adherence.get((b.protocol_id | string) ~ '|' ~ d.isoformat(), 'upcoming') }}"></span></button>{% endfor %}
```

(replacing the existing empty `<button ...></button>` — everything else about that line is
unchanged, just the added `<span>` inside it.)

In `app/templates/calendar/_week.html`, add the same span inside the `cal-block` button:

```html
    {% for entries in cells[key] %}
    <div class="cal-week-cell {{ 'cal-today' if days[loop.index0] == today }}">
      {% for occ, items in entries %}<button type="button" class="cal-block c{{ colors.get(occ.protocol_id, 0) }}" data-key="{{ occ.protocol_id }}|{{ occ.date.isoformat() }}"><span class="adherence-dot adherence-{{ adherence.get((occ.protocol_id | string) ~ '|' ~ occ.date.isoformat(), 'upcoming') }}"></span><strong>{{ occ.protocol_name }}</strong><span>{{ items | length }} dose{{ '' if items | length == 1 else 's' }}</span></button>{% endfor %}
    </div>
    {% endfor %}
```

Read `app/templates/calendar/_day.html` first (not yet read in this plan's research) and apply the
same pattern — find wherever it renders one button/block per occurrence and add an
`adherence-dot` span the same way, keyed identically (`"{{ occ.protocol_id }}|{{ occ.date.isoformat() }}"`).

Add minimal CSS to `app/static/css/app.css` (append near other `.cal-*` rules — search for
`.cal-bar` to find them):

```css
.adherence-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
.adherence-on_time { background: #2e7d32; }
.adherence-late { background: #f9a825; }
.adherence-missed { background: #c62828; }
.adherence-upcoming { background: #90a4ae; }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dosing.py -k "calendar_shows" -v`
Expected: All PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass except the still-known Task-1 calculator/reconstitute failures.

- [ ] **Step 7: Commit**

```bash
git add app/routers/calendar.py app/templates/calendar/_month.html app/templates/calendar/_week.html app/templates/calendar/_day.html app/static/css/app.css tests/test_dosing.py
git commit -m "feat: color-code Calendar occurrences by adherence status"
```

---

### Task 7: Pen support and reconstitution wiring

**Files:**
- Modify: `app/routers/calculator.py`
- Modify: `app/templates/calculator/calculator.html`
- Modify: `app/routers/inventory.py`
- Modify: `app/templates/inventory/list.html`
- Modify: `app/static/js/inventory.js`
- Modify: `tests/test_active_vials.py`
- Modify: `tests/test_calculator_page.py` (fixes the failures Task 1's Step 9 flagged)

**Interfaces:**
- Consumes: `ActiveVial.dispensing_method`/`.volume_remaining_ml` from Task 1.

This task fixes the `reconstitute` route to actually set `volume_remaining_ml` (required, not
nullable, since Task 1) and `dispensing_method` (from a new "Load into a peptide pen?" question),
and adds a "Convert to peptide pen" action for vials that answered no. It also updates the Today
view's dose-logging wording (Task 3's route/template) to say "Dial the pen to" instead of "Draw to"
when the vial's `dispensing_method` is `PEN`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_active_vials.py` (fixing/extending whatever the file's existing
`reconstitute`-flow tests already look like — read them first, since Task 1 broke them by making
`volume_remaining_ml` required; this task's job is to make the route itself set it):

```python
def test_reconstitute_sets_volume_remaining_and_defaults_to_syringe(client, db):
    item_id = _create_item("Retatrutide", category="Medicine", medium="Lyophilized", vial_size_mg=10,
                           quantity=1, arrival_date=date(2026, 8, 10))
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "2", "dose_unit": "mg",
        "discard_by": "2026-10-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        vial = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == item_id))
        assert vial.volume_remaining_ml == 2.0
        assert vial.dispensing_method.value == "syringe"


def test_reconstitute_with_pen_question_answered_yes(client, db):
    item_id = _create_item("Retatrutide", category="Medicine", medium="Lyophilized", vial_size_mg=10,
                           quantity=1, arrival_date=date(2026, 8, 10))
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "2", "dose_unit": "mg",
        "discard_by": "2026-10-01", "load_into_pen": "1",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        vial = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == item_id))
        assert vial.dispensing_method.value == "pen"


def test_convert_vial_to_pen_later(client, db):
    item_id = _create_item("Retatrutide", category="Medicine", medium="Lyophilized", vial_size_mg=10,
                           quantity=1, arrival_date=date(2026, 8, 10))
    client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "2", "dose_unit": "mg",
        "discard_by": "2026-10-01",
    })
    with SessionLocal() as s:
        vial_id = s.scalar(select(ActiveVial.id).where(ActiveVial.inventory_item_id == item_id))
    r = client.post(f"/active-vials/{vial_id}/convert-to-pen", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).dispensing_method.value == "pen"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_active_vials.py -k "reconstitute_sets_volume or pen_question or convert_vial" -v`
Expected: FAIL — `volume_remaining_ml` not set (IntegrityError/NOT NULL), `load_into_pen` ignored,
`/active-vials/{id}/convert-to-pen` 404s.

- [ ] **Step 3: Fix `reconstitute` and add the convert route**

In `app/routers/calculator.py`'s `reconstitute` route, update the `ActiveVial(...)` construction:

```python
    load_into_pen = bool(str(form.get("load_into_pen", "")).strip())
    session.add(ActiveVial(
        owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=result.concentration_mg_ml,
        water_ml=water_ml, dose_value=dose_value, dose_unit=DoseUnit(dose_unit),
        doses_total=result.doses_per_vial, date_mixed=date.today(), discard_by=discard_by,
        volume_remaining_ml=water_ml,
        dispensing_method=DispensingMethod.PEN if load_into_pen else DispensingMethod.SYRINGE,
    ))
```

Add `DispensingMethod` to the `from app.models import (...)` line at the top of the file.

In `app/routers/inventory.py`, add the convert route directly after `discard_active_vial`:

```python
@router.post("/active-vials/{vial_id}/convert-to-pen")
def convert_vial_to_pen(vial_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    vial = _own_active_vial(session, vial_id, uid)
    if vial is None:
        raise HTTPException(404)
    vial.dispensing_method = DispensingMethod.PEN
    session.commit()
    return RedirectResponse("/inventory#active-vials", status_code=303)
```

Add `DispensingMethod` to `app/routers/inventory.py`'s `from app.models import (...)` line.

- [ ] **Step 4: Add the pen question to the Calculator template, and a Convert action to the Active Vials list**

In `app/templates/calculator/calculator.html`, find the reconstitution form (the one posting to
`/calculator/reconstitute`) and add, right before its submit button:

```html
      <label class="field checkbox-field">
        <input type="checkbox" name="load_into_pen" value="1">
        <span>Load into a peptide pen?</span>
      </label>
```

In `app/templates/inventory/list.html`'s Active Vials card (find the existing per-vial "Discard"
form in that section), add a Convert action for syringe-dispensed vials:

```html
      {% if v.dispensing_method.value == 'syringe' %}
      <form method="post" action="/active-vials/{{ v.id }}/convert-to-pen" class="inline">
        <button type="submit" class="btn btn-ghost">Convert to peptide pen</button>
      </form>
      {% endif %}
```

(Place this alongside the vial's existing Discard button, inside the same per-vial card markup —
find the exact insertion point by reading the Active Vials section of `list.html` first, since its
current structure wasn't part of this plan's own research and needs a direct read before editing.)

- [ ] **Step 5: Update Today view wording for pen-dispensed vials**

In `app/templates/dosing/today.html`, the dose-amount display doesn't currently show a computed
mL amount at all (Task 3 kept it simple — just `item.dose`/`item.unit`). Add the computed-volume
line, worded by dispensing method, by having `app/routers/dosing.py`'s `today_page` also resolve
each item's open vial (reusing `_open_vial_for_item`) and compute its volume, then pass a
`volume_text` dict alongside `site_data`. `DueItem` only carries a display `inventory` name string,
not the actual `inventory_item_id`, so resolve it from the `ProtocolItem` instead (`protocols` is
already in scope in `today_page` from the query at the top of the function). Add, in the same loop
that builds `site_data`:

```python
    volume_text = {}
    protocol_items_by_id = {
        pi.id: pi for p in protocols for pi in p.items
    }
    for occ, item in due:
        pi = protocol_items_by_id.get(item.protocol_item_id)
        if pi is None or pi.inventory_item_id is None:
            continue
        vial = _open_vial_for_item(session, pi.inventory_item_id)
        if vial is None:
            continue
        vol = _dose_volume_ml(pi.dose, pi.dose_unit, vial)
        if vol is None:
            continue
        verb = "Dial the pen to" if vial.dispensing_method.value == "pen" else "Draw to"
        volume_text[item.protocol_item_id] = f"{verb} {vol:.2f} mL"
```

Add `"volume_text": volume_text` to the returned context dict. In `today.html`, add a line under
the dose cell:

```html
      <td>{{ item.dose }} {{ item.unit }}{% if item.protocol_item_id in volume_text %}<div class="muted small">{{ volume_text[item.protocol_item_id] }}</div>{% endif %}</td>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_active_vials.py tests/test_calculator_page.py tests/test_dosing.py -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass — this is the task that fixes the Task-1-flagged calculator/reconstitute
breakage, so this should be the point the ENTIRE suite is fully green again with no known
exceptions.

- [ ] **Step 8: Commit**

```bash
git add app/routers/calculator.py app/templates/calculator/calculator.html app/routers/inventory.py app/templates/inventory/list.html app/static/js/inventory.js app/routers/dosing.py app/templates/dosing/today.html tests/test_active_vials.py tests/test_calculator_page.py
git commit -m "feat: peptide-pen loading at reconstitution, convert-later action, dose-logging wording"
```

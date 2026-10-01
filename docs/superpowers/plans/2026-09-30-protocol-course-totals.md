# Protocol Course Totals Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An icon on every saved protocol (any status) opens a popup showing each item's total course quantity (its own unit, plus vial/BAC-water estimates where computable), reusing the now-correct cycle-off-aware schedule.

**Architecture:** A new per-`InventoryItem` "purchasing unit" field (individual vial / kit of 10) drives an as-needed item's flat course quantity. A new, dependency-free `app/protocols/course_totals.py` module walks each scheduled item's full planned date range with the existing `is_due()`/`current_step()` functions and sums doses, converting to vial/BAC-water estimates where the unit and a linked inventory item allow it. The Protocols list page embeds every shown protocol's computed totals as one JSON blob (mirroring the Calendar page's own `cal-data` pattern) and a small JS/CSS addition renders the icon and populates a shared dialog on click — no new endpoint, no AJAX round trip.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Jinja2, vanilla JS (no build step), pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-protocol-course-totals-design.md`

## Global Constraints

- BAC water is a flat **1.5 mL per vial** assumption, applied uniformly to every vial of an item across its course — never editable, never read from reconstitution history.
- Any positive remainder of a vial count rounds up to the next whole vial, via `math.ceil(raw_vials - 1e-9)` — the `1e-9` epsilon only guards float noise at an exact whole-vial boundary; it must never mask a genuine fractional vial.
- As-needed items get a flat course quantity: 1 vial (`PurchasingUnit.INDIVIDUAL`, also the default when nothing is linked) or 10 vials (`PurchasingUnit.KIT_OF_10`) — never a computed dose-based total.
- IU-dosed items, items with no linked `InventoryItem`, and items whose linked `InventoryItem` has no usable vial size each show a total-only result with `vials_estimate=None`/`bac_water_ml=None` and an explanatory `note` — never an error.
- The course-totals calculation sums the full **planned** course (`protocol.start_date` to `protocol.end_date` inclusive), deliberately ignoring `protocol.paused`/`protocol.ended_on` — it must not reuse `protocol_window()`, which gates on those.
- A protocol with no `end_date` has no course to total — the popup says so instead of computing anything.
- `Category.SUPPLY` and `Category.BAC_WATER` inventory items never show the Purchasing Unit field and always get `PurchasingUnit.INDIVIDUAL` by default — it is Medicine-only, mirroring exactly how `medium`/`vial_size_mg` are already Medicine-only.

## Review Focus

- A protocol with no `end_date` must produce one clear message for the whole popup, not a per-item error or a 500 — exercised in Task 4 and Task 6.
- An as-needed item with no linked `InventoryItem` at all must default cleanly to "1 vial," not crash on a `None` lookup — exercised in Task 4.
- A scheduled item whose summed dose comes out to an exact whole number of vials (e.g. exactly 2.0) must not round up to 3 — exercised in Task 4, pinning the `1e-9` epsilon's own boundary.
- An item with both titration steps and a cycle-off must sum correctly across the whole course (some weeks off entirely, others at a stepped-up dose) by consuming `is_due()`/`current_step()` as-is, not reimplementing or second-guessing them — exercised in Task 4.
- A `Category.SUPPLY` item linked to a protocol item (unusual, but not prevented elsewhere in the app) must default to `PurchasingUnit.INDIVIDUAL` cleanly rather than erroring for lack of the field ever being set — exercised in Task 2.

---

### Task 1: `PurchasingUnit` enum + `InventoryItem.purchasing_unit` column

**Files:**
- Modify: `app/models.py` (add `PurchasingUnit` enum near `Medium`; add column to `InventoryItem`)
- Create: `migrations/versions/0028_purchasing_unit.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `PurchasingUnit` enum (`INDIVIDUAL`/`KIT_OF_10`, a `LabeledEnum`); `InventoryItem.purchasing_unit: Mapped[PurchasingUnit]`, default `PurchasingUnit.INDIVIDUAL`.

- [ ] **Step 1: Add the enum and column**

In `app/models.py`, immediately before the `class InventoryItem(Base):` line (around line 258), add:

```python
class PurchasingUnit(LabeledEnum):
    INDIVIDUAL = ("individual", "Individual vial")
    KIT_OF_10 = ("kit_of_10", "Kit of 10 vials")
```

Then add this column to `InventoryItem`, immediately after its existing `vial_size_unit` column (around line 276):

```python
    # Only meaningful for Category.MEDICINE (same gate vial_size_mg/medium already use) -- drives
    # an as-needed protocol item's flat course-total quantity (1 vial vs. a 10-vial kit).
    purchasing_unit: Mapped["PurchasingUnit"] = mapped_column(_enum_column(PurchasingUnit), default=PurchasingUnit.INDIVIDUAL)
```

- [ ] **Step 2: Write the migration**

Create `migrations/versions/0028_purchasing_unit.py`:

```python
"""inventory_items.purchasing_unit: individual vial vs. a 10-vial kit, for as-needed course totals

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0028'
down_revision: Union[str, None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'purchasing_unit', sa.String(length=20), nullable=False, server_default='individual'))


def downgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_column('purchasing_unit')
```

- [ ] **Step 3: Write the failing test**

Add to `tests/test_migrations.py`, following the exact style of its most recent migration test
(read `test_0027_adds_protocol_item_cycle_offs` first and mirror its `_cfg`/`sqlite3.connect`
pattern):

```python
def test_0028_adds_purchasing_unit(tmp_path):
    db = tmp_path / "o.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0027")
    with sqlite3.connect(db) as c:
        c.execute("insert into inventory_items(name, count, created_at, updated_at) "
                  "values ('Old', 2, '2026-09-30', '2026-09-30')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert "purchasing_unit" in cols
        # Pre-existing row backfilled to the default, not left NULL.
        assert c.execute("select purchasing_unit from inventory_items where name='Old'").fetchone()[0] == "individual"
    command.downgrade(cfg, "0027")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
    assert "purchasing_unit" not in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_migrations.py -k 0028 -v`
Expected: FAIL — table has no `purchasing_unit` column yet (migration/model not added).

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_migrations.py -v`
Expected: all pass, including the new test.

- [ ] **Step 5: Commit**

```bash
git add app/models.py migrations/versions/0028_purchasing_unit.py tests/test_migrations.py
git commit -m "feat: add PurchasingUnit enum and InventoryItem.purchasing_unit column"
```

---

### Task 2: Inventory add/edit wiring for Purchasing Unit

**Files:**
- Modify: `app/routers/inventory.py` (`ITEM_FIELDS`, `_parse_item_fields`, `_form_values`, `list_items`'s context, `_detail_context`)
- Modify: `app/templates/inventory/list.html` (add/edit dialog form)
- Modify: `app/templates/inventory/detail.html` (edit form)
- Modify: `app/static/js/inventory.js` (`fields` hydration array)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `PurchasingUnit` from Task 1.
- Produces: nothing new for later tasks — Task 4's calculation module reads `InventoryItem.purchasing_unit` directly off the model, not through any inventory-router helper.

- [ ] **Step 1: Write the failing tests**

Find this file's existing item-creation test helper (search `tests/test_inventory.py` for a
`def test_create_` or similar that POSTs to `/inventory` with a Medicine item) and mirror its exact
field set. Add:

```python
def test_purchasing_unit_defaults_to_individual(client, db):
    from app.models import InventoryItem
    client.post("/inventory", data={
        "name": "BPC-157", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
    })
    item = db.scalar(select(InventoryItem).where(InventoryItem.name == "BPC-157"))
    assert item.purchasing_unit.value == "individual"


def test_purchasing_unit_can_be_set_to_kit_of_10(client, db):
    from app.models import InventoryItem
    client.post("/inventory", data={
        "name": "Tirzepatide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "purchasing_unit": "kit_of_10",
    })
    item = db.scalar(select(InventoryItem).where(InventoryItem.name == "Tirzepatide"))
    assert item.purchasing_unit.value == "kit_of_10"


def test_purchasing_unit_round_trips_on_edit(client, db):
    from app.models import InventoryItem
    client.post("/inventory", data={
        "name": "Semaglutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "5",
    })
    item = db.scalar(select(InventoryItem).where(InventoryItem.name == "Semaglutide"))
    client.post(f"/inventory/{item.id}", data={
        "name": "Semaglutide", "medium": "Lyophilized", "vial_size_mg": "5", "purchasing_unit": "kit_of_10",
    })
    db.expire_all()
    assert item.purchasing_unit.value == "kit_of_10"


def test_supply_item_always_defaults_purchasing_unit_to_individual(client, db):
    from app.models import InventoryItem
    client.post("/inventory", data={"name": "Syringes", "category": "Supply", "count": "50"})
    item = db.scalar(select(InventoryItem).where(InventoryItem.name == "Syringes"))
    assert item.purchasing_unit.value == "individual"
```

(Match the exact existing field names/required fields this file's other Medicine-item creation
tests already use — e.g. whether `category` is submitted as `"Medicine"` or something else, and
whether a `count`/`vendor` field is mandatory — read a neighboring test in `tests/test_inventory.py`
first rather than guessing.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_inventory.py -k purchasing_unit -v`
Expected: FAIL — `item.purchasing_unit` doesn't reflect the submitted value yet (parsing not wired),
though the column itself already exists from Task 1 and defaults correctly on its own (the first
and fourth tests may already pass by coincidence of the DB default — the second and third must
fail).

- [ ] **Step 3: Implement**

In `app/routers/inventory.py`:

Import `PurchasingUnit` alongside the existing `Medium`/`DoseUnit` import (near the top, in the
`from app.models import (...)` block).

Add `"purchasing_unit"` to `ITEM_FIELDS` (line 28), right after `"vial_size_unit"`:
```python
ITEM_FIELDS = ("name", "category", "count", "vial_size_mg", "vial_size_unit", "purchasing_unit", "medium",
              "volume_ml", "units_per_package", "storage", "low_stock_threshold", "cost", "vendor", "notes")
```

In `_parse_item_fields` (around line 118), the `Category.SUPPLY` branch (around line 143-160) gets
one more default line, right after `values["vial_size_unit"] = DoseUnit.MG` (line 157):
```python
        values["purchasing_unit"] = PurchasingUnit.INDIVIDUAL
```

The `Category.MEDICINE` branch (around line 169-184) gets a parsed value, right after
`values["vial_size_unit"] = ...` (line 172):
```python
        values["purchasing_unit"] = _parse_choice(PurchasingUnit, raw["purchasing_unit"],
                                                   PurchasingUnit.INDIVIDUAL, "purchasing_unit", errors)
```

The `else` (`BAC_WATER`) branch (around line 185-190) gets the same default as Supply, right after
its own `values["vial_size_unit"] = DoseUnit.MG` (line 188):
```python
        values["purchasing_unit"] = PurchasingUnit.INDIVIDUAL
```

In `_form_values` (around line 479-499), add one line right after `"vial_size_unit"` (line 490):
```python
        "purchasing_unit": item.purchasing_unit.value,
```

In `list_items`'s returned context dict (the one containing `"mediums": list(Medium),` around line
676), add, right after that line:
```python
            "purchasing_units": list(PurchasingUnit),
```

In `_detail_context` (the one containing `"mediums": list(Medium),` around line 609), add the same
line right after it:
```python
        "purchasing_units": list(PurchasingUnit),
```

- [ ] **Step 4: Add the form fields**

In `app/templates/inventory/list.html`, immediately after the existing `vial_size_mg` field block
(ends around line 211, right before the `volume_ml` label), add:
```html
        <label {{ cls('purchasing_unit') }} data-field="purchasing_unit" data-field-group="medium-only">
          <span>Purchasing unit</span>
          <select name="purchasing_unit">
            {% for u in purchasing_units %}<option value="{{ u.value }}" {{ 'selected' if f.purchasing_unit == u.value }}>{{ u.label }}</option>{% endfor %}
          </select>
          {{ err('purchasing_unit') }}
        </label>
```

In `app/templates/inventory/detail.html`, immediately after the existing `vial_size_mg` field block
(ends around line 76, right before the `volume_ml` label at line 78), add:
```html
      <label {{ icls('purchasing_unit') }} data-field="purchasing_unit">
        <span>Purchasing unit</span>
        <select name="purchasing_unit">
          {% for u in purchasing_units %}<option value="{{ u.value }}" {{ 'selected' if ef.purchasing_unit == u.value }}>{{ u.label }}</option>{% endfor %}
        </select>
        {{ ierr('purchasing_unit') }}
      </label>
```

In `app/static/js/inventory.js`, add `"purchasing_unit"` to the `fields` array (line 10-12):
```javascript
  const fields = [
    "name", "category", "count", "vial_size_mg", "vial_size_unit", "purchasing_unit", "medium", "volume_ml",
    "units_per_package", "storage", "cost", "vendor", "notes",
  ];
```

And extend `openFor`'s per-field default (line 76, the line starting `form.elements[f].value = ...`)
so a brand-new item's dialog defaults this select to "individual" the same way `vial_size_unit`
already defaults to "mg":
```javascript
      form.elements[f].value = item ? item[f] ?? "" : f === "count" ? "1" : f === "vial_size_unit" ? "mg" : f === "purchasing_unit" ? "individual" : "";
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_inventory.py -v`
Expected: all pass, including the four new tests.

- [ ] **Step 6: Run the full suite to confirm nothing else broke**

Run: `py -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/inventory.py app/templates/inventory/list.html app/templates/inventory/detail.html app/static/js/inventory.js tests/test_inventory.py
git commit -m "feat: wire Purchasing Unit into the Inventory add/edit forms"
```

---

### Task 3: Backup export/import for Purchasing Unit

**Files:**
- Modify: `app/routers/backup.py` (`_inventory_row`, `_import_inventory_row`)
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: `PurchasingUnit` from Task 1.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backup.py` (mirror `test_backup_round_trips_cycle_offs`'s exact pattern: create,
export, delete the original, import, assert on the re-created row):

```python
def test_backup_round_trips_purchasing_unit(client, db):
    from app.models import InventoryItem
    client.post("/inventory", data={
        "name": "Kit Item", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "purchasing_unit": "kit_of_10",
    })
    export = client.get("/backup/export.json").json()
    with SessionLocal() as s:
        s.query(InventoryItem).filter_by(name="Kit Item").delete()
        s.commit()
    client.post("/backup/import", files={"file": ("backup.json", json.dumps(export), "application/json")})
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Kit Item"))
        assert item.purchasing_unit.value == "kit_of_10"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_backup.py -k purchasing_unit -v`
Expected: FAIL — the re-imported item's `purchasing_unit` is the default (`"individual"`), not the
exported `"kit_of_10"`, since `_inventory_row`/`_import_inventory_row` don't handle the field yet.

- [ ] **Step 3: Implement**

In `app/routers/backup.py`, import `PurchasingUnit` alongside the existing `Medium`/`DoseUnit`
import. Add to `_inventory_row` (around line 35-45), right after the `"vial_size_unit"` line:
```python
        "purchasing_unit": i.purchasing_unit.value,
```

Add to `_import_inventory_row`'s `InventoryItem(...)` construction (around line 169-178), right
after its own `vial_size_unit=...` line:
```python
        purchasing_unit=PurchasingUnit(row.get("purchasing_unit") or "individual"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_backup.py -v`
Expected: all pass, including the new test.

- [ ] **Step 5: Commit**

```bash
git add app/routers/backup.py tests/test_backup.py
git commit -m "feat: back up and restore InventoryItem.purchasing_unit"
```

---

### Task 4: Course totals calculation module

**Files:**
- Create: `app/protocols/course_totals.py`
- Test: `tests/test_course_totals.py`

**Interfaces:**
- Consumes: `is_due(item, start, day)` from `app/calendar/schedule.py`; `current_step(steps, week)`/`current_week(start, day)` from `app/protocols/status.py`; `_DOSE_UNITS_TO_MG` from `app/calculator/reconstitution.py`; `PurchasingUnit` from Task 1.
- Produces: `ItemTotal` dataclass (`peptide: str`, `unit: str`, `as_needed: bool`, `total_amount: float | None`, `vials_estimate: int | None`, `bac_water_ml: float | None`, `note: str | None`); `compute_course_totals(protocol, inventory_by_id: dict[int, InventoryItem]) -> list[ItemTotal] | None` (the module-level function Task 5 calls).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_course_totals.py`:

```python
from datetime import date
from types import SimpleNamespace as NS

from app.models import DoseUnit, Frequency, PurchasingUnit
from app.protocols.course_totals import compute_course_totals


def peptide_item(name="BPC-157", dose=250.0, unit=DoseUnit.MCG, freq=Frequency.DAILY, every_n=None,
                 weekdays=None, steps=(), cycle_offs=(), inventory_item_id=None):
    return NS(id=1, peptide=NS(name=name), dose=dose, dose_unit=unit, frequency=freq,
              every_n_days=every_n, weekdays=weekdays, steps=list(steps), cycle_offs=list(cycle_offs),
              inventory_item_id=inventory_item_id)


def proto(start=date(2026, 1, 1), end=date(2026, 1, 14), titration=False, items=None):
    return NS(start_date=start, end_date=end, titration_enabled=titration, items=items or [peptide_item()])


def inv(id=1, vial_size_mg=100.0, vial_size_unit=DoseUnit.MG, purchasing_unit=PurchasingUnit.INDIVIDUAL):
    return NS(id=id, vial_size_mg=vial_size_mg, vial_size_unit=vial_size_unit, purchasing_unit=purchasing_unit)


def test_no_end_date_means_no_totals():
    p = proto(end=None)
    assert compute_course_totals(p, {}) is None


def test_simple_daily_dose_sums_correctly_in_its_own_unit():
    # 14 days, 250 mcg/day = 3500 mcg total, no inventory item linked.
    p = proto(items=[peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=None)])
    [total] = compute_course_totals(p, {})
    assert total.unit == "mcg" and total.total_amount == 3500.0
    assert total.vials_estimate is None and "No inventory item linked" in total.note


def test_vial_estimate_rounds_up_any_positive_remainder():
    # 3500 mcg = 3.5 mg total dose; a 1 mg vial needs 3.5 vials -> rounds up to 4.
    p = proto(items=[peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=1)])
    inventory = {1: inv(vial_size_mg=1.0, vial_size_unit=DoseUnit.MG)}
    [total] = compute_course_totals(p, inventory)
    assert total.vials_estimate == 4
    assert total.bac_water_ml == 6.0  # 4 vials * 1.5 mL


def test_vial_estimate_does_not_round_up_an_exact_whole_number():
    # 14 days * 250 mcg = 3.5 mg; a 0.875 mg vial divides evenly into exactly 4 vials.
    p = proto(items=[peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=1)])
    inventory = {1: inv(vial_size_mg=0.875, vial_size_unit=DoseUnit.MG)}
    [total] = compute_course_totals(p, inventory)
    assert total.vials_estimate == 4


def test_iu_item_shows_total_but_skips_vial_math():
    p = proto(items=[peptide_item(dose=2.0, unit=DoseUnit.IU, inventory_item_id=1)])
    inventory = {1: inv()}
    [total] = compute_course_totals(p, inventory)
    assert total.unit == "IU" and total.total_amount == 28.0  # 14 days * 2 IU
    assert total.vials_estimate is None and "IU" in total.note


def test_as_needed_item_with_no_inventory_link_defaults_to_one_vial():
    p = proto(items=[peptide_item(freq=Frequency.AS_NEEDED, inventory_item_id=None)])
    [total] = compute_course_totals(p, {})
    assert total.as_needed and total.total_amount is None
    assert total.vials_estimate == 1 and "1 vial" in total.note


def test_as_needed_item_linked_to_a_kit_item_estimates_ten_vials():
    p = proto(items=[peptide_item(freq=Frequency.AS_NEEDED, inventory_item_id=1)])
    inventory = {1: inv(purchasing_unit=PurchasingUnit.KIT_OF_10)}
    [total] = compute_course_totals(p, inventory)
    assert total.vials_estimate == 10 and "kit" in total.note.lower()


def test_titration_and_cycle_off_both_apply_across_the_course():
    # Weeks 1-2 on (step 1: 100 mcg/day), week 3 off entirely, week 4 on at step 2 (200 mcg/day).
    # 14-day course starting Jan 1 2026 covers exactly weeks 1-2 (7 days each); extend to 28 days
    # to reach week 4.
    steps = [NS(start_week=1, end_week=2, dose=100.0), NS(start_week=4, end_week=None, dose=200.0)]
    offs = [NS(start_week=3, end_week=3)]
    p = proto(end=date(2026, 1, 28), titration=True,
              items=[peptide_item(dose=None, unit=DoseUnit.MCG, steps=steps, cycle_offs=offs)])
    [total] = compute_course_totals(p, {})
    # Weeks 1-2: 14 days * 100 = 1400. Week 3: 0 (cycled off). Week 4: 7 days * 200 = 1400.
    assert total.total_amount == 2800.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_course_totals.py -v`
Expected: FAIL on every test with `ModuleNotFoundError: No module named 'app.protocols.course_totals'`.

- [ ] **Step 3: Implement**

Create `app/protocols/course_totals.py`:

```python
"""Total course quantities for a protocol -- per item, the full planned-course dose total (and,
where computable, an estimated vial count + BAC water total), for the Protocols page's totals
popup. Deliberately ignores protocol.paused/protocol.ended_on: this is the full *planned* course,
not "how much is left" or "how much was actually used." Pure except for the one inventory lookup
dict callers pass in -- no database access of its own.
"""

import math
from dataclasses import dataclass
from datetime import timedelta

from app.calculator.reconstitution import _DOSE_UNITS_TO_MG
from app.calendar.schedule import is_due
from app.models import Frequency, PurchasingUnit
from app.protocols.status import current_step, current_week

_BAC_WATER_ML_PER_VIAL = 1.5


@dataclass
class ItemTotal:
    peptide: str
    unit: str
    as_needed: bool
    total_amount: float | None
    vials_estimate: int | None
    bac_water_ml: float | None
    note: str | None


def _days(first, last):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _effective_dose(item, start, day):
    if not item.steps:
        return item.dose
    step = current_step(item.steps, current_week(start, day))
    return step.dose if step is not None else item.dose


def _as_needed_total(item, inventory_by_id: dict) -> ItemTotal:
    inv = inventory_by_id.get(item.inventory_item_id) if item.inventory_item_id else None
    kit = inv is not None and inv.purchasing_unit == PurchasingUnit.KIT_OF_10
    vials = 10 if kit else 1
    note = "As needed — 1 kit (10 vials)" if kit else "As needed — 1 vial"
    return ItemTotal(peptide=item.peptide.name, unit=item.dose_unit.value, as_needed=True,
                     total_amount=None, vials_estimate=vials, bac_water_ml=vials * _BAC_WATER_ML_PER_VIAL,
                     note=note)


def _scheduled_total(item, protocol, inventory_by_id: dict) -> ItemTotal:
    total = 0.0
    for day in _days(protocol.start_date, protocol.end_date):
        if is_due(item, protocol.start_date, day):
            dose = _effective_dose(item, protocol.start_date, day)
            if dose is not None:
                total += dose

    unit = item.dose_unit.value
    inv = inventory_by_id.get(item.inventory_item_id) if item.inventory_item_id else None
    dose_factor = _DOSE_UNITS_TO_MG.get(unit)

    if inv is None:
        return ItemTotal(item.peptide.name, unit, False, total, None, None, "No inventory item linked")
    if dose_factor is None:
        return ItemTotal(item.peptide.name, unit, False, total, None, None, "IU — vial count not calculable")
    vial_factor = _DOSE_UNITS_TO_MG.get(inv.vial_size_unit.value)
    if not inv.vial_size_mg or vial_factor is None:
        return ItemTotal(item.peptide.name, unit, False, total, None, None, "Inventory item has no vial size set")

    total_mg = total * dose_factor
    vial_mg = inv.vial_size_mg * vial_factor
    vials_estimate = math.ceil(total_mg / vial_mg - 1e-9)
    bac_water_ml = vials_estimate * _BAC_WATER_ML_PER_VIAL
    return ItemTotal(item.peptide.name, unit, False, total, vials_estimate, bac_water_ml, None)


def compute_course_totals(protocol, inventory_by_id: dict) -> list[ItemTotal] | None:
    """None when `protocol.end_date` is None -- an ongoing protocol has no course to total."""
    if protocol.end_date is None:
        return None
    return [
        _as_needed_total(item, inventory_by_id) if item.frequency is Frequency.AS_NEEDED
        else _scheduled_total(item, protocol, inventory_by_id)
        for item in protocol.items
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m pytest tests/test_course_totals.py -v`
Expected: all 9 pass.

- [ ] **Step 5: Commit**

```bash
git add app/protocols/course_totals.py tests/test_course_totals.py
git commit -m "feat: add the course-totals calculation module"
```

---

### Task 5: Backend wiring on the Protocols list page

**Files:**
- Modify: `app/routers/protocols.py` (`list_protocols`)
- Test: `tests/test_protocols.py`

**Interfaces:**
- Consumes: `compute_course_totals` from Task 4.
- Produces: `course_totals` key in the template context Task 6's dialog JS reads — `{protocol_id: [{"peptide", "unit", "as_needed", "total_amount", "vials_estimate", "bac_water_ml", "note"}, ...] | None}` (one entry per protocol shown, `None` meaning "no end date").

- [ ] **Step 1: Write the failing test**

Add to `tests/test_protocols.py` (reuse `valid_form`/`peptide_id` exactly as Task 5 of the Cycle
On/Off plan's own tests already do in this same file):

```python
def test_list_page_embeds_course_totals_json(client, db):
    client.post("/protocols", data=valid_form(db))
    r = client.get("/protocols")
    assert r.status_code == 200
    assert 'id="course-totals-data"' in r.text
    assert '"Retatrutide"' in r.text  # valid_form's first item


def test_list_page_course_totals_is_none_without_an_end_date(client, db):
    client.post("/protocols", data=valid_form(db, end_date="", weeks=""))
    r = client.get("/protocols")
    assert r.status_code == 200
    import json, re
    m = re.search(r'id="course-totals-data"[^>]*>(.*?)</script>', r.text, re.S)
    data = json.loads(m.group(1))
    [(_, totals)] = data.items()
    assert totals is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_protocols.py -k course_totals -v`
Expected: FAIL — no `course-totals-data` script tag exists yet.

- [ ] **Step 3: Implement**

In `app/routers/protocols.py`, import `compute_course_totals` from `app.protocols.course_totals`
and `InventoryItem` (already imported) at the top. Update `list_protocols` (around line 114-137):

```python
@router.get("/protocols")
def list_protocols(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
        uid: int = Depends(current_user_id)):
    protocols = session.scalars(_protocol_query(uid).order_by(Protocol.created_at.desc(), Protocol.id.desc())).all()
    views = [_view(p, today) for p in protocols]
    # Active, Paused, and Scheduled protocols all get a card in the Active Protocols section (each
    # its own banner color) -- only Ended protocols drop to Saved. Within the section, cards group
    # by status in this fixed order, each group sorted by start date.
    active = []
    for status in (Status.ACTIVE, Status.PAUSED, Status.SCHEDULED):
        active += sorted((v for v in views if v["status"] is status), key=lambda v: v["p"].start_date)
    saved = [v for v in views if v["status"] is Status.ENDED]

    shared_protocols = session.scalars(
        _shared_protocol_query(uid).order_by(Protocol.created_at.desc(), Protocol.id.desc())).all()
    owner_ids = {p.owner_id for p in shared_protocols}
    owner_names = {}
    if owner_ids:
        owner_names = dict(session.execute(select(User.id, User.username).where(User.id.in_(owner_ids))).all())
    shared_views = [_view(p, today, owner_name=owner_names.get(p.owner_id)) for p in shared_protocols]

    inventory_by_id = {i.id: i for i in session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid))}
    course_totals = {
        v["p"].id: [
            {"peptide": t.peptide, "unit": t.unit, "as_needed": t.as_needed, "total_amount": t.total_amount,
             "vials_estimate": t.vials_estimate, "bac_water_ml": t.bac_water_ml, "note": t.note}
            for t in totals
        ] if (totals := compute_course_totals(v["p"], inventory_by_id)) is not None else None
        for v in views
    }

    return templates.TemplateResponse(request, "protocols/list.html",
                                      {"active_views": active, "saved_views": saved, "shared_views": shared_views,
                                       "goals": GOALS, "statuses": list(Status), "course_totals": course_totals})
```

Add the script tag to `app/templates/protocols/list.html`, right before the closing
`{% endblock %}` that precedes `{% block scripts %}` (after the `</div>` that closes `tab-shared`,
around line 180):
```html
<script type="application/json" id="course-totals-data">{{ course_totals | tojson }}</script>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m pytest tests/test_protocols.py -v`
Expected: all pass, including the two new tests.

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `py -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/routers/protocols.py app/templates/protocols/list.html tests/test_protocols.py
git commit -m "feat: embed per-protocol course totals on the Protocols list page"
```

---

### Task 6: Totals icon + popup UI

**Files:**
- Modify: `app/templates/protocols/list.html` (icon buttons, dialog markup)
- Modify: `app/static/js/protocols.js` (dialog open/populate)
- Modify: `app/static/css/app.css` (icon positioning, dialog table styling)
- Test: manual verification via an isolated TestClient render + Browser pane (no JS test harness in
  this repo, same verification approach already used for the Cycle On/Off builder UI)

**Interfaces:**
- Consumes: `course_totals` JSON blob from Task 5, keyed by protocol id (JS object keys from JSON
  are always strings, so look up with `String(protocolId)`).

- [ ] **Step 1: Add the icon buttons**

In `app/templates/protocols/list.html`, inside the Active protocols card (`<article
class="protocol-card" ...>`, around line 30-60), add an icon button as the last child before the
closing `</article>` (after the existing `<footer class="protocol-card-actions">...</footer>`,
around line 59):
```html
      <button type="button" class="btn-icon totals-btn" data-protocol-id="{{ p.id }}"
        aria-label="Total course quantities for {{ p.name }}" title="Total course quantities">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="2" width="16" height="20" rx="2"></rect><line x1="8" y1="7" x2="16" y2="7"></line><line x1="8" y1="11" x2="16" y2="11"></line><line x1="8" y1="15" x2="12" y2="15"></line></svg>
      </button>
```

In the Saved protocols table row's Actions `<td>` (around line 121-131), add the same button
inside that `<td>`, as its first child:
```html
          <td class="actions">
            <button type="button" class="btn-icon totals-btn" data-protocol-id="{{ p.id }}"
              aria-label="Total course quantities for {{ p.name }}" title="Total course quantities">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="2" width="16" height="20" rx="2"></rect><line x1="8" y1="7" x2="16" y2="7"></line><line x1="8" y1="11" x2="16" y2="11"></line><line x1="8" y1="15" x2="12" y2="15"></line></svg>
            </button>
```
(This is the same `<td class="actions">` already there — add the button as a new first line inside
it, keeping every existing child unchanged.)

- [ ] **Step 2: Add the dialog markup**

In `app/templates/protocols/list.html`, immediately before the `<script type="application/json"
id="course-totals-data">` tag added in Task 5, add:
```html
<dialog id="course-totals-dialog" class="dialog" aria-labelledby="course-totals-title">
  <header class="dialog-head">
    <h2 id="course-totals-title">Total course quantities</h2>
    <button type="button" class="btn btn-ghost btn-icon" data-action="close" aria-label="Close">×</button>
  </header>
  <div id="course-totals-body"></div>
</dialog>
```

- [ ] **Step 3: Add the JS**

Add to `app/static/js/protocols.js`, as a new IIFE appended at the end of the file (after the
existing Mine/Shared tabs block):
```javascript
// Total course quantities popup: one shared dialog, populated from the page's embedded JSON.
(() => {
  const dialog = document.getElementById("course-totals-dialog");
  if (!dialog) return;
  const body = document.getElementById("course-totals-body");
  const allTotals = JSON.parse(document.getElementById("course-totals-data").textContent);

  function fmt(n) {
    return n === null || n === undefined ? "" : String(Math.round(n * 100) / 100).replace(/\.?0+$/, "") || "0";
  }

  function render(protocolId) {
    const totals = allTotals[String(protocolId)];
    if (totals === null || totals === undefined) {
      body.replaceChildren(Object.assign(document.createElement("p"), {
        className: "muted",
        textContent: "Set an end date on this protocol to see its total course quantities.",
      }));
      return;
    }
    const table = document.createElement("table");
    table.className = "inv-table";
    const thead = document.createElement("thead");
    thead.innerHTML = "<tr><th>Peptide</th><th>Total</th><th>Vials</th><th>BAC water</th></tr>";
    const tbody = document.createElement("tbody");
    for (const t of totals) {
      const tr = document.createElement("tr");
      const totalCell = t.as_needed || t.total_amount === null ? (t.note || "") : `${fmt(t.total_amount)} ${t.unit}`;
      const vialsCell = t.vials_estimate === null ? (t.note || "—") : String(t.vials_estimate);
      const bacCell = t.bac_water_ml === null ? "—" : `${fmt(t.bac_water_ml)} mL`;
      tr.innerHTML = `<td>${t.peptide}</td><td>${totalCell}</td><td>${vialsCell}</td><td>${bacCell}</td>`;
      tbody.append(tr);
    }
    table.append(thead, tbody);
    const caption = document.createElement("p");
    caption.className = "muted small";
    caption.textContent = "Estimated using 1.5 mL bacteriostatic water per vial.";
    body.replaceChildren(table, caption);
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".totals-btn");
    if (btn) {
      render(btn.dataset.protocolId);
      dialog.showModal();
      return;
    }
    if (e.target.closest("[data-close]") && dialog.open) dialog.close();
  });

  let mousedownOnBackdrop = false;
  dialog.addEventListener("mousedown", (e) => { mousedownOnBackdrop = e.target === dialog; });
  dialog.addEventListener("click", (e) => { if (mousedownOnBackdrop && e.target === dialog) dialog.close(); });
})();
```

- [ ] **Step 4: Add CSS**

In `app/static/css/app.css`, immediately after the existing `.protocol-card-actions` rule, add:
```css
/* Bottom-right corner, a different corner from the top-left status ribbon so they never collide. */
.totals-btn { position: absolute; bottom: 10px; right: 10px; background: none; border: 0; color: var(--muted); cursor: pointer; padding: 4px; border-radius: 6px; }
.totals-btn:hover { color: var(--accent); background: var(--accent-soft); }
#course-totals-body table { margin-top: 12px; }
```

- [ ] **Step 5: Verify**

Run:
```bash
py -c "
css = open('app/static/css/app.css', encoding='utf-8').read()
print('open:', css.count('{'), 'close:', css.count('}'))
"
```
Expected: the two counts match.

Then render the Protocols page against an isolated TestClient instance (never the user's real
running one) and confirm visually via the Browser pane: create a protocol with an end date, load
`/protocols`, confirm the icon appears in the bottom-right corner of its card, click it, confirm the
dialog opens showing a table row for each item with a total/vials/BAC-water, confirm the "Estimated
using 1.5 mL..." caption appears, close the dialog, then create a second protocol with no end date
and confirm its popup shows the "Set an end date..." message instead of a table.

- [ ] **Step 6: Commit**

```bash
git add app/templates/protocols/list.html app/static/js/protocols.js app/static/css/app.css
git commit -m "feat: add the totals icon and popup to the Protocols list page"
```

---

## Final Verification

```bash
py -m pytest -q
py -c "
css = open('app/static/css/app.css', encoding='utf-8').read()
print('open:', css.count('{'), 'close:', css.count('}'))
"
```

Both counts must match, and every test must pass.

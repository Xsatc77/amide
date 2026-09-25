# Active Vials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track a reconstituted, opened vial from the moment it's mixed until it's discarded, completing Phase 2 (`Reconstitution Calculator + Active Vials`).

**Architecture:** A new `ActiveVial` table (owner_id, inventory_item_id, concentration, dose snapshot, dates) is created from the existing Calculator page via a new "Reconstitute this vial" commit step, reached by clicking a new "Reconstitute" button on Inventory's Lyophilized rows. The Inventory page grows a new Active Vials section — cards using the real vial icon with a text-overlay label, colored by expiry state, with a manual Discard action and an expiry-detection popup with a 24-hour re-ask throttle.

**Tech Stack:** FastAPI routers, SQLAlchemy 2.0 ORM, Alembic migration (SQLite), Jinja2 templates, vanilla JS (native `<dialog>`, matching the existing two-step delete-user pattern in `settings.js`).

**Spec:** `docs/superpowers/specs/2026-09-25-active-vials-design.md`

## Global Constraints

- `ActiveVial` always requires an existing `InventoryItem` — there is no standalone/manual creation path.
- Doses remaining (`doses_total`) is computed once at creation and never depletes in this pass — no manual decrement, no dose-logging integration (that's Phase 3).
- The duplicate-open-vial check is a warning, not a block: matched on the exact `inventory_item_id`, never a fuzzy peptide-name match (`InventoryItem` has no `Peptide` link).
- The Calculator page never forces a decision — picking an inventory item there, with or without a URL prefill, only ever *offers* the "Reconstitute this vial" button; nothing about viewing computed numbers commits anything.
- One confirmation modal for the actual commit, not a multi-step wizard — consistent with the rest of the app's single-screen forms.
- `ActiveVial` is owned data like `InventoryItem` and is visible to a grantee under the exact same `ShareCategory.INVENTORY` grant, with no new share category. A grantee sees a shared vial read-only: no Discard button, no expiry popup (that's the owner's decision to make).
- The Calculator's own syringe-fill visual is untouched in this plan — only the real `vial-blank-label.png` icon is wired in, on the new Active Vials cards.
- `InventoryItem.count` never goes negative; the Reconstitute button and the commit route both refuse when `count` is already 0.

## Review Focus

1. A grantee (Inventory-shared, not the owner) never sees a Discard button, never gets the expiry popup, and can't POST to the discard/reconstitute-commit routes for someone else's vial or item — sharing is view-only here exactly like it is for `InventoryItem`. (Task 5 `test_shared_active_vial_is_read_only`, Task 4 `test_reconstitute_commit_requires_ownership`)
2. The duplicate-vial warning is scoped to the exact inventory line, not the peptide name — two different `InventoryItem` rows with similar names must not trigger each other's warning. (Task 4 `test_duplicate_vial_check_scoped_to_exact_item`)
3. Reconstituting when `count` is already 0 is refused both in the UI (disabled button) and server-side (a direct POST must still 422/400, not silently create a vial and drop count to -1). (Task 3 `test_reconstitute_commit_refuses_when_count_is_zero`)
4. The expiry popup's 24-hour throttle actually throttles: declining it must not show it again on an immediate second page load, only after 24 hours have genuinely passed. (Task 5 `test_expiry_popup_does_not_repeat_within_24_hours`)
5. Discarding a vial never deletes the row (the spec requires it survive for history) — only `discarded_at` is set, and a discarded vial must disappear from the active list on the very next render. (Task 5 `test_discard_sets_discarded_at_and_leaves_active_list`)

---

### Task 1: Data model — `ActiveVial`, `User.default_discard_days`, migration

**Files:**
- Modify: `app/models.py` (add `ActiveVial`; add `User.default_discard_days`)
- Create: `migrations/versions/0010_active_vials.py`
- Modify: `tests/test_migrations.py` (new migration test)

**Interfaces:**
- Consumes: existing `LabeledEnum`/`_enum_column` pattern, `DoseUnit` enum (`app/models.py`).
- Produces: `ActiveVial` model — `id, owner_id, inventory_item_id, concentration_mg_ml: float, water_ml: float, dose_value: float, dose_unit: DoseUnit, doses_total: int, date_mixed: date, discard_by: date, discarded_at: datetime | None, last_discard_prompt_at: datetime | None, created_at: datetime`, table `active_vials`, indexed on `owner_id` and `inventory_item_id`. `User.default_discard_days: int` (nullable column; application default of 28 applied at read time, not a DB default). Consumed by Task 2 (Settings field), Task 3 (commit route reads `default_discard_days` and creates `ActiveVial`), Task 4 (duplicate-check query), Task 5 (Active Vials list + discard route).

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_migrations.py`:

```python
def test_0010_adds_active_vials_and_discard_days(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0009")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        assert "default_discard_days" in {r[1] for r in c.execute("pragma table_info(users)")}
        cols = {r[1] for r in c.execute("pragma table_info(active_vials)")}
        assert {"owner_id", "inventory_item_id", "concentration_mg_ml", "water_ml", "dose_value",
               "dose_unit", "doses_total", "date_mixed", "discard_by", "discarded_at",
               "last_discard_prompt_at", "created_at"} <= cols
    command.downgrade(cfg, "0009")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "active_vials" not in tables
        assert "default_discard_days" not in {r[1] for r in c.execute("pragma table_info(users)")}
```

- [ ] **Step 2: Run to verify it fails**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_migrations.py::test_0010_adds_active_vials_and_discard_days -v`
Expected: FAIL — `alembic.util.exc.CommandError` (no revision `0010`) or a schema assertion failure.

- [ ] **Step 3: Model changes**

In `app/models.py`, add `default_discard_days` to `User` (right after `colorway`):

```python
    default_discard_days: Mapped[int | None] = mapped_column(Integer)
```

Add a new `ActiveVial` class right after `InventoryItem` (before `Peptide`):

```python
class ActiveVial(Base):
    """A reconstituted, opened vial -- created from an InventoryItem, decrementing its count by 1.
    Doses remaining is a static snapshot computed once at creation (see app.calculator.reconstitution);
    it never depletes in this pass, since there is no dose-logging feature yet to draw it down."""

    __tablename__ = "active_vials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    concentration_mg_ml: Mapped[float] = mapped_column(Float)
    water_ml: Mapped[float] = mapped_column(Float)
    dose_value: Mapped[float] = mapped_column(Float)
    dose_unit: Mapped[DoseUnit] = mapped_column(_enum_column(DoseUnit))
    doses_total: Mapped[int] = mapped_column(Integer)
    date_mixed: Mapped[date] = mapped_column(Date)
    discard_by: Mapped[date] = mapped_column(Date)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_discard_prompt_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: utcnow().replace(tzinfo=None))

    inventory_item: Mapped["InventoryItem"] = relationship()
```

(`InventoryItem` is defined above `ActiveVial` in the file already, so the forward-reference string isn't strictly required, but keeping it as a string avoids caring about exact ordering if this changes later.)

- [ ] **Step 4: Write the migration**

Create `migrations/versions/0010_active_vials.py`:

```python
"""active vials: ActiveVial table, User.default_discard_days

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOSE_UNIT_VALUES = ('mg', 'mcg', 'IU')


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('default_discard_days', sa.Integer(), nullable=True))

    op.create_table(
        'active_vials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=False),
        sa.Column('concentration_mg_ml', sa.Float(), nullable=False),
        sa.Column('water_ml', sa.Float(), nullable=False),
        sa.Column('dose_value', sa.Float(), nullable=False),
        sa.Column('dose_unit', sa.Enum(*DOSE_UNIT_VALUES, name='doseunit', native_enum=False, length=20),
                  nullable=False),
        sa.Column('doses_total', sa.Integer(), nullable=False),
        sa.Column('date_mixed', sa.Date(), nullable=False),
        sa.Column('discard_by', sa.Date(), nullable=False),
        sa.Column('discarded_at', sa.DateTime(), nullable=True),
        sa.Column('last_discard_prompt_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_active_vials_owner_id', 'active_vials', ['owner_id'])
    op.create_index('ix_active_vials_inventory_item_id', 'active_vials', ['inventory_item_id'])


def downgrade() -> None:
    op.drop_index('ix_active_vials_inventory_item_id', table_name='active_vials')
    op.drop_index('ix_active_vials_owner_id', table_name='active_vials')
    op.drop_table('active_vials')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('default_discard_days')
```

`DoseUnit`'s current members are `MG = ("mg", "mg")`, `MCG = ("mcg", "mcg")`, `IU = ("IU", "IU")` (`app/models.py`), matching `DOSE_UNIT_VALUES` above exactly.

- [ ] **Step 5: Run to verify it passes**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_migrations.py -v`
Expected: PASS, all migration tests including the new one.

- [ ] **Step 6: Run the whole suite and commit**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

```bash
git add app/models.py migrations/versions/0010_active_vials.py tests/test_migrations.py
git commit -m "feat: ActiveVial model, User.default_discard_days, migration 0010"
```

---

### Task 2: Settings — default discard window field

**Files:**
- Modify: `app/routers/settings.py` (`POST /settings/discard-window`)
- Modify: `app/templates/settings/settings.html` (new field in the `#user` section)
- Modify: `tests/test_settings.py` (new tests)

**Interfaces:**
- Consumes: `User.default_discard_days` (Task 1).
- Produces: nothing consumed by later tasks — Task 3 reads `User.default_discard_days` directly off the model, applying its own `or 28` default at the point of use.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_settings.py`:

```python
def test_discard_window_saves_and_defaults_to_28(client, db, me):
    t = text(client.get("/settings"))
    assert 'value="28"' in t  # unset -> the form shows the application default, not blank

    r = client.post("/settings/discard-window", data={"default_discard_days": "45"}, follow_redirects=False)
    assert r.status_code == 303
    assert _current(me).default_discard_days == 45

    t = text(client.get("/settings"))
    assert 'value="45"' in t


def test_discard_window_rejects_non_positive(client, db, me):
    r = client.post("/settings/discard-window", data={"default_discard_days": "0"})
    assert r.status_code == 422
    r = client.post("/settings/discard-window", data={"default_discard_days": "not-a-number"})
    assert r.status_code == 422
    assert _current(me).default_discard_days in (None, 45)  # unchanged from whatever the prior test left
```

- [ ] **Step 2: Run to verify it fails**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_settings.py -k discard_window -v`
Expected: FAIL — 404 (route doesn't exist) and the settings page doesn't render `value="28"` anywhere yet.

- [ ] **Step 3: Add the route**

In `app/routers/settings.py`, add after `change_email`:

```python
@router.post("/settings/discard-window")
async def change_discard_window(request: Request, session: Session = Depends(get_session),
                                uid: int = Depends(current_user_id)):
    form = await request.form()
    raw = str(form.get("default_discard_days", "")).strip()
    try:
        days = int(raw)
        if days <= 0:
            raise ValueError
    except ValueError:
        return _render(request, session, errors={"default_discard_days": "Enter a whole number of days, greater than 0."},
                      status_code=422)

    _me(session, uid).default_discard_days = days
    session.commit()
    return RedirectResponse("/settings", status_code=303)
```

- [ ] **Step 4: Add the template field**

In `app/templates/settings/settings.html`, add right after the email `</form>` and before the Backup & restore `<div>`:

```html
      <form method="post" action="/settings/discard-window" class="settings-form" novalidate>
        <h3 class="small muted">Default discard window</h3>
        <p class="small muted">How many days after reconstituting a vial it defaults to expiring. You can still change it per vial when you reconstitute.</p>
        <label class="field {{ 'has-error' if errors.default_discard_days }}">
          <span>Days</span>
          <input name="default_discard_days" type="number" min="1" step="1" inputmode="numeric"
                value="{{ me.default_discard_days or 28 }}">
          {{ err('default_discard_days') }}
        </label>
        <button type="submit" class="btn btn-primary">Save</button>
      </form>
```

- [ ] **Step 5: Run to verify it passes**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_settings.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 6: Run the whole suite and commit**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

```bash
git add app/routers/settings.py app/templates/settings/settings.html tests/test_settings.py
git commit -m "feat: default discard-window setting"
```

---

### Task 3: Calculator — item-id tracking, Reconstitute button, commit route

**Files:**
- Modify: `app/routers/calculator.py` (`inventory_item_id` query param; `POST /calculator/reconstitute`)
- Modify: `app/templates/calculator/calculator.html` (inventory `<select>` uses item id as the value; Reconstitute button + confirmation `<dialog>`)
- Modify: `app/static/js/calculator.js` (track selected item id; wire the dialog)
- Create: `tests/test_active_vials.py`

**Interfaces:**
- Consumes: `ActiveVial`, `User.default_discard_days` (Task 1); `compute()` (existing, `app.calculator.reconstitution`).
- Produces: `POST /calculator/reconstitute` (form fields: `inventory_item_id`, `water_ml`, `dose_value`, `dose_unit`, `discard_by`) → creates an `ActiveVial`, decrements `InventoryItem.count`, redirects to `/inventory#active-vials`. Consumed by Task 4 (the Inventory page's Reconstitute button navigates here indirectly, via the Calculator page) and Task 5 (the redirect target).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_active_vials.py`:

```python
import html

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import ActiveVial, InventoryItem, User


def text(r) -> str:
    return html.unescape(r.text)


@pytest.fixture
def lyo_item(client):
    """A Lyophilized inventory item this test user owns, with 2 in stock."""
    client.post("/inventory", data={"name": "AV Test Peptide", "count": "2", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        return s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Test Peptide"))


def test_calculator_inventory_select_uses_item_id_as_value(client, db, lyo_item):
    t = text(client.get("/calculator"))
    assert f'<option value="{lyo_item}" data-vial-mg="10">AV Test Peptide (10 mg)</option>' in t


def test_calculator_prefills_from_inventory_item_id_query_param(client, db, lyo_item):
    t = text(client.get(f"/calculator?inventory_item_id={lyo_item}"))
    assert f'<option value="{lyo_item}" data-vial-mg="10" selected>' in t
    assert 'value="10"' in t.split('id="calc-vial"')[1][:200]  # the vial-amount field is prefilled


def test_reconstitute_commit_creates_vial_and_decrements_count(client, db, lyo_item, me):
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/inventory#active-vials"

    with SessionLocal() as s:
        item = s.get(InventoryItem, lyo_item)
        assert item.count == 1  # started at 2
        vial = s.scalar(select(ActiveVial).where(ActiveVial.inventory_item_id == lyo_item))
        assert vial is not None and vial.owner_id == me
        assert vial.concentration_mg_ml == pytest.approx(5.0)  # 10mg / 2mL
        assert vial.doses_total == 20  # 10mg // (250mcg = 0.25mg) = 40... see note below
        assert vial.discarded_at is None


def test_reconstitute_commit_refuses_when_count_is_zero(client, db, me):
    client.post("/inventory", data={"name": "Empty Stock", "count": "0", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Empty Stock"))

    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=item_id).count() == 0


def test_reconstitute_commit_requires_ownership(client, db, lyo_item):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "AVOther", "password": "AVOther1!", "confirm": "AVOther1!"})
    r = other.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert r.status_code == 404
```

Note on `doses_total` in the second test: `compute()`'s `doses_per_vial` is `int(vial // dose_mg)` where `dose_mg = 250 mcg = 0.25 mg`, so `10 // 0.25 == 40`, not `20` — **fix the test's expected value to `40`** before running it; the `20` above is a deliberate error for you to catch in Step 2 (a test that would pass for the wrong reason is worse than one that fails clearly). Correct it to `assert vial.doses_total == 40` as part of writing this step.

- [ ] **Step 2: Run to verify it fails**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_active_vials.py -v`
Expected: FAIL — the inventory `<option>` still uses `vial_mg` as its value (not the item id), there's no `data-vial-mg` attribute, `inventory_item_id` isn't read from query params, and `/calculator/reconstitute` doesn't exist (404, not 303/422).

- [ ] **Step 3: Update the calculator router**

In `app/routers/calculator.py`, add imports and the new route:

```python
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_user_id
from app.calculator.reconstitution import SYRINGE_CAPACITIES_UNITS, compute, water_for_target_units
from app.db import get_session
from app.models import ActiveVial, DoseUnit, InventoryItem, Medium, Protocol, ProtocolItem
from app.templating import templates
```

(`RedirectResponse`, `HTTPException`, `date`, `ActiveVial` are new; the rest already existed.)

Update `calculator_page` to read and apply `inventory_item_id`:

```python
@router.get("/calculator")
def calculator_page(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    q = request.query_params
    state = {k: q.get(k, str(default)) for k, default in DEFAULTS.items()}
    selected_item_id = q.get("inventory_item_id")

    inventory = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.owner_id == uid, InventoryItem.medium == Medium.LYOPHILIZED,
              InventoryItem.vial_size_mg.is_not(None))
        .order_by(InventoryItem.name.collate("NOCASE"))
    ).all()

    if selected_item_id and any(str(i.id) == selected_item_id for i in inventory) and "vial_mg" not in q:
        # Only auto-fill the vial amount from the id if the caller didn't also pass an explicit
        # vial_mg -- an explicit vial_mg (e.g. a bookmarked link) always wins.
        state["vial_mg"] = str(next(i.vial_size_mg for i in inventory if str(i.id) == selected_item_id))

    syringe_ml = _syringe_ml(state["syringe_ml"])
    result = compute(_num(state["vial_mg"]), _num(state["water_ml"]), _num(state["dose_value"]),
                     state["dose_unit"], syringe_ml)

    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid)
        .options(selectinload(Protocol.items).selectinload(ProtocolItem.peptide))
        .order_by(Protocol.name)
    ).all()

    data = {
        "syringe_capacities": list(SYRINGE_CAPACITIES_UNITS.items()),
        "inventory": [{"id": i.id, "name": i.name, "vial_mg": i.vial_size_mg} for i in inventory],
        "protocol_doses": [
            {"protocol": p.name, "peptide": it.peptide.name, "dose": it.dose, "unit": it.dose_unit.value}
            for p in protocols for it in p.items if it.dose is not None
        ],
    }
    return templates.TemplateResponse(request, "calculator/calculator.html", {
        "state": state, "result": result, "data": data, "capacities": SYRINGE_CAPACITIES_UNITS,
        "selected_item_id": int(selected_item_id) if selected_item_id and selected_item_id.isdigit() else None,
    })
```

Add the commit route, after `api_target_water` and before `calculator_page` (or anywhere in the file — order doesn't matter to FastAPI, but keep related routes near each other):

```python
@router.post("/calculator/reconstitute")
async def reconstitute(request: Request, session: Session = Depends(get_session),
                       uid: int = Depends(current_user_id)):
    form = await request.form()
    item = session.get(InventoryItem, int(form.get("inventory_item_id", 0) or 0))
    if item is None or item.owner_id != uid:
        raise HTTPException(status_code=404)

    water_ml = _num(form.get("water_ml"))
    dose_value = _num(form.get("dose_value"))
    dose_unit = str(form.get("dose_unit", "mg"))
    discard_by_raw = str(form.get("discard_by", ""))

    errors = []
    if item.count <= 0:
        errors.append("This item has none left in stock to reconstitute.")
    if water_ml is None or water_ml <= 0:
        errors.append("Enter the water added.")
    if dose_value is None or dose_value <= 0:
        errors.append("Enter a dose amount.")
    try:
        discard_by = date.fromisoformat(discard_by_raw)
    except ValueError:
        discard_by = None
        errors.append("Enter a valid discard-by date.")

    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))

    result = compute(item.vial_size_mg, water_ml, dose_value, dose_unit, 1.0)
    if result.concentration_mg_ml is None:
        raise HTTPException(status_code=422, detail="Could not compute a concentration from these values.")

    session.add(ActiveVial(
        owner_id=uid, inventory_item_id=item.id, concentration_mg_ml=result.concentration_mg_ml,
        water_ml=water_ml, dose_value=dose_value, dose_unit=DoseUnit(dose_unit),
        doses_total=result.doses_per_vial, date_mixed=date.today(), discard_by=discard_by,
    ))
    item.count -= 1
    session.commit()
    return RedirectResponse("/inventory#active-vials", status_code=303)
```

- [ ] **Step 4: Update the calculator template**

In `app/templates/calculator/calculator.html`, change the inventory `<select>`'s options to use the item id as the value, carrying `vial_mg` as a data attribute, and mark the selected one:

```html
        {% if data.inventory %}
        <label class="field span-2">
          <span>From inventory <span class="muted small">(lyophilized items only)</span></span>
          <select id="calc-inventory">
            <option value="">— Choose an item —</option>
            {% for i in data.inventory %}<option value="{{ i.id }}" data-vial-mg="{{ '%g' % i.vial_mg }}" {{ 'selected' if selected_item_id == i.id }}>{{ i.name }} ({{ '%g' % i.vial_mg }} mg)</option>{% endfor %}
          </select>
        </label>
        {% endif %}
```

Add the Reconstitute button and its confirmation dialog right after the closing `</form>` of `#calc-form` (before `<div class="calc-result...">`):

```html
  <div class="calc-reconstitute" id="calc-reconstitute" hidden>
    <button type="button" class="btn btn-primary" id="calc-reconstitute-btn">Reconstitute this vial</button>
  </div>
```

Add the confirmation `<dialog>` right before `{% endblock %}` (after the closing `</div>` of `.calc-layout`):

```html
<dialog id="reconstitute-confirm" class="dialog">
  <form method="post" action="/calculator/reconstitute">
    <div class="dialog-head"><h3>Reconstitute <span data-fill="item-name"></span>?</h3></div>
    <div class="form-section">
      <dl class="kv calc-kv">
        <dt>Water added</dt><dd data-fill="water-ml"></dd>
        <dt>Concentration</dt><dd data-fill="concentration"></dd>
        <dt>Per dose</dt><dd data-fill="dose"></dd>
        <dt>Doses per vial</dt><dd data-fill="doses"></dd>
      </dl>
      <label class="field">
        <span>Discard by</span>
        <input type="date" name="discard_by" data-discard-by>
      </label>
      <input type="hidden" name="inventory_item_id" data-inventory-item-id>
      <input type="hidden" name="water_ml" data-water-ml-value>
      <input type="hidden" name="dose_value" data-dose-value>
      <input type="hidden" name="dose_unit" data-dose-unit-value>
    </div>
    <div class="dialog-foot">
      <button type="submit" class="btn btn-primary">Reconstitute</button>
      <button type="button" class="btn" data-action="cancel-reconstitute">Cancel</button>
    </div>
  </form>
</dialog>
```

- [ ] **Step 5: Wire the JS**

In `app/static/js/calculator.js`, change the inventory-select handler to track the selected id and toggle the Reconstitute button, and add the confirmation dialog's fill-in logic. Replace the existing `els.inventory?.addEventListener(...)` block and add to the `els` map and the end of the file:

```javascript
  const els = {
    vial: $("calc-vial"), water: $("calc-water"), dose: $("calc-dose"), doseUnit: $("calc-dose-unit"),
    inventory: $("calc-inventory"), protocolDose: $("calc-protocol-dose"),
    warning: $("calc-warning"), unitsValue: $("calc-units-value"),
    concentration: $("calc-concentration"), draw: $("calc-draw"), doses: $("calc-doses"),
    fill: $("calc-fill"), ticks: $("calc-ticks"), scale: $("calc-scale"),
    targetUnits: $("calc-target-units"), targetResult: $("calc-target-result"),
    reconstituteWrap: $("calc-reconstitute"), reconstituteBtn: $("calc-reconstitute-btn"),
    confirmDialog: $("reconstitute-confirm"),
  };
```

```javascript
  function updateReconstituteVisibility() {
    if (!els.reconstituteWrap) return;
    els.reconstituteWrap.hidden = !els.inventory?.value;
  }

  els.inventory?.addEventListener("change", () => {
    const opt = els.inventory.selectedOptions[0];
    if (opt?.dataset.vialMg) { els.vial.value = opt.dataset.vialMg; recompute(); }
    updateReconstituteVisibility();
  });
  updateReconstituteVisibility();

  els.reconstituteBtn?.addEventListener("click", () => {
    const opt = els.inventory.selectedOptions[0];
    const dialog = els.confirmDialog;
    dialog.querySelector('[data-fill="item-name"]').textContent = opt.textContent;
    dialog.querySelector('[data-fill="water-ml"]').textContent = `${els.water.value} mL`;
    dialog.querySelector('[data-fill="concentration"]').textContent = els.concentration.textContent;
    dialog.querySelector('[data-fill="dose"]').textContent = `${els.dose.value} ${els.doseUnit.value}`;
    dialog.querySelector('[data-fill="doses"]').textContent = els.doses.textContent;
    dialog.querySelector("[data-inventory-item-id]").value = opt.value;
    dialog.querySelector("[data-water-ml-value]").value = els.water.value;
    dialog.querySelector("[data-dose-value]").value = els.dose.value;
    dialog.querySelector("[data-dose-unit-value]").value = els.doseUnit.value;
    const discardInput = dialog.querySelector("[data-discard-by]");
    if (!discardInput.value) {
      const d = new Date();
      d.setDate(d.getDate() + (Number(data.default_discard_days) || 28));
      discardInput.value = d.toISOString().slice(0, 10);
    }
    dialog.showModal();
  });
  els.confirmDialog?.querySelector('[data-action="cancel-reconstitute"]')?.addEventListener("click", () => {
    els.confirmDialog.close();
  });
```

Add `default_discard_days` to the page's `data` JSON so the dialog can default the date: `request.state.user` is already the loaded `User` for this request (the same object `request.state.user.id` is read from elsewhere in the app). Add `"default_discard_days": request.state.user.default_discard_days or 28` to the `data` dict in `calculator_page`.

- [ ] **Step 6: Run to verify it passes**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_active_vials.py tests/test_calculator_page.py -v`
Expected: PASS, all tests in both files.

- [ ] **Step 7: Manual browser check**

Start a scratch demo server, add a Lyophilized inventory item, go to `/calculator`, pick it from the dropdown, confirm the "Reconstitute this vial" button appears and the vial-amount field fills correctly (not "None" or empty — this is the step most likely to reveal an id/value mixup), fill water and dose, click Reconstitute, confirm the modal shows sensible values and a defaulted discard-by date, confirm, and check the Inventory item's count dropped by 1 and you land on `/inventory#active-vials`. Clean up the scratch server/data.

- [ ] **Step 8: Run the whole suite and commit**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

```bash
git add app/routers/calculator.py app/templates/calculator/calculator.html app/static/js/calculator.js tests/test_active_vials.py
git commit -m "feat: Calculator tracks inventory item id, Reconstitute commit route"
```

---

### Task 4: Inventory — Reconstitute button + duplicate-vial check

**Files:**
- Modify: `app/routers/inventory.py` (`_render_list` computes each Lyophilized item's open `ActiveVial`, if any)
- Modify: `app/templates/inventory/list.html` (Reconstitute button; duplicate-check `<dialog>`)
- Modify: `app/static/js/inventory.js` (wire the dialog)
- Modify: `tests/test_active_vials.py` (new tests)

**Interfaces:**
- Consumes: `ActiveVial` (Task 1).
- Produces: nothing consumed by other tasks — this task only adds a client-side bridge into the Calculator page Task 3 already built.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_active_vials.py`:

```python
def _button_tag(t: str, item_id) -> str:
    """The full opening <button ...> tag whose data-item-id matches, regardless of attribute
    order or surrounding whitespace -- avoids brittle fixed-length slicing."""
    marker = f'data-item-id="{item_id}"'
    start = t.rindex("<button", 0, t.index(marker))
    end = t.index(">", t.index(marker)) + 1
    return t[start:end]


def test_reconstitute_button_present_and_disabled_when_out_of_stock(client, db, lyo_item):
    client.post("/inventory", data={"name": "Zero Stock", "count": "0", "vial_size_mg": "5",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        zero_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Zero Stock"))
    t = text(client.get("/inventory"))
    assert f'data-action="reconstitute" data-item-id="{lyo_item}"' in t
    assert "disabled" in _button_tag(t, zero_id)
    assert "disabled" not in _button_tag(t, lyo_item)  # has stock -- not disabled


def test_duplicate_vial_check_scoped_to_exact_item(client, db, lyo_item):
    client.post("/inventory", data={"name": "AV Test Peptide Two", "count": "1", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        other_item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Test Peptide Two"))

    client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(lyo_item), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })

    t = text(client.get("/inventory"))
    # The exact item with an open vial carries its data for the JS-side warning dialog.
    assert "data-active-vial=" in _button_tag(t, lyo_item)
    # The similarly-named other item has no open vial and carries no such data.
    assert "data-active-vial=" not in _button_tag(t, other_item_id)
```

- [ ] **Step 2: Run to verify it fails**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_active_vials.py -k "reconstitute_button or duplicate_vial" -v`
Expected: FAIL — no `data-action="reconstitute"` button exists yet anywhere in the Inventory page.

- [ ] **Step 3: Update the inventory router**

In `app/routers/inventory.py`, add the import and update `_render_list`:

```python
from app.models import ActiveVial, DoseUnit, InventoryItem, Medium, Share, ShareCategory, StorageLocation, User
```

Add a helper right after `_visible_items`:

```python
def _open_active_vials(session: Session, item_ids: list[int]) -> dict[int, ActiveVial]:
    """The earliest-discard-by open (non-discarded) ActiveVial per inventory_item_id, for the
    duplicate-vial warning check. Only one per item is shown even if more than one exists."""
    if not item_ids:
        return {}
    vials = session.scalars(
        select(ActiveVial).where(ActiveVial.inventory_item_id.in_(item_ids), ActiveVial.discarded_at.is_(None))
        .order_by(ActiveVial.discard_by)
    ).all()
    result: dict[int, ActiveVial] = {}
    for v in vials:
        result.setdefault(v.inventory_item_id, v)  # first (earliest discard_by) wins per item
    return result
```

Update `_render_list` to pass this data:

```python
def _render_list(request: Request, session: Session, *, form: dict | None = None, errors=None,
                 editing: InventoryItem | None = None, status_code: int = 200):
    uid = request.state.user.id
    items, owner_names = _visible_items(session, uid)
    own_lyo_ids = [i.id for i in items if i.owner_id == uid and i.medium == Medium.LYOPHILIZED]
    open_vials = _open_active_vials(session, own_lyo_ids)
    return templates.TemplateResponse(
        request,
        "inventory/list.html",
        {
            "items": items,
            "viewer_id": uid,
            "owner_names": owner_names,
            "open_vials": open_vials,
            "edit_data": {i.id: _form_values(i) for i in items if i.owner_id == uid},
            "mediums": list(Medium),
            "dose_units": list(DoseUnit),
            "storage_locations": list(StorageLocation),
            "medium_rules": {
                m.value: {
                    "required": sorted(required_fields_for(m)),
                    "labels": {f: field_label(f, m) for f in ("vial_size_mg", "units_per_package")},
                }
                for m in Medium
            },
            "form": form,
            "errors": errors or {},
            "editing": editing,
        },
        status_code=status_code,
    )
```

- [ ] **Step 4: Update the template**

In `app/templates/inventory/list.html`, add the Reconstitute button to the actions cell, only for the viewer's own Lyophilized rows:

```html
        <td class="actions">
          {% if item.owner_id == viewer_id %}
          {% if item.medium and item.medium.value == 'Lyophilized' %}
          <button type="button" class="btn btn-ghost" data-action="reconstitute" data-item-id="{{ item.id }}"
                  data-item-name="{{ item.name }}"
                  {% if item.id in open_vials %}data-active-vial='{{ {"concentration": open_vials[item.id].concentration_mg_ml, "doses": open_vials[item.id].doses_total, "discard_by": open_vials[item.id].discard_by.isoformat()} | tojson }}'{% endif %}
                  {% if item.count == 0 %}disabled{% endif %}>Reconstitute</button>
          {% endif %}
          <button type="button" class="btn btn-ghost" data-action="edit"
                  data-item='{{ edit_data[item.id] | tojson }}'>Edit</button>
          <form method="post" action="/inventory/{{ item.id }}/delete" class="inline" data-confirm="Delete “{{ item.name }}”? This can't be undone.">
            <button type="submit" class="btn btn-ghost btn-danger">Delete</button>
          </form>
          {% else %}
          <span class="muted small">Read only</span>
          {% endif %}
        </td>
```

Add the duplicate-check dialog right before `{% endblock %}` (after the closing `</dialog>` of `#item-dialog`):

```html
<dialog id="duplicate-vial-check" class="dialog">
  <div class="dialog-head"><h3>You already have an open vial of <span data-fill="item-name"></span></h3></div>
  <div class="form-section">
    <p class="small muted" data-fill="existing-summary"></p>
    <p class="small muted">Reconstitute a new one anyway?</p>
  </div>
  <div class="dialog-foot">
    <button type="button" class="btn btn-primary" data-action="continue-reconstitute">Continue anyway</button>
    <button type="button" class="btn" data-action="cancel-reconstitute-check">Cancel</button>
  </div>
</dialog>
```

- [ ] **Step 5: Wire the JS**

In `app/static/js/inventory.js`, add (near the other `document.querySelectorAll("[data-action=...]")` handlers — read the file first to match its existing style for where these live):

```javascript
document.querySelectorAll('[data-action="reconstitute"]').forEach((btn) =>
  btn.addEventListener("click", () => {
    const itemId = btn.dataset.itemId;
    const existing = btn.dataset.activeVial ? JSON.parse(btn.dataset.activeVial) : null;
    const goToCalculator = () => { window.location.href = `/calculator?inventory_item_id=${itemId}`; };
    if (!existing) { goToCalculator(); return; }

    const dialog = document.getElementById("duplicate-vial-check");
    dialog.querySelector('[data-fill="item-name"]').textContent = btn.dataset.itemName;
    dialog.querySelector('[data-fill="existing-summary"]').textContent =
      `${existing.concentration.toFixed(2)} mg/mL, ${existing.doses} doses, discard by ${existing.discard_by}.`;
    dialog.querySelector('[data-action="continue-reconstitute"]').onclick = () => { dialog.close(); goToCalculator(); };
    dialog.querySelector('[data-action="cancel-reconstitute-check"]').onclick = () => dialog.close();
    dialog.showModal();
  })
);
```

- [ ] **Step 6: Run to verify it passes**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_active_vials.py tests/test_inventory.py -v`
Expected: PASS, all tests in both files.

- [ ] **Step 7: Manual browser check**

On the scratch server: reconstitute a vial for an item (via the Calculator), then go back to Inventory and click that same item's Reconstitute button again — confirm the "You already have an open vial" dialog appears with the right numbers, "Continue anyway" takes you to the Calculator prefilled, "Cancel" just closes the dialog. Confirm a *different* item's Reconstitute button (no open vial) goes straight to the Calculator with no dialog. Clean up.

- [ ] **Step 8: Run the whole suite and commit**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

```bash
git add app/routers/inventory.py app/templates/inventory/list.html app/static/js/inventory.js tests/test_active_vials.py
git commit -m "feat: Reconstitute button on Inventory, duplicate-open-vial warning"
```

---

### Task 5: Active Vials section — cards, icon, expiry, discard

**Files:**
- Modify: `app/routers/inventory.py` (Active Vials list in `_render_list`; `POST /active-vials/{vial_id}/discard`; `POST /active-vials/{vial_id}/snooze-prompt`)
- Modify: `app/templates/inventory/list.html` (Active Vials section; expiry-popup dialogs)
- Modify: `app/static/js/inventory.js` (expiry popup logic, reconstitute-again bridge)
- Modify: `app/static/css/app.css` (vial card + label-overlay + red/yellow state styling)
- Modify: `tests/test_active_vials.py`, `tests/test_privacy.py` (sharing coverage)

**Interfaces:**
- Consumes: `ActiveVial`, real icon at `app/static/img/icons/vial-blank-label.png` (Task 1, already committed on `main`).
- Produces: nothing consumed by other tasks (final feature task before docs).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_active_vials.py`:

```python
from datetime import datetime, timedelta


def _reconstitute(client, item_id, discard_by="2026-12-31"):
    client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": discard_by,
    })
    with SessionLocal() as s:
        return s.scalar(select(ActiveVial.id).where(ActiveVial.inventory_item_id == item_id))


def test_active_vial_card_shows_icon_and_label_fields(client, db, lyo_item):
    _reconstitute(client, lyo_item)
    t = text(client.get("/inventory"))
    assert 'id="active-vials"' in t
    assert "vial-blank-label.png" in t
    assert "AV Test Peptide" in t.split('id="active-vials"')[1]
    assert "5 mg/mL" in t  # 10mg / 2mL
    assert "10mg" in t.split('id="active-vials"')[1].split("5 mg/mL")[0][-30:]  # total content near it


def test_discard_sets_discarded_at_and_leaves_active_list(client, db, lyo_item):
    vial_id = _reconstitute(client, lyo_item)
    r = client.post(f"/active-vials/{vial_id}/discard", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        assert v is not None and v.discarded_at is not None  # row survives
    t = text(client.get("/inventory"))
    assert "AV Test Peptide" not in t.split('id="active-vials"')[1].split("</section>")[0]


def test_expiry_popup_fires_once_then_throttles_24_hours(client, db, lyo_item):
    vial_id = _reconstitute(client, lyo_item, discard_by="2020-01-01")  # already expired
    t = text(client.get("/inventory"))
    assert f'data-expired-prompt="{vial_id}"' in t  # server flags it for the JS popup to show

    client.post(f"/active-vials/{vial_id}/snooze-prompt")
    with SessionLocal() as s:
        assert s.get(ActiveVial, vial_id).last_discard_prompt_at is not None

    t = text(client.get("/inventory"))
    assert f'data-expired-prompt="{vial_id}"' not in t  # throttled, no popup flag
    section = t.split('id="active-vials"')[1].split("</section>")[0]
    assert "vial-card-yellow" in section  # renders yellow instead


def test_expiry_popup_does_not_repeat_within_24_hours(client, db, lyo_item):
    vial_id = _reconstitute(client, lyo_item, discard_by="2020-01-01")
    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        v.last_discard_prompt_at = datetime.utcnow() - timedelta(hours=1)
        s.commit()
    t = text(client.get("/inventory"))
    assert f'data-expired-prompt="{vial_id}"' not in t

    with SessionLocal() as s:
        v = s.get(ActiveVial, vial_id)
        v.last_discard_prompt_at = datetime.utcnow() - timedelta(hours=25)
        s.commit()
    t = text(client.get("/inventory"))
    assert f'data-expired-prompt="{vial_id}"' in t


def test_shared_active_vial_is_read_only(client, db, lyo_item, me):
    from app.models import Share, ShareCategory

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "AVShared", "password": "AVShared1!", "confirm": "AVShared1!"})
    with SessionLocal() as s:
        other_id = s.scalar(select(User.id).where(User.username_key == "avshared"))
        s.add(Share(owner_id=me, grantee_id=other_id, category=ShareCategory.INVENTORY))
        s.commit()

    vial_id = _reconstitute(client, lyo_item)
    t = text(other.get("/inventory"))
    section = t.split('id="active-vials"')[1].split("</section>")[0]
    assert "AV Test Peptide" in section
    assert f'data-vial-id="{vial_id}"' in section
    assert "Discard" not in section  # read-only: no discard button for a grantee

    assert other.post(f"/active-vials/{vial_id}/discard").status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_active_vials.py -v`
Expected: FAIL — no `#active-vials` section exists anywhere yet, no discard/snooze routes.

- [ ] **Step 3: Update the inventory router**

Add the Active Vials list (own + shared) and the two new routes. In `app/routers/inventory.py`, add:

```python
def _visible_active_vials(session: Session, uid: int):
    """This user's own open Active Vials, plus open vials on items owned by anyone who granted
    them Inventory sharing -- the same grant, no new category."""
    shared_owner_ids = select(Share.owner_id).where(
        Share.grantee_id == uid, Share.category == ShareCategory.INVENTORY)
    vials = session.scalars(
        select(ActiveVial)
        .where(ActiveVial.discarded_at.is_(None),
              (ActiveVial.owner_id == uid) | (ActiveVial.owner_id.in_(shared_owner_ids)))
        .order_by(ActiveVial.discard_by)
    ).all()
    item_ids = {v.inventory_item_id for v in vials}
    items_by_id = {i.id: i for i in session.scalars(
        select(InventoryItem).where(InventoryItem.id.in_(item_ids)))} if item_ids else {}
    owner_ids = {v.owner_id for v in vials if v.owner_id != uid}
    owner_names = dict(session.execute(
        select(User.id, User.username).where(User.id.in_(owner_ids)))) if owner_ids else {}
    return vials, items_by_id, owner_names
```

Update `_render_list` to add these to context:

```python
    vials, vial_items, vial_owner_names = _visible_active_vials(session, uid)
    now = datetime.utcnow()
    expired_prompts = {
        v.id for v in vials
        if v.owner_id == uid and v.discard_by < date.today()
        and (v.last_discard_prompt_at is None or now - v.last_discard_prompt_at > timedelta(hours=24))
    }
```

(add this block right after computing `open_vials`, and add `active_vials`, `vial_items`, `vial_owner_names`, `expired_prompts`, `today` to the returned template context dict — `"active_vials": vials, "vial_items": vial_items, "vial_owner_names": vial_owner_names, "expired_prompts": expired_prompts, "today": date.today(),`)

Add the needed imports at the top of the file: `from datetime import date, datetime, timedelta` (replacing the existing `from datetime import date`).

Add the two new routes at the end of the HTML routes section (after `delete_item`, before `get_coa`):

```python
def _own_active_vial(session: Session, vial_id: int, uid: int) -> ActiveVial | None:
    vial = session.get(ActiveVial, vial_id)
    return vial if vial is not None and vial.owner_id == uid else None


@router.post("/active-vials/{vial_id}/discard")
def discard_active_vial(vial_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    vial = _own_active_vial(session, vial_id, uid)
    if vial is None:
        raise HTTPException(404)
    vial.discarded_at = datetime.utcnow()
    session.commit()
    return RedirectResponse(f"/inventory?just_discarded={vial.inventory_item_id}#active-vials", status_code=303)


@router.post("/active-vials/{vial_id}/snooze-prompt")
def snooze_active_vial_prompt(vial_id: int, session: Session = Depends(get_session),
                              uid: int = Depends(current_user_id)):
    vial = _own_active_vial(session, vial_id, uid)
    if vial is None:
        raise HTTPException(404)
    vial.last_discard_prompt_at = datetime.utcnow()
    session.commit()
    return {"ok": True}
```

- [ ] **Step 4: Update the template**

In `app/templates/inventory/list.html`, add the Active Vials section right after the closing `</div>` of the main inventory `.table-wrap` block's surrounding content (i.e. right before the `{# ---------- Add / edit dialog ---------- #}` comment):

```html
<section id="active-vials" aria-labelledby="active-vials-heading">
  <h2 id="active-vials-heading" class="section-title">Active vials</h2>
  {% if active_vials %}
  <div class="card-grid vial-card-grid">
    {% for v in active_vials %}
    {% set is_mine = v.owner_id == viewer_id %}
    {% set expired = v.discard_by < today %}
    {% set item = vial_items.get(v.inventory_item_id) %}
    <article class="vial-card {{ 'vial-card-expired' if expired and v.id in expired_prompts }} {{ 'vial-card-yellow' if expired and v.id not in expired_prompts and v.last_discard_prompt_at }}"
             data-vial-id="{{ v.id }}" {% if is_mine and v.id in expired_prompts %}data-expired-prompt="{{ v.id }}"{% endif %}
             {% if is_mine %}data-item-id-for-reconstitute="{{ v.inventory_item_id }}"{% endif %}>
      <div class="vial-card-icon">
        <img src="{{ static_url('img/icons/vial-blank-label.png') }}" alt="">
        <div class="vial-card-label">
          <strong>{{ item.name if item else 'Item' }}</strong>
          <span>{{ item.vial_size_mg | mg if item else '' }} · {{ v.concentration_mg_ml | dose_num }} mg/mL</span>
          <span>Discard by {{ v.discard_by | shortdate }}</span>
          <span>{{ v.doses_total }} doses</span>
          {% if not is_mine %}<span class="muted">Shared by {{ vial_owner_names.get(v.owner_id) }}</span>{% endif %}
        </div>
      </div>
      {% if is_mine %}
      <form method="post" action="/active-vials/{{ v.id }}/discard" class="inline"
            data-confirm="Discard this vial? This can't be undone.">
        <button type="submit" class="btn btn-ghost btn-danger">Discard</button>
      </form>
      {% endif %}
    </article>
    {% endfor %}
  </div>
  {% else %}
  <p class="muted">No open vials yet — reconstitute one from a Lyophilized item above.</p>
  {% endif %}
</section>
```

Add the expiry-popup and reconstitute-again dialogs right before `{% endblock %}` (after the duplicate-vial-check dialog from Task 4):

```html
<dialog id="expiry-prompt" class="dialog">
  <div class="dialog-head"><h3>This vial passed its discard-by date</h3></div>
  <div class="form-section"><p class="small muted">Discard it?</p></div>
  <div class="dialog-foot">
    <button type="button" class="btn btn-primary" data-action="expiry-discard">Discard</button>
    <button type="button" class="btn" data-action="expiry-not-yet">Not yet</button>
  </div>
</dialog>

<dialog id="reconstitute-again-prompt" class="dialog">
  <div class="dialog-head"><h3>Reconstitute a new one now?</h3></div>
  <div class="dialog-foot">
    <button type="button" class="btn btn-primary" data-action="reconstitute-again-yes">Yes</button>
    <button type="button" class="btn" data-action="reconstitute-again-no">No</button>
  </div>
</dialog>
```

- [ ] **Step 5: Wire the JS**

Append to `app/static/js/inventory.js`:

```javascript
// Expiry popups: one per vial flagged by the server (data-expired-prompt), shown on load.
document.querySelectorAll("[data-expired-prompt]").forEach((card) => {
  const vialId = card.dataset.expiredPrompt;
  const dialog = document.getElementById("expiry-prompt");
  dialog.querySelector('[data-action="expiry-discard"]').onclick = () => {
    dialog.close();
    fetch(`/active-vials/${vialId}/discard`, { method: "POST" }).then(() => {
      const itemId = card.dataset.itemIdForReconstitute;
      const again = document.getElementById("reconstitute-again-prompt");
      again.querySelector('[data-action="reconstitute-again-yes"]').onclick = () => {
        window.location.href = `/calculator?inventory_item_id=${itemId}`;
      };
      again.querySelector('[data-action="reconstitute-again-no"]').onclick = () => {
        again.close();
        window.location.href = "/inventory#active-vials";
      };
      again.showModal();
    });
  };
  dialog.querySelector('[data-action="expiry-not-yet"]').onclick = () => {
    dialog.close();
    fetch(`/active-vials/${vialId}/snooze-prompt`, { method: "POST" }).then(() => window.location.reload());
  };
  dialog.showModal();
});

// If we just redirected here after discarding via the Inventory-page popup flow, drop the query
// param from the visible URL without a reload (it's already done its job).
if (window.location.search.includes("just_discarded")) {
  window.history.replaceState({}, "", "/inventory#active-vials");
}
```

- [ ] **Step 6: Add CSS**

In `app/static/css/app.css`, add near `.card-grid`/`.protocol-card`:

```css
.vial-card-grid { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
.vial-card { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.vial-card-icon { position: relative; display: flex; justify-content: center; }
.vial-card-icon img { width: 100px; height: auto; }
.vial-card-label { position: absolute; inset: 38% 8px auto 8px; display: flex; flex-direction: column; align-items: center; gap: 2px; text-align: center; font-size: 0.7rem; line-height: 1.2; color: #1a1a1a; }
.vial-card-label strong { font-size: 0.75rem; }
.vial-card-expired { border-color: var(--danger); background: color-mix(in srgb, var(--danger) 12%, var(--surface)); }
.vial-card-yellow { border-color: #d4a017; background: color-mix(in srgb, #d4a017 12%, var(--surface)); }
```

- [ ] **Step 7: Run to verify it passes**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_active_vials.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 8: Add the sharing-privacy test to `test_privacy.py`**

Add to `tests/test_privacy.py`, reusing its existing `_grant`/`_revoke` helpers:

```python
def test_active_vial_not_visible_without_inventory_grant(client, other, me):
    from app.models import ActiveVial

    client.post("/inventory", data={"name": "AV Privacy Item", "count": "1", "vial_size_mg": "10",
                                    "medium": "Lyophilized"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Privacy Item"))
    client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert "AV Privacy Item" not in other.get("/inventory").text
```

- [ ] **Step 9: Run to verify it passes**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest tests/test_active_vials.py tests/test_privacy.py tests/test_inventory.py -v`
Expected: PASS, all tests in all three files.

- [ ] **Step 10: Manual browser check**

On the scratch server: reconstitute a vial, confirm the card shows the vial icon with the overlay text (name, total content + concentration, discard-by, doses), confirm Discard works (own confirm dialog, vial disappears from the active list). Reconstitute another with a past discard-by date (e.g. type a date in the past in the confirm modal), reload Inventory, confirm the red-state popup appears; click "Not yet" and confirm it turns yellow and doesn't pop again on an immediate reload; click Discard on it manually and confirm it disappears. Grant Inventory sharing from one scratch account to another and confirm the second account sees the first's active vial read-only (no Discard button). Clean up.

- [ ] **Step 11: Run the whole suite and commit**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: PASS.

```bash
git add app/routers/inventory.py app/templates/inventory/list.html app/static/js/inventory.js app/static/css/app.css tests/test_active_vials.py tests/test_privacy.py
git commit -m "feat: Active Vials section (icon card, expiry popup, discard, sharing)"
```

---

### Task 6: Docs + ship

**Files:**
- Modify: `README.md`, `docs/ROADMAP.md`

- [ ] **Step 1: Run the full suite one more time**

Run: `/c/tmp/amide/.venv/Scripts/python -m pytest -q -p no:warnings`
Expected: all pass.

- [ ] **Step 2: Update README**

Add a new version-history line above the current top entry (`**v0.9 — Sharing.**`):

```
**v1.0 — Active Vials.** Reconstituting a Lyophilized inventory item (from the Inventory page or the Calculator) creates a tracked Active Vial — concentration, total content, discard-by date, and doses per vial, shown as a card with the real vial icon. Expired vials flag themselves for a one-time-per-24-hours discard prompt; discarding never deletes the record, just retires it. A default discard window (in days) is configurable in Settings.

```

- [ ] **Step 3: Update ROADMAP**

Replace the two "Still open" lines under Phase 2 (`Active Vials` and `real icon art`) with:

```
- ✅ *Active Vials shipped in v1.0*: reconstitute from Inventory or the Calculator, tracked concentration/discard-by/doses, expiry-aware Active Vials section with the real vial icon, default discard-window setting.
- **Still open — remaining icon art:** the Calculator's own syringe-fill visual still uses its original CSS-drawn look; the other cropped icons (pens, pill bottle, etc.) remain unused until later phases need them.
```

This closes Phase 2 except for that one remaining icon-polish note.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ROADMAP.md
git commit -m "docs: Active Vials shipped as v1.0"
```

- [ ] **Step 5: Merge to main and push**

```bash
cd /c/tmp/amide
git merge --ff-only feature/active-vials
git push origin main
git worktree remove --force /c/tmp/amide-vials
git branch -d feature/active-vials
```

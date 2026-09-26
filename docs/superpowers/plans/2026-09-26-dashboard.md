# Dashboard (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** build the Dashboard homepage described in the spec — Today's Schedule, Alerts (low
stock, expiration, shipment delay), Cost snapshot, Adherence snapshot, placeholder cards for
Weight/Journal/Health, and a single-person viewer switcher for shared users.

**Architecture:** one new router (`app/routers/dashboard.py`) and one new pure-function module
(`app/alerts.py`), reusing existing helpers (`occurrences()`, `DoseLog`, `available_count`,
`total_cost`) rather than duplicating them. Two new nullable `User` columns and one new nullable
`InventoryItem` column, all following the `default_discard_days` pattern already in the codebase.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic (SQLite) + Jinja2 + vanilla JS (same as the rest
of the app).

**Spec:** `docs/superpowers/specs/2026-09-26-dashboard-design.md`

## Global Constraints

- `None` is the only "use the default" sentinel for `low_stock_threshold`/`low_stock_default`/
  `shipment_delay_days` — `0` is a real, valid value and must never be treated as unset.
- All Dashboard queries are scoped to exactly one viewer at a time (self or one selected shared
  user) — never blended, matching the spec's explicit departure from `_visible_items`'s
  blend-everything pattern.
- Alerts/cost/adherence widgets are gated by `ShareCategory` when viewing a shared user: Inventory
  category unlocks Alerts + Cost snapshot; Personal-data category unlocks Today's Schedule +
  Adherence snapshot; a widget whose category isn't shared is omitted, not shown empty.
- No per-widget on/off toggles in this plan (explicitly deferred) — fixed layout only.
- No real Weight/Journal/Health-integration logic — static placeholder cards only.
- Follow existing conventions: `_own_*`/`_visible_*` naming for scoping helpers, `Depends(get_today)`
  for the pinned "today" date (never bare `date.today()` in a route), migrations via
  `op.batch_alter_table` for SQLite compatibility.

## Review Focus

1. `low_stock_threshold = 0` (explicitly set) must trigger the low-stock alert immediately, not be
   treated as "unset" and fall back to the default (`None` is the only unset sentinel).
2. Switching the viewer dropdown to a shared user must show ONLY that user's data for every gated
   widget — a bug here would leak one user's health/financial data into another's view.
3. An `Order` with a non-null `arrival_date` must never appear in the shipment-delay alert, no
   matter how long ago it shipped — the query is `arrival_date IS NULL`, not a duration check alone.
4. The viewer-switcher dropdown must only list users whose `Share` row currently exists (`grantee_id
   == viewer`) — a revoked share must remove that option on the very next page load, no caching.
5. The Dashboard's "already logged today" computation (for Today's Schedule) must exactly match
   `dosing.py`'s own `today_page` filter shape, so logging a dose from `/today` is immediately
   reflected on the Dashboard with no separate cache to invalidate.

---

### Task 1: Migration + new columns

**Files:**
- Modify: `app/models.py` (add `InventoryItem.low_stock_threshold`, `User.low_stock_default`,
  `User.shipment_delay_days`)
- Create: `migrations/versions/0015_dashboard_thresholds.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `InventoryItem.low_stock_threshold: int | None`, `User.low_stock_default: int | None`,
  `User.shipment_delay_days: int | None` — Task 2 (forms) and Task 3 (alerts) both read these by
  name.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_migrations.py`:

```python
def test_0015_adds_dashboard_thresholds(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0014")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        c.execute("insert into inventory_items(name,count,vial_size_unit,category,created_at,"
                  "updated_at,owner_id) values ('Retatrutide',0,'mg','Medicine','2026-09-25',"
                  "'2026-09-25',1)")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        user_cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert {"low_stock_default", "shipment_delay_days"} <= user_cols
        item_cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert "low_stock_threshold" in item_cols
        # All three must be nullable (None means "use the default") -- explicitly set 0 must
        # round-trip as 0, never coerced to NULL or rejected.
        c.execute("update inventory_items set low_stock_threshold = 0 where name = 'Retatrutide'")
        value = c.execute(
            "select low_stock_threshold from inventory_items where name = 'Retatrutide'").fetchone()[0]
        assert value == 0
    command.downgrade(cfg, "0014")
    with sqlite3.connect(db) as c:
        user_cols = {r[1] for r in c.execute("pragma table_info(users)")}
        assert "low_stock_default" not in user_cols and "shipment_delay_days" not in user_cols
        item_cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert "low_stock_threshold" not in item_cols
```

Check the top of `tests/test_migrations.py` for its `_cfg`/`command`/`sqlite3` imports and helper —
they already exist (used by `test_0014_adds_dose_logging`); this test follows the same shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py -k test_0015 -v`
Expected: FAIL — migration `0015` doesn't exist yet (`command.upgrade(cfg, "head")` stays at `0014`,
so the new columns are missing).

- [ ] **Step 3: Add the model fields**

In `app/models.py`'s `InventoryItem` class, add alongside its other optional fields (near
`expiration_date`):

```python
    low_stock_threshold: Mapped[int | None] = mapped_column(Integer)  # None -> User.low_stock_default
```

In `app/models.py`'s `User` class, add alongside `default_discard_days`:

```python
    low_stock_default: Mapped[int | None] = mapped_column(Integer)  # None -> 5 at render time
    shipment_delay_days: Mapped[int | None] = mapped_column(Integer)  # None -> 21 at render time
```

- [ ] **Step 4: Write the migration**

Create `migrations/versions/0015_dashboard_thresholds.py` (follow `0014_dose_logging.py`'s exact
style — `op.batch_alter_table` for SQLite):

```python
"""dashboard thresholds: InventoryItem.low_stock_threshold, User.low_stock_default/shipment_delay_days

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('low_stock_threshold', sa.Integer(), nullable=True))
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('low_stock_default', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('shipment_delay_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('shipment_delay_days')
        batch_op.drop_column('low_stock_default')
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_column('low_stock_threshold')
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_migrations.py -k test_0015 -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass — this is a purely additive, nullable-column migration; nothing existing should
break.

- [ ] **Step 7: Commit**

```bash
git add app/models.py migrations/versions/0015_dashboard_thresholds.py tests/test_migrations.py
git commit -m "feat: add InventoryItem/User columns for dashboard alert thresholds"
```

---

### Task 2: Settings and Inventory form wiring

**Files:**
- Modify: `app/routers/settings.py`
- Modify: `app/templates/settings/settings.html`
- Modify: `app/routers/inventory.py`
- Modify: `app/templates/inventory/list.html`
- Test: `tests/test_settings.py`, `tests/test_inventory.py`

**Interfaces:**
- Consumes: `User.low_stock_default`/`shipment_delay_days`, `InventoryItem.low_stock_threshold`
  from Task 1.
- Produces: `POST /settings/dashboard-thresholds`, and `low_stock_threshold` accepted by the
  existing inventory item add/edit form parsing — Task 3's alerts module reads all three fields by
  name regardless of how they were set, so this task only needs to make them user-editable.

This task has no verbatim brief code for two reasons: (1) the exact form-field parsing helper names
in `app/routers/inventory.py` (e.g. `_parse_item_fields`) must be read directly since they differ
per medium/category, and a wrong guess here would silently break other fields; (2) `settings.html`'s
error-rendering macro (`{{ err(...) }}`) is already established — read the file's own
`/settings/discard-window` form (lines ~94-102) as the exact template to copy, adjusting only the
field name, label, and default value.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_settings.py` (read the file first for its `client`/`db` fixture conventions and
how `/settings/discard-window` is already tested — mirror that test exactly):

```python
def test_dashboard_thresholds_saved(client, db):
    r = client.post("/settings/dashboard-thresholds", data={
        "low_stock_default": "3", "shipment_delay_days": "14",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        me = s.scalar(select(User).where(User.username_key == "tester"))
        assert me.low_stock_default == 3
        assert me.shipment_delay_days == 14


def test_dashboard_thresholds_rejects_non_positive(client, db):
    r = client.post("/settings/dashboard-thresholds", data={
        "low_stock_default": "0", "shipment_delay_days": "14",
    })
    assert r.status_code == 422
```

Add to `tests/test_inventory.py` (find an existing Medicine-item add/edit test and mirror its form
payload, adding the one new field):

```python
def test_low_stock_threshold_saved_including_zero(client, db):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized",
        "vial_size_mg": "10", "low_stock_threshold": "0",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        assert item.low_stock_threshold == 0  # explicit 0 must round-trip, not become None
```

Adjust the exact required fields in that POST payload to match whatever `_create_item`/the add-item
route actually requires (check an existing passing test in the same file for the minimal valid
payload) — the point is only the added `low_stock_threshold` field and its `== 0` assertion.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_settings.py -k dashboard_thresholds -v` and
`pytest tests/test_inventory.py -k low_stock_threshold -v`
Expected: FAIL — route/field don't exist yet.

- [ ] **Step 3: Add the Settings route**

In `app/routers/settings.py`, add a new route following `change_discard_window`'s exact shape
(read it first — same file, already found at the route `POST /settings/discard-window`):

```python
@router.post("/settings/dashboard-thresholds")
async def change_dashboard_thresholds(request: Request, session: Session = Depends(get_session),
                                      uid: int = Depends(current_user_id)):
    form = await request.form()
    errors = {}
    parsed = {}
    for field in ("low_stock_default", "shipment_delay_days"):
        raw = str(form.get(field, "")).strip()
        try:
            value = int(raw)
            if value <= 0:
                raise ValueError
            parsed[field] = value
        except ValueError:
            errors[field] = "Enter a whole number of days, greater than 0."
    if errors:
        return _render(request, session, errors=errors, status_code=422)

    me = _me(session, uid)
    me.low_stock_default = parsed["low_stock_default"]
    me.shipment_delay_days = parsed["shipment_delay_days"]
    session.commit()
    return RedirectResponse("/settings", status_code=303)
```

Check `_render`'s and `_me`'s exact signatures already used by `change_discard_window` in this same
file and match them precisely (parameter names/order may differ slightly from this sketch).

- [ ] **Step 4: Add the Settings template fields**

In `app/templates/settings/settings.html`, add a new form block immediately after the existing
`/settings/discard-window` form, in the same `settings-form` style:

```html
<form method="post" action="/settings/dashboard-thresholds" class="settings-form" novalidate>
  <h3 class="small muted">Dashboard alert defaults</h3>
  <p class="small muted">Used when an item doesn't have its own low-stock number set, and for
    flagging a shipment that's taking longer than usual.</p>
  <label class="field {{ 'has-error' if errors.low_stock_default }}">
    <span>Low stock default (vials)</span>
    <input name="low_stock_default" type="number" min="1" step="1" inputmode="numeric"
          value="{{ me.low_stock_default or 5 }}">
    {{ err('low_stock_default') }}
  </label>
  <label class="field {{ 'has-error' if errors.shipment_delay_days }}">
    <span>Flag a shipment after (days)</span>
    <input name="shipment_delay_days" type="number" min="1" step="1" inputmode="numeric"
          value="{{ me.shipment_delay_days or 21 }}">
    {{ err('shipment_delay_days') }}
  </label>
  <button type="submit" class="btn btn-primary">Save</button>
</form>
```

- [ ] **Step 5: Add the Inventory form field**

Read `app/routers/inventory.py`'s item add/edit form-parsing function (used by both `POST
/inventory` and the edit route) and add `low_stock_threshold` parsing alongside its other optional
numeric fields (e.g. near `vial_size_mg`), being careful to preserve `0` rather than falsy-coercing
it to `None`:

```python
raw_threshold = str(raw.get("low_stock_threshold", "")).strip()
values["low_stock_threshold"] = int(raw_threshold) if raw_threshold else None
```

Wire `values["low_stock_threshold"]` into wherever this function's return dict is applied to the
`InventoryItem` object (same place `vial_size_mg` etc. are applied). Add the corresponding
`<input name="low_stock_threshold" type="number" min="0" step="1">` field to the Medicine/BAC-Water
item form section of `app/templates/inventory/list.html`, alongside the item's other optional
numeric fields — read that section first to match its exact markup style (labels, `data-medium`
conditional visibility if any).

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_settings.py tests/test_inventory.py -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 8: Commit**

```bash
git add app/routers/settings.py app/templates/settings/settings.html app/routers/inventory.py app/templates/inventory/list.html tests/test_settings.py tests/test_inventory.py
git commit -m "feat: make dashboard alert thresholds user-editable"
```

---

### Task 3: Alerts module

**Files:**
- Create: `app/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Consumes: `InventoryItem`, `ActiveVial`, `Order` (Task 1's new columns).
- Produces: `low_stock_alerts()`, `expiration_alerts()`, `shipment_alerts()` — Task 4's dashboard
  route calls all three by name.

This task is pure logic: each function takes the caller's already-loaded rows and a `today` date,
and returns a list of dicts — no database queries inside `app/alerts.py` itself (the router builds
the queries, scoped to whichever viewer is selected; this module only classifies rows it's handed).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_alerts.py`:

```python
from datetime import date, timedelta

from app.alerts import EXPIRING_SOON_DAYS, expiration_alerts, low_stock_alerts, shipment_alerts


class _Item:
    def __init__(self, id, name, available_count, low_stock_threshold, expiration_date=None):
        self.id = id
        self.name = name
        self.available_count = available_count
        self.low_stock_threshold = low_stock_threshold
        self.expiration_date = expiration_date


class _Vial:
    def __init__(self, id, item_name, discard_by, discarded_at=None):
        self.id = id
        self.item_name = item_name
        self.discard_by = discard_by
        self.discarded_at = discarded_at


class _Order:
    def __init__(self, id, vendor, order_date, shipped_date, arrival_date):
        self.id = id
        self.vendor = vendor
        self.order_date = order_date
        self.shipped_date = shipped_date
        self.arrival_date = arrival_date


def test_low_stock_uses_item_threshold_over_default():
    items = [_Item(1, "A", available_count=2, low_stock_threshold=3)]
    alerts = low_stock_alerts(items, default_threshold=5)
    assert len(alerts) == 1 and alerts[0]["item_id"] == 1


def test_low_stock_explicit_zero_is_not_unset():
    # available_count=0, threshold explicitly 0 -> 0 <= 0 -> still alerts (item genuinely out).
    items = [_Item(1, "A", available_count=0, low_stock_threshold=0)]
    alerts = low_stock_alerts(items, default_threshold=5)
    assert len(alerts) == 1


def test_low_stock_falls_back_to_default_when_threshold_none():
    items = [_Item(1, "A", available_count=4, low_stock_threshold=None)]
    assert low_stock_alerts(items, default_threshold=5) == [
        {"item_id": 1, "name": "A", "available_count": 4, "threshold": 5}
    ]
    assert low_stock_alerts(items, default_threshold=3) == []


def test_expiration_soon_and_expired_vials():
    today = date(2026, 9, 26)
    vials = [
        _Vial(1, "A", discard_by=today - timedelta(days=1)),  # expired
        _Vial(2, "B", discard_by=today + timedelta(days=EXPIRING_SOON_DAYS)),  # soon (boundary)
        _Vial(3, "C", discard_by=today + timedelta(days=EXPIRING_SOON_DAYS + 1)),  # not yet
        _Vial(4, "D", discard_by=today - timedelta(days=5), discarded_at=today),  # already discarded, skip
    ]
    alerts = expiration_alerts(vials=vials, items=[], today=today)
    by_id = {a["id"]: a["severity"] for a in alerts}
    assert by_id == {1: "expired", 2: "soon"}


def test_expiration_covers_sealed_stock_too():
    today = date(2026, 9, 26)
    items = [_Item(1, "A", available_count=1, low_stock_threshold=None,
                   expiration_date=today - timedelta(days=1))]
    alerts = expiration_alerts(vials=[], items=items, today=today)
    assert alerts == [{"kind": "item", "id": 1, "name": "A", "severity": "expired"}]


def test_expiration_ignores_depleted_sealed_stock():
    today = date(2026, 9, 26)
    items = [_Item(1, "A", available_count=0, low_stock_threshold=None,
                   expiration_date=today - timedelta(days=1))]
    assert expiration_alerts(vials=[], items=items, today=today) == []


def test_shipment_over_threshold_uses_shipped_date_when_present():
    today = date(2026, 9, 26)
    orders = [_Order(1, "VendorX", order_date=today - timedelta(days=30),
                     shipped_date=today - timedelta(days=25), arrival_date=None)]
    alerts = shipment_alerts(orders, today=today, threshold_days=21)
    assert len(alerts) == 1 and alerts[0]["order_id"] == 1


def test_shipment_falls_back_to_order_date_when_unshipped():
    today = date(2026, 9, 26)
    orders = [_Order(1, "VendorX", order_date=today - timedelta(days=25),
                     shipped_date=None, arrival_date=None)]
    assert len(shipment_alerts(orders, today=today, threshold_days=21)) == 1


def test_shipment_arrived_never_alerts():
    today = date(2026, 9, 26)
    orders = [_Order(1, "VendorX", order_date=today - timedelta(days=60),
                     shipped_date=today - timedelta(days=55), arrival_date=today - timedelta(days=1))]
    assert shipment_alerts(orders, today=today, threshold_days=21) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_alerts.py -v`
Expected: FAIL — `ModuleNotFoundError: app.alerts`.

- [ ] **Step 3: Write `app/alerts.py`**

```python
"""Dashboard alerts: low stock, expiration (vials + sealed stock), shipments running long. Pure
functions -- callers pass already-scoped-to-one-viewer rows; nothing here queries a database."""

from datetime import date

EXPIRING_SOON_DAYS = 7


def low_stock_alerts(items, default_threshold: int) -> list[dict]:
    """`items` need `.id`, `.name`, `.available_count`, `.low_stock_threshold`. None on the item's
    own threshold means "use default_threshold" -- 0 is a real, valid threshold, never coerced."""
    out = []
    for item in items:
        threshold = item.low_stock_threshold if item.low_stock_threshold is not None else default_threshold
        if item.available_count <= threshold:
            out.append({"item_id": item.id, "name": item.name,
                       "available_count": item.available_count, "threshold": threshold})
    return out


def expiration_alerts(*, vials, items, today: date) -> list[dict]:
    """`vials` need `.id`, `.item_name`, `.discard_by`, `.discarded_at` (skip if not None -- already
    discarded). `items` need `.id`, `.name`, `.available_count`, `.expiration_date` (skip if None or
    available_count <= 0 -- nothing left to expire). Same two-threshold rule for both: `< today` is
    'expired', `today <= date <= today + EXPIRING_SOON_DAYS` is 'soon'."""
    out = []
    for vial in vials:
        if vial.discarded_at is not None:
            continue
        severity = _severity(vial.discard_by, today)
        if severity:
            out.append({"kind": "vial", "id": vial.id, "name": vial.item_name, "severity": severity})
    for item in items:
        if item.expiration_date is None or item.available_count <= 0:
            continue
        severity = _severity(item.expiration_date, today)
        if severity:
            out.append({"kind": "item", "id": item.id, "name": item.name, "severity": severity})
    return out


def _severity(target_date: date, today: date) -> str | None:
    if target_date < today:
        return "expired"
    if target_date <= today + timedelta(days=EXPIRING_SOON_DAYS):
        return "soon"
    return None


def shipment_alerts(orders, *, today: date, threshold_days: int) -> list[dict]:
    """`orders` need `.id`, `.vendor`, `.order_date`, `.shipped_date`, `.arrival_date`. Only
    unarrived orders (`arrival_date is None`) are ever eligible -- an order that arrived late but
    was checked in never alerts, no matter how long it took."""
    out = []
    for order in orders:
        if order.arrival_date is not None:
            continue
        anchor = order.shipped_date or order.order_date
        if (today - anchor).days > threshold_days:
            out.append({"order_id": order.id, "vendor": order.vendor, "days": (today - anchor).days})
    return out
```

Add `from datetime import timedelta` to the top-level import (used by `_severity`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_alerts.py -v`
Expected: All PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions (this module isn't wired into any route yet).

- [ ] **Step 6: Commit**

```bash
git add app/alerts.py tests/test_alerts.py
git commit -m "feat: add pure-function dashboard alerts module (low stock, expiration, shipment delay)"
```

---

### Task 4: Dashboard router, template, and homepage switch (own data only)

**Files:**
- Create: `app/routers/dashboard.py`
- Create: `app/templates/dashboard/index.html`
- Modify: `app/main.py` (register router)
- Modify: `app/routers/auth.py` (`HOME` constant)
- Modify: `app/templates/base.html` (nav link)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `app.alerts`'s three functions (Task 3); `occurrences()`/`DoseLog` (existing,
  Daily Dosing); `OrderItem.total_cost` (existing).
- Produces: `GET /dashboard` — Task 5 extends this same route with the viewer-switcher query
  param; this task builds the "viewing yourself" path only.

This task deliberately does NOT touch the viewer switcher yet (Task 5) — every widget here queries
only `request.state.user.id`'s own data, matching every other page's existing single-owner query
shape before sharing enters the picture.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard.py` (read `tests/test_dosing.py`'s top-of-file imports and
`_setup_protocol_with_vial` helper first — reuse that helper rather than re-deriving protocol/vial
setup from scratch; import it or copy its exact shape into this file, matching this codebase's
established test-fixture conventions, e.g. pinning `uid` via `username_key == "tester"`):

```python
import html
from datetime import date, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActiveVial, Category, InventoryItem, Medium, Order, OrderItem, User


def test_dashboard_shows_todays_schedule(client, db):
    # Reuse a helper equivalent to test_dosing.py's _setup_protocol_with_vial to create a due item.
    ...  # implementer: adapt the existing helper's setup, then:
    t = html.unescape(client.get("/dashboard").text)
    assert "Retatrutide" in t


def test_dashboard_low_stock_alert_respects_explicit_zero(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        item = InventoryItem(owner_id=uid, name="Retatrutide", category=Category.MEDICINE,
                             medium=Medium.LYOPHILIZED, vial_size_mg=10, count=0,
                             low_stock_threshold=0)
        s.add(item)
        s.commit()
    t = html.unescape(client.get("/dashboard").text)
    assert "Retatrutide" in t  # available_count=0 <= threshold=0 -> alerts


def test_dashboard_shipment_alert_shows_when_over_threshold(client, db):
    with SessionLocal() as s:
        uid = s.scalar(select(User.id).where(User.username_key == "tester"))
        s.add(Order(vendor="VendorX", order_date=date.today() - timedelta(days=30),
                    tax_cents=None, shipping_cents=None))
        s.commit()
    t = html.unescape(client.get("/dashboard").text)
    assert "VendorX" in t


def test_dashboard_placeholder_cards_present(client, db):
    t = html.unescape(client.get("/dashboard").text)
    assert "Weight" in t and "Journal" in t


def test_home_redirects_to_dashboard(client, db):
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/dashboard"
```

Fill in the first test's setup by reading `tests/test_dosing.py`'s `_setup_protocol_with_vial`
directly and reusing its pattern (a `Protocol` + `ProtocolItem` due today). Adjust the Order test's
required fields by checking `Order`'s actual non-nullable columns.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard.py -v`
Expected: FAIL — `404 Not Found` (route doesn't exist), and the home-redirect test fails since
`HOME` is still `/protocols`.

- [ ] **Step 3: Add the router**

Create `app/routers/dashboard.py`:

```python
"""The Dashboard: the app's homepage -- today's schedule, alerts, cost/adherence snapshots, and
placeholders for not-yet-built widgets. Read-only; every widget reuses an existing query shape."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.alerts import expiration_alerts, low_stock_alerts, shipment_alerts
from app.auth.deps import current_user_id
from app.calendar.schedule import occurrences
from app.db import get_session
from app.models import (
    ActiveVial, Category, DoseLog, DoseStatus, InventoryItem, Order, OrderItem, Protocol,
    ProtocolItem, User,
)
from app.protocols.status import Status, protocol_status
from app.routers.protocols import get_today
from app.templating import templates

router = APIRouter()


def _todays_schedule(session: Session, uid: int, today: date) -> list[dict]:
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.steps),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    occs = occurrences(protocols, today, today)
    due = [(occ, item) for occ in occs for item in occ.items]
    logs = {dl.protocol_item_id: dl for dl in session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date == today))}
    rows = []
    for occ, item in due:
        log = logs.get(item.protocol_item_id)
        status = "Due"
        if log is not None:
            status = "Logged" if log.status in (DoseStatus.ON_TIME, DoseStatus.LATE) else "Skipped"
        rows.append({"peptide": item.peptide, "dose": item.dose, "unit": item.unit,
                    "time_of_day": item.time_of_day, "status": status})
    return rows


def _adherence_pct(session: Session, uid: int, today: date) -> int | None:
    since = today - timedelta(days=30)
    logs = session.scalars(
        select(DoseLog).where(DoseLog.owner_id == uid, DoseLog.scheduled_date >= since,
                              DoseLog.scheduled_date <= today)).all()
    if not logs:
        return None
    on_time_or_late = sum(1 for l in logs if l.status in (DoseStatus.ON_TIME, DoseStatus.LATE))
    return round(100 * on_time_or_late / len(logs))


def _cost_snapshot(session: Session, uid: int, today: date) -> list[dict]:
    protocols = session.scalars(
        select(Protocol).where(Protocol.owner_id == uid).options(
            selectinload(Protocol.items).selectinload(ProtocolItem.peptide),
            selectinload(Protocol.items).selectinload(ProtocolItem.inventory_item),
        )).all()
    active_items = [
        it for p in protocols if protocol_status(p, today) is Status.ACTIVE
        for it in p.items if it.inventory_item_id is not None
    ]
    rows = []
    seen_item_ids = set()
    for it in active_items:
        if it.inventory_item_id in seen_item_ids:
            continue
        seen_item_ids.add(it.inventory_item_id)
        line = session.scalar(
            select(OrderItem).join(Order)
            .where(OrderItem.inventory_item_id == it.inventory_item_id, Order.arrival_date.is_not(None))
            .order_by(Order.arrival_date.desc()))
        if line is None or line.total_cost is None or not line.received_quantity:
            continue
        cost_per_vial = line.total_cost / line.received_quantity
        vial = session.scalar(
            select(ActiveVial).where(ActiveVial.inventory_item_id == it.inventory_item_id,
                                     ActiveVial.discarded_at.is_(None))
            .order_by(ActiveVial.id.desc()))
        cost_per_dose = cost_per_vial / vial.doses_total if vial and vial.doses_total else None
        rows.append({"peptide": it.peptide.name, "cost_per_vial": cost_per_vial,
                    "cost_per_dose": cost_per_dose})
    return rows


@router.get("/dashboard")
def dashboard(request: Request, session: Session = Depends(get_session), today: date = Depends(get_today),
             uid: int = Depends(current_user_id)):
    threshold_items = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid,
                                    InventoryItem.category.in_([Category.MEDICINE, Category.BAC_WATER]))).all()
    viewer = session.get(User, uid)
    default_threshold = viewer.low_stock_default if viewer.low_stock_default is not None else 5
    delay_days = viewer.shipment_delay_days if viewer.shipment_delay_days is not None else 21

    vials = session.scalars(
        select(ActiveVial).where(ActiveVial.owner_id == uid, ActiveVial.discarded_at.is_(None))).all()
    for v in vials:
        v.item_name = v.inventory_item.name  # convenience attr expected by app.alerts

    # Orders have no owner_id of their own -- scope via their line items' linked InventoryItem.
    order_ids = {li.order_id for li in session.scalars(
        select(OrderItem).join(InventoryItem).where(InventoryItem.owner_id == uid))}
    orders = session.scalars(select(Order).where(Order.id.in_(order_ids))).all() if order_ids else []

    alerts = {
        "low_stock": low_stock_alerts(threshold_items, default_threshold),
        "expiration": expiration_alerts(vials=vials, items=threshold_items, today=today),
        "shipment": shipment_alerts(orders, today=today, threshold_days=delay_days),
    }

    return templates.TemplateResponse(request, "dashboard/index.html", {
        "schedule": _todays_schedule(session, uid, today),
        "alerts": alerts,
        "cost_snapshot": _cost_snapshot(session, uid, today),
        "adherence_pct": _adherence_pct(session, uid, today),
        "today": today,
    })
```

Read this back before running anything: confirm every name resolves against the imports at the top
of the file, and that `_cost_snapshot` (below) queries each active protocol item's linked
`InventoryItem`'s most recent checked-in `OrderItem` via `OrderItem`/`Order` directly (no indirect
`.__class__` lookups).

- [ ] **Step 4: Add the template**

Create `app/templates/dashboard/index.html`:

```html
{% extends "base.html" %}
{% set active_nav = "dashboard" %}
{% block title %}Dashboard{% endblock %}

{% block content %}
<div class="page-head"><h1>Dashboard</h1></div>

<section aria-labelledby="schedule-heading">
  <h2 id="schedule-heading" class="section-title">Today's Schedule</h2>
  {% if not schedule %}
  <p class="muted">Nothing scheduled today.</p>
  {% else %}
  <ul class="dose-list">
    {% for row in schedule %}
    <li><strong>{{ row.peptide }}</strong> — {{ row.dose }} {{ row.unit }} · {{ row.time_of_day.label }}
      <span class="tag">{{ row.status }}</span></li>
    {% endfor %}
  </ul>
  {% endif %}
  <a href="/today" class="btn btn-ghost">View full Today page</a>
</section>

<section aria-labelledby="alerts-heading">
  <h2 id="alerts-heading" class="section-title">Alerts</h2>
  {% set has_alerts = alerts.low_stock or alerts.expiration or alerts.shipment %}
  {% if not has_alerts %}
  <p class="muted">Nothing needs attention.</p>
  {% else %}
  <ul class="dose-list">
    {% for a in alerts.low_stock %}
    <li><a href="/inventory">{{ a.name }}</a> — low stock ({{ a.available_count }} left, threshold {{ a.threshold }})</li>
    {% endfor %}
    {% for a in alerts.expiration %}
    <li><a href="/inventory#active-vials">{{ a.name }}</a> — {{ 'expired' if a.severity == 'expired' else 'expiring soon' }}</li>
    {% endfor %}
    {% for a in alerts.shipment %}
    <li>Order from {{ a.vendor or 'vendor' }} — {{ a.days }} days, no arrival yet</li>
    {% endfor %}
  </ul>
  {% endif %}
</section>

<section aria-labelledby="cost-heading">
  <h2 id="cost-heading" class="section-title">Cost snapshot</h2>
  {% if not cost_snapshot %}
  <p class="muted">No cost data yet.</p>
  {% else %}
  <table class="inv-table">
    <thead><tr><th>Peptide</th><th>Cost / vial</th><th>Cost / dose</th></tr></thead>
    <tbody>
      {% for row in cost_snapshot %}
      <tr><td>{{ row.peptide }}</td><td>{{ row.cost_per_vial | money }}</td>
        <td>{{ row.cost_per_dose | money if row.cost_per_dose is not none else '—' }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
  {% endif %}
</section>

<section aria-labelledby="adherence-heading">
  <h2 id="adherence-heading" class="section-title">Adherence (last 30 days)</h2>
  <p>{{ (adherence_pct | string) + '%' if adherence_pct is not none else 'No doses logged yet.' }}</p>
</section>

<section class="dashboard-placeholders">
  <div class="card"><h3>Weight & Measurements</h3><p class="muted">Coming in a future update.</p></div>
  <div class="card"><h3>Journal</h3><p class="muted">Coming in a future update.</p></div>
  <div class="card"><h3>Health app integrations</h3><p class="muted">Coming in a future update.</p></div>
</section>
{% endblock %}
```

Check whether a `money` Jinja filter already exists (used elsewhere for cents-to-dollars display,
e.g. in `inventory/detail.html`) and reuse its exact name — don't invent a new one if one exists.

- [ ] **Step 5: Register the router, switch HOME, add nav link**

In `app/main.py`:

```python
from app.routers import auth, backup, calculator, calendar, dashboard, dosing, inventory, library, protocols, settings
...
app.include_router(dashboard.router)
```

In `app/routers/auth.py`, change:

```python
HOME = "/dashboard"  # the front page
```

In `app/templates/base.html`, add a "Dashboard" link as the FIRST nav item:

```html
<a href="/dashboard" class="{{ 'active' if active == 'dashboard' }}">Dashboard</a>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_dashboard.py -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass. Pay particular attention to any test that asserted the OLD post-login/HOME
redirect target was `/protocols` (search the test suite for `"/protocols"` in redirect assertions)
— those must now expect `/dashboard`, and that's a real, intentional behavior change to fix in the
test, not a regression to work around.

- [ ] **Step 8: Commit**

```bash
git add app/routers/dashboard.py app/templates/dashboard/index.html app/main.py app/routers/auth.py app/templates/base.html tests/test_dashboard.py
git commit -m "feat: add Dashboard homepage (own-data view: schedule, alerts, cost, adherence)"
```

---

### Task 5: Viewer switcher (shared users)

**Files:**
- Modify: `app/routers/dashboard.py`
- Modify: `app/templates/dashboard/index.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `Share`/`ShareCategory` (existing model); Task 4's `dashboard()` route and its helper
  functions (extends each with a `viewer_id`/category-gating parameter rather than hardcoding `uid`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dashboard.py` (create a second user and a `Share` row, following
`test_shared_active_vial_discard_restricted_to_owner`'s pattern in `tests/test_active_vials.py` for
how this codebase already sets up a second registered user + a `Share` row):

```python
def test_viewer_dropdown_lists_users_who_shared_with_me(client, db):
    # implementer: register a second user, have them create a Share(owner_id=other, grantee_id=tester,
    # category=INVENTORY), then assert their username appears in the dashboard's viewer dropdown.
    ...


def test_switching_viewer_shows_only_that_users_data_not_blended():
    # implementer: with an Inventory-only share from `other` to `tester`, and `other` having a
    # low-stock item while `tester` doesn't, GET /dashboard?viewer_id=<other.id> and assert the
    # alert appears; GET /dashboard (default, self) and assert it does NOT appear.
    ...


def test_viewer_without_personal_data_share_hides_schedule_widget():
    # implementer: Inventory-only share (no PERSONAL_DATA) -> viewing that user's dashboard must
    # omit the Today's Schedule and Adherence sections entirely (not render them empty).
    ...


def test_revoked_share_removes_viewer_option():
    # implementer: create then delete a Share row; assert the now-revoked user no longer appears
    # in the dropdown and GET /dashboard?viewer_id=<their-id> falls back to self (403 or silent
    # ignore -- pick one and assert it, don't leave it undefined).
    ...
```

Fill in each `...` using this codebase's established pattern for registering a second test user and
creating a `Share` row (grep `tests/test_active_vials.py` and `tests/test_inventory.py` for existing
examples — several already exist for the Inventory category; adapt for `PERSONAL_DATA` where
needed).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard.py -k viewer -v`
Expected: FAIL — no `viewer_id` param exists yet, no dropdown rendered.

- [ ] **Step 3: Extend the route**

In `app/routers/dashboard.py`'s `dashboard()` route, add a `viewer_id: int | None =
Query(None)` parameter (import `Query` from `fastapi`). Add a helper:

```python
def _resolve_viewer(session: Session, uid: int, viewer_id: int | None) -> tuple[int, set[ShareCategory]]:
    """Returns (effective_viewer_id, categories_shared_by_that_viewer_with_uid). Falls back to
    (uid, {both categories}) when viewer_id is None or the share no longer exists -- a revoked
    share silently reverts to self rather than erroring, since the dropdown itself won't offer a
    stale option on the next render anyway."""
    if viewer_id is None or viewer_id == uid:
        return uid, {ShareCategory.INVENTORY, ShareCategory.PERSONAL_DATA}
    categories = set(session.scalars(
        select(Share.category).where(Share.owner_id == viewer_id, Share.grantee_id == uid)))
    if not categories:
        return uid, {ShareCategory.INVENTORY, ShareCategory.PERSONAL_DATA}
    return viewer_id, categories


def _shared_with_me(session: Session, uid: int) -> list[dict]:
    rows = session.execute(
        select(Share.owner_id, User.username).join(User, User.id == Share.owner_id)
        .where(Share.grantee_id == uid).distinct()).all()
    return [{"id": owner_id, "username": username} for owner_id, username in rows]
```

Add `Share`, `ShareCategory` to the existing `from app.models import (...)` line at the top of the
file, and `from fastapi import APIRouter, Depends, Query, Request`.

In `dashboard()`, call `effective_uid, categories = _resolve_viewer(session, uid, viewer_id)` first,
then use `effective_uid` everywhere `uid` was previously used inside the widget-building calls
(`_todays_schedule`, `_adherence_pct`, the low-stock/expiration/shipment queries, `_cost_snapshot`).
Gate which widgets are computed at all by `categories`:

```python
    schedule = _todays_schedule(session, effective_uid, today) if ShareCategory.PERSONAL_DATA in categories else None
    adherence_pct = _adherence_pct(session, effective_uid, today) if ShareCategory.PERSONAL_DATA in categories else None
    alerts = None
    cost_snapshot = None
    if ShareCategory.INVENTORY in categories:
        alerts = {...}  # existing block, using effective_uid
        cost_snapshot = _cost_snapshot(session, effective_uid, today)
```

Pass `viewer_id=effective_uid`, `shared_with_me=_shared_with_me(session, uid)` (always the REAL
`uid`'s own share list, never `effective_uid`'s — the dropdown always reflects who shared with the
person actually logged in) into the template context.

- [ ] **Step 4: Add the dropdown to the template**

In `app/templates/dashboard/index.html`, add near the top (only rendered when there's something to
show):

```html
{% if shared_with_me %}
<form method="get" action="/dashboard" class="dashboard-viewer">
  <label>
    <span class="small muted">Viewing</span>
    <select name="viewer_id" onchange="this.form.submit()">
      <option value="">You</option>
      {% for u in shared_with_me %}
      <option value="{{ u.id }}" {{ 'selected' if viewer_id == u.id }}>{{ u.username }}</option>
      {% endfor %}
    </select>
  </label>
</form>
{% endif %}
```

Wrap the Today's Schedule and Adherence `<section>` blocks in `{% if schedule is not none %}`/
`{% if adherence_pct is not none or ... %}` guards matching the gating above (a `None` schedule
means "not shared," which is different from an empty list meaning "shared but nothing due" — don't
conflate the two). Same for the Alerts/Cost-snapshot sections gated on `alerts is not none`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard.py -v`
Expected: All PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add app/routers/dashboard.py app/templates/dashboard/index.html tests/test_dashboard.py
git commit -m "feat: add single-person viewer switcher to the Dashboard for shared users"
```

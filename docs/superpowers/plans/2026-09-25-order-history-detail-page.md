# Order History & Item Detail Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace InventoryItem's single order_date/shipped_date/arrival_date/vendor/cost/lot/COA
fields with real per-lot Order history, give every item a detail page, split items into
Medicine/BAC Water/Supply categories, and resection the Inventory list around those categories
plus a new In-Transit view.

**Architecture:** A new `Order` table (one item -> many orders) carries everything that varies by
shipment (quantity, dates, tracking, vendor, cost/tax/shipping, lot #, expiration, COA). A
computed `InventoryItem.available_count` property replaces the raw `count` column for
Medicine/BAC Water (Supply keeps the plain column). The Inventory list groups items by category
into sections and gains an In-Transit section fed by orders with no arrival date yet. A new item
detail page (`GET /inventory/{id}`) hosts Details, Inventory, and Order History.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 ORM, Alembic (SQLite), Jinja2, vanilla JS — unchanged from
the rest of this codebase.

**Spec:** [docs/superpowers/specs/2026-09-25-order-history-detail-page-design.md](../specs/2026-09-25-order-history-detail-page-design.md)

## Global Constraints

- Money is stored as integer cents everywhere (matches `InventoryItem.cost_cents` today).
- Dates are naive `date` objects, ISO-8601 strings on the wire — matches the rest of this app.
- `Category` values are the literal strings `"Medicine"`, `"BAC Water"`, `"Supply"` (a plain
  `str, enum.Enum` like `Medium`, not a `LabeledEnum` — no separate display label needed, the
  value already reads correctly).
- A Medicine item's `category` and `medium`/`vial_size_unit`/etc. are set at creation and are
  **read-only afterward** — editing an item never changes its category (delete and re-add to
  recategorize, per the spec).
- Editing an existing item (Details) never creates or touches an Order. Orders are only created
  by "Add order" (this plan's Task 5) or the item's first order at creation time (Task 2).
- Every new/changed route that touches another user's item must go through the existing
  `_own_item`/`_visible_item` helpers in `app/routers/inventory.py` — never a bare
  `session.get(InventoryItem, id)` on a route reachable by a non-owner.
- SQLite migrations in this repo use `op.batch_alter_table` for simple add/drop-column, and raw
  `text()` SQL via `op.get_bind()` only when batch mode would lose something (COLLATE, as seen in
  `migrations/versions/0009_sharing.py`) or when doing a data backfill. This migration needs raw
  SQL for the data backfill step, batch mode for column add/drop.

## Review Focus

- An existing Lyophilized item with real vendor/cost/lot/COA/order-arrival data migrates into
  exactly one already-arrived Order, and `available_count` after migration equals its
  pre-migration `count` — a reasonable person restoring from an old install expects their stock
  numbers to be unchanged after upgrading.
- An item with two Orders, one arrived and one still in-transit, must not double-count or
  under-count: `available_count` reflects only the arrived one, and the in-transit one shows
  under In-Transit and nowhere else as "stock".
- Reconstituting against an item whose `available_count` is 0 because everything's still
  in-transit (not because it's truly out of stock) must be blocked the same way a truly
  zero-stock item is blocked — a reasonable person shouldn't be able to reconstitute a vial that
  hasn't arrived yet.
- A shared Medicine item's Order History (vendor, cost, tracking, COA) is visible read-only to a
  grantee, matching how the rest of this item's fields are already shared, but never editable by
  them, and the "Add order"/edit-order actions must 404 for a non-owner even if they navigate the
  URL directly.
- BAC Water items must never show a Reconstitute control and must never be selectable in the
  Calculator's inventory dropdown, even though they can otherwise look just like a Medicine item
  (quantity, orders, arrival) — the gate must check `category == Category.MEDICINE` explicitly,
  not infer it from which fields happen to be set.

---

## Task 1: Data model — `Category`, `Order`, derived `available_count`, migration 0011

**Files:**
- Modify: `app/models.py` (add `Category` enum after `Medium`; add `Order` class after
  `InventoryItem`; add `category`/`reconstituted_count`/`sold_count`/`orders` to `InventoryItem`;
  add `available_count` property)
- Create: `migrations/versions/0011_order_history.py`
- Test: `tests/test_migrations.py` (new test, following the `test_0010_...` pattern at line 209)
- Test: `tests/test_inventory.py` (new tests for `available_count`)

**Interfaces:**
- Produces: `Category` enum (`MEDICINE`, `BAC_WATER`, `SUPPLY` with values `"Medicine"`,
  `"BAC Water"`, `"Supply"`); `Order` model with fields `id, inventory_item_id, quantity,
  order_date, shipped_date, arrival_date, tracking_site, tracking_number, vendor, vendor_id,
  lot_number, cost_cents, tax_cents, shipping_cents, expiration_date, coa_filename,
  coa_vial_size_mg, coa_purity_pct, created_at` plus `.cost`/`.tax`/`.shipping` float properties;
  `InventoryItem.category`, `.reconstituted_count`, `.sold_count`, `.orders` (relationship,
  newest-order_date-first), `.available_count` property.
- Consumes: nothing from other tasks (this is the foundation).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inventory.py -- add near the top-level tests
from app.models import Category, Order

def test_available_count_supply_is_the_plain_count_column(db, me):
    item = InventoryItem(owner_id=me, name="Alcohol Pads", category=Category.SUPPLY, count=250)
    db.add(item)
    db.commit()
    assert item.available_count == 250


def test_available_count_medicine_sums_arrived_orders_minus_reconstituted_and_sold(db, me):
    item = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                         medium=Medium.LYOPHILIZED, vial_size_mg=10, reconstituted_count=2, sold_count=1)
    db.add(item)
    db.flush()
    db.add_all([
        Order(inventory_item_id=item.id, quantity=10, order_date=date(2026, 8, 1), arrival_date=date(2026, 8, 10)),
        Order(inventory_item_id=item.id, quantity=5, order_date=date(2026, 9, 20)),  # not arrived
    ])
    db.commit()
    db.refresh(item)
    assert item.available_count == 10 - 2 - 1  # the in-transit order of 5 doesn't count yet
```

```python
# tests/test_migrations.py -- add after test_0010_adds_active_vials_and_discard_days (after line 227)
def test_0011_adds_orders_and_categorizes_existing_items(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0010")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        # A Lyophilized item with full legacy order/vendor/lot/COA data.
        c.execute("insert into inventory_items(name,count,vial_size_unit,medium,vendor,lot_number,"
                  "cost_cents,order_date,shipped_date,arrival_date,coa_vial_size_mg,coa_purity_pct,"
                  "created_at,updated_at,owner_id) values ('Retatrutide',10,'mg','Lyophilized',"
                  "'PeptideCo','LOT1',8400,'2026-08-01','2026-08-03','2026-08-10',9.48,99.5,"
                  "'2026-08-01','2026-08-01',1)")
        # A no-medium item (today's "supply" pattern), no order data at all.
        c.execute("insert into inventory_items(name,count,vial_size_unit,created_at,updated_at,owner_id)"
                  " values ('Alcohol Prep Pads',250,'mg','2026-09-01','2026-09-01',1)")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(orders)")}
        assert {"inventory_item_id", "quantity", "order_date", "shipped_date", "arrival_date",
               "tracking_site", "tracking_number", "vendor", "vendor_id", "lot_number",
               "cost_cents", "tax_cents", "shipping_cents", "expiration_date", "coa_filename",
               "coa_vial_size_mg", "coa_purity_pct"} <= cols
        item_cols = {r[1] for r in c.execute("pragma table_info(inventory_items)")}
        assert {"category", "reconstituted_count", "sold_count"} <= item_cols
        assert "lot_number" not in item_cols and "arrival_date" not in item_cols

        cat, qty = c.execute("select category,count from inventory_items where name='Retatrutide'").fetchone()
        assert cat == "Medicine"
        order = c.execute("select quantity,vendor,lot_number,arrival_date from orders "
                          "where inventory_item_id=(select id from inventory_items where name='Retatrutide')").fetchone()
        assert order == (10, "PeptideCo", "LOT1", "2026-08-10")

        cat2 = c.execute("select category from inventory_items where name='Alcohol Prep Pads'").fetchone()[0]
        assert cat2 == "Supply"
        no_orders = c.execute("select count(*) from orders where inventory_item_id="
                              "(select id from inventory_items where name='Alcohol Prep Pads')").fetchone()[0]
        assert no_orders == 0
    command.downgrade(cfg, "0010")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "orders" not in tables
        assert "category" not in {r[1] for r in c.execute("pragma table_info(inventory_items)")}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -k available_count tests/test_migrations.py -k 0011 -v`
Expected: FAIL — `Category`/`Order` don't exist yet (ImportError/AttributeError), and revision
`0011` doesn't exist (`command.upgrade(cfg, "head")` stays at 0010).

- [ ] **Step 3: Add `Category`, `Order`, and `InventoryItem` changes to `app/models.py`**

Add right after the `Medium` class (after line 44):

```python
class Category(str, enum.Enum):
    MEDICINE = "Medicine"
    BAC_WATER = "BAC Water"
    SUPPLY = "Supply"
```

In `InventoryItem`, add these mapped columns right after `notes` (after line 133, before
`owner_id`):

```python
    category: Mapped[Category] = mapped_column(_enum_column(Category), default=Category.MEDICINE)
    reconstituted_count: Mapped[int] = mapped_column(Integer, default=0)
    sold_count: Mapped[int] = mapped_column(Integer, default=0)
```

Add the `orders` relationship and `available_count` property right after the existing `cost`
property (after line 141):

```python
    orders: Mapped[list["Order"]] = relationship(
        back_populates="inventory_item", order_by="Order.order_date.desc()", cascade="all, delete-orphan")

    @property
    def available_count(self) -> int:
        """Medicine/BAC Water: arrived-order quantity minus reconstituted/sold. Supply: the plain
        count column. The Inventory list and Calculator read this, never `count` directly, for
        Medicine/BAC Water items."""
        if self.category == Category.SUPPLY:
            return self.count
        arrived = sum(o.quantity for o in self.orders if o.arrival_date is not None)
        return arrived - self.reconstituted_count - self.sold_count
```

Add the `Order` class right after `InventoryItem` (before `ActiveVial`, currently starting at
line 143):

```python
class Order(Base):
    """One shipment/lot of a Medicine or BAC Water InventoryItem. Filling in arrival_date is what
    moves this order's quantity into the item's available_count; until then it shows in the
    Inventory page's In-Transit section. Supply items never have Orders."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_quantity_pos"),
        CheckConstraint("cost_cents IS NULL OR cost_cents >= 0", name="ck_order_cost_nonneg"),
        CheckConstraint("tax_cents IS NULL OR tax_cents >= 0", name="ck_order_tax_nonneg"),
        CheckConstraint("shipping_cents IS NULL OR shipping_cents >= 0", name="ck_order_shipping_nonneg"),
        CheckConstraint("coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0", name="ck_order_coa_vial_size_pos"),
        CheckConstraint("coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)",
                        name="ck_order_coa_purity_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    order_date: Mapped[date] = mapped_column(Date)
    shipped_date: Mapped[date | None] = mapped_column(Date)
    arrival_date: Mapped[date | None] = mapped_column(Date)
    tracking_site: Mapped[str | None] = mapped_column(String(500))
    tracking_number: Mapped[str | None] = mapped_column(String(100))
    vendor: Mapped[str | None] = mapped_column(String(200))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    lot_number: Mapped[str | None] = mapped_column(String(100))
    cost_cents: Mapped[int | None] = mapped_column(Integer)
    tax_cents: Mapped[int | None] = mapped_column(Integer)
    shipping_cents: Mapped[int | None] = mapped_column(Integer)
    expiration_date: Mapped[date | None] = mapped_column(Date)
    coa_filename: Mapped[str | None] = mapped_column(String(100))
    coa_vial_size_mg: Mapped[float | None] = mapped_column(Float)
    coa_purity_pct: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    inventory_item: Mapped["InventoryItem"] = relationship(back_populates="orders")

    @property
    def cost(self) -> float | None:
        return None if self.cost_cents is None else self.cost_cents / 100

    @property
    def tax(self) -> float | None:
        return None if self.tax_cents is None else self.tax_cents / 100

    @property
    def shipping(self) -> float | None:
        return None if self.shipping_cents is None else self.shipping_cents / 100
```

- [ ] **Step 4: Write `migrations/versions/0011_order_history.py`**

```python
"""order history: orders table, item categories

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORY_VALUES = ('Medicine', 'BAC Water', 'Supply')


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('order_date', sa.Date(), nullable=False),
        sa.Column('shipped_date', sa.Date(), nullable=True),
        sa.Column('arrival_date', sa.Date(), nullable=True),
        sa.Column('tracking_site', sa.String(length=500), nullable=True),
        sa.Column('tracking_number', sa.String(length=100), nullable=True),
        sa.Column('vendor', sa.String(length=200), nullable=True),
        sa.Column('vendor_id', sa.Integer(), nullable=True),
        sa.Column('lot_number', sa.String(length=100), nullable=True),
        sa.Column('cost_cents', sa.Integer(), nullable=True),
        sa.Column('tax_cents', sa.Integer(), nullable=True),
        sa.Column('shipping_cents', sa.Integer(), nullable=True),
        sa.Column('expiration_date', sa.Date(), nullable=True),
        sa.Column('coa_filename', sa.String(length=100), nullable=True),
        sa.Column('coa_vial_size_mg', sa.Float(), nullable=True),
        sa.Column('coa_purity_pct', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('quantity > 0', name='ck_order_quantity_pos'),
        sa.CheckConstraint('cost_cents IS NULL OR cost_cents >= 0', name='ck_order_cost_nonneg'),
        sa.CheckConstraint('tax_cents IS NULL OR tax_cents >= 0', name='ck_order_tax_nonneg'),
        sa.CheckConstraint('shipping_cents IS NULL OR shipping_cents >= 0', name='ck_order_shipping_nonneg'),
        sa.CheckConstraint('coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0', name='ck_order_coa_vial_size_pos'),
        sa.CheckConstraint('coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)',
                           name='ck_order_coa_purity_range'),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vendor_id'], ['vendors.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_orders_inventory_item_id', 'orders', ['inventory_item_id'])

    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'category', sa.Enum(*CATEGORY_VALUES, name='category', native_enum=False, length=20),
            nullable=False, server_default='Medicine'))
        batch_op.add_column(sa.Column('reconstituted_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('sold_count', sa.Integer(), nullable=False, server_default='0'))

    # Backfill: medium set -> Medicine (already the column default), no medium -> Supply (matches
    # today's real usage -- alcohol pads/syringes already have no medium). Nothing existing maps
    # to BAC Water automatically; recategorize by deleting and re-adding if needed.
    bind.execute(text("UPDATE inventory_items SET category = 'Supply' WHERE medium IS NULL"))

    # For every Medicine item with any order-shaped data or existing stock, synthesize one
    # already-arrived Order carrying the old per-item fields across, so existing stock isn't
    # stranded off of available_count after upgrading.
    rows = bind.execute(text("""
        SELECT id, count, vendor, vendor_id, lot_number, cost_cents, expiration_date,
               order_date, shipped_date, arrival_date, coa_filename, coa_vial_size_mg,
               coa_purity_pct, created_at
        FROM inventory_items WHERE category = 'Medicine'
    """)).fetchall()
    for r in rows:
        has_order_data = any([r.vendor, r.vendor_id, r.lot_number, r.cost_cents, r.expiration_date,
                              r.order_date, r.shipped_date, r.arrival_date, r.coa_filename,
                              r.coa_vial_size_mg, r.coa_purity_pct])
        if not has_order_data and not r.count:
            continue
        order_date = r.order_date or (r.created_at[:10] if r.created_at else None)
        arrival_date = r.arrival_date or order_date
        bind.execute(text("""
            INSERT INTO orders (inventory_item_id, quantity, order_date, shipped_date, arrival_date,
                                vendor, vendor_id, lot_number, cost_cents, expiration_date,
                                coa_filename, coa_vial_size_mg, coa_purity_pct, created_at)
            VALUES (:item_id, :qty, :order_date, :shipped_date, :arrival_date, :vendor, :vendor_id,
                    :lot_number, :cost_cents, :expiration_date, :coa_filename, :coa_vial_size_mg,
                    :coa_purity_pct, :created_at)
        """), {"item_id": r.id, "qty": max(r.count, 1), "order_date": order_date,
              "shipped_date": r.shipped_date, "arrival_date": arrival_date, "vendor": r.vendor,
              "vendor_id": r.vendor_id, "lot_number": r.lot_number, "cost_cents": r.cost_cents,
              "expiration_date": r.expiration_date, "coa_filename": r.coa_filename,
              "coa_vial_size_mg": r.coa_vial_size_mg, "coa_purity_pct": r.coa_purity_pct,
              "created_at": r.created_at})

    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.drop_column('lot_number')
        batch_op.drop_column('order_date')
        batch_op.drop_column('shipped_date')
        batch_op.drop_column('arrival_date')
        batch_op.drop_column('coa_filename')
        batch_op.drop_column('coa_vial_size_mg')
        batch_op.drop_column('coa_purity_pct')


def downgrade() -> None:
    with op.batch_alter_table('inventory_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('coa_purity_pct', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coa_vial_size_mg', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coa_filename', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('arrival_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('shipped_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('order_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('lot_number', sa.String(length=100), nullable=True))
        batch_op.drop_column('sold_count')
        batch_op.drop_column('reconstituted_count')
        batch_op.drop_column('category')
    op.drop_index('ix_orders_inventory_item_id', table_name='orders')
    op.drop_table('orders')
```

Note: `r.count` is never `NULL` (the column has a Python-level `default=1` and every existing row
was written by code that always sets it), so `max(r.count, 1)` is a safe floor, not a
null-coalesce.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -k available_count tests/test_migrations.py -k 0011 -v`
Expected: PASS, 3/3.

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (existing tests untouched by this task — `InventoryItem(**values)` calls
elsewhere still work since `category` has a Python-level default).

```bash
git add app/models.py migrations/versions/0011_order_history.py tests/test_inventory.py tests/test_migrations.py
git commit -m "feat: Order model, item categories, migration 0011"
```

---

## Task 2: Add Item backend — category-gated parsing, first Order on create

**Files:**
- Modify: `app/routers/inventory.py` (`FORM_FIELDS`, `_parse_form` split into item-fields +
  order-fields, `_form_values`, `create_item`, `update_item`)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `Category`, `Order` from Task 1.
- Produces: `_parse_item_fields(raw, session, uid, category) -> (values, errors)`,
  `_parse_order_form(raw, session, uid) -> (values, errors)` (both in `app/routers/inventory.py`,
  used again by Task 5's Add/Edit order routes). `POST /inventory` now creates an `Order` too for
  Medicine/BAC Water.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inventory.py
def test_add_medicine_item_creates_item_and_first_order(client, db):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_site": "https://track.example/x",
        "tracking_number": "LY123", "vendor": "PeptideCo", "cost": "84.00", "tax": "5.00", "shipping": "10.00",
        "lot_number": "LOT1", "expiration_date": "2028-01-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        assert item.category == Category.MEDICINE
        assert item.available_count == 0  # not arrived yet -- in transit
        order = item.orders[0]
        assert order.quantity == 10 and order.tracking_number == "LY123" and order.lot_number == "LOT1"
        assert order.cost_cents == 8400 and order.tax_cents == 500 and order.shipping_cents == 1000


def test_add_supply_item_has_no_order(client, db):
    r = client.post("/inventory", data={
        "name": "Alcohol Pads", "category": "Supply", "count": "250", "cost": "4.23", "vendor": "Acme Pharmacy",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Alcohol Pads"))
        assert item.category == Category.SUPPLY
        assert item.available_count == 250
        assert item.orders == []


def test_add_bac_water_item_has_no_medium_fields(client, db):
    client.post("/inventory", data={
        "name": "Bacteriostatic Water", "category": "BAC Water", "quantity": "4", "order_date": "2026-09-01",
    })
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Bacteriostatic Water"))
        assert item.category == Category.BAC_WATER and item.medium is None


def test_add_medicine_item_requires_quantity(client, db):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "order_date": "2026-08-01",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(InventoryItem).filter_by(name="Retatrutide").count() == 0


def test_editing_item_does_not_create_or_touch_orders(client, db, me):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    client.post(f"/inventory/{item_id}", data={"name": "Retatrutide XR", "storage": "fridge"})
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.name == "Retatrutide XR" and item.storage.value == "fridge"
        assert len(item.orders) == 1  # unchanged
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -k "add_medicine or add_supply or add_bac_water or editing_item_does_not" -v`
Expected: FAIL — current `create_item`/`_parse_form` know nothing about `category` or order
fields; `test_add_medicine_item_creates_item_and_first_order` fails on `item.orders[0]`
(`IndexError`, empty list) or on the item being created with today's flat fields ignored.

- [ ] **Step 3: Implement**

Replace `FORM_FIELDS` (lines 22-27) with:

```python
ITEM_FIELDS = ("name", "category", "count", "vial_size_mg", "vial_size_unit", "medium",
              "volume_ml", "units_per_package", "storage", "cost", "vendor", "notes")
ORDER_FIELDS = ("quantity", "order_date", "tracking_site", "tracking_number", "vendor", "cost",
                "tax", "shipping", "lot_number", "expiration_date", "coa_vial_size_mg", "coa_purity_pct")
FORM_FIELDS = tuple(dict.fromkeys(ITEM_FIELDS + ORDER_FIELDS))  # union, order preserved, no dupes
```

Add `Category`, `Order` to the `app.models` import (line 16).

Replace `_parse_form` (lines 65-145) with three functions:

```python
def _parse_money(raw: str, field: str, label: str, errors: dict) -> int | None:
    raw = raw.lstrip("$").replace(",", "")
    if not raw:
        return None
    try:
        cents = (Decimal(raw) * 100).quantize(Decimal("1"))
    except InvalidOperation:
        errors[field] = f"{label} must be a number, e.g. 45.99."
        return None
    if cents < 0:
        errors[field] = f"{label} can't be negative."
    return int(cents)


def _parse_item_fields(raw: dict[str, str], session: Session, uid: int, category: "Category") -> tuple[dict, dict]:
    """Parses the Details fields for an item of the given (already-resolved) category. Used by
    both create (with a freshly-parsed category) and update (with the item's existing, immutable
    category)."""
    errors: dict[str, str] = {}
    values: dict = {"category": category}

    values["name"] = raw["name"]
    if not values["name"]:
        errors["name"] = "Item name is required."
    values["storage"] = _parse_choice(StorageLocation, raw["storage"], None, "storage", errors)
    values["notes"] = raw["notes"] or None

    if category == Category.SUPPLY:
        try:
            values["count"] = int(raw["count"]) if raw["count"] else 1
            if values["count"] < 0:
                errors["count"] = "Count can't be negative."
        except ValueError:
            values["count"] = 1
            errors["count"] = "Count must be a whole number."
        values["cost_cents"] = _parse_money(raw["cost"], "cost", "Cost", errors)
        vendor = resolve_vendor(session, uid, raw["vendor"])
        values["vendor_id"] = vendor.id if vendor else None
        values["vendor"] = vendor.name if vendor else None
        values["medium"] = None
        values["vial_size_mg"] = None
        values["vial_size_unit"] = DoseUnit.MG
        values["volume_ml"] = None
        values["units_per_package"] = None
        return values, errors

    # Medicine / BAC Water: count/cost/vendor are vestigial here (available_count is derived from
    # Orders); the item-level columns are simply not written to by these categories.
    values["count"] = 0
    values["cost_cents"] = None
    values["vendor_id"] = None
    values["vendor"] = None

    if category == Category.MEDICINE:
        values["medium"] = _parse_choice(Medium, raw["medium"], None, "medium", errors)
        values["vial_size_mg"] = _parse_positive_float(raw["vial_size_mg"], "vial_size_mg", "Amount", errors)
        values["vial_size_unit"] = _parse_choice(DoseUnit, raw["vial_size_unit"], DoseUnit.MG, "vial_size_unit", errors)
        values["volume_ml"] = _parse_positive_float(raw["volume_ml"], "volume_ml", "Volume", errors)
        values["units_per_package"] = None
        if raw["units_per_package"]:
            try:
                values["units_per_package"] = int(raw["units_per_package"])
                if values["units_per_package"] <= 0:
                    errors["units_per_package"] = "Units per package must be greater than 0."
            except ValueError:
                errors["units_per_package"] = "Units per package must be a whole number."
        for field in required_fields_for(values["medium"]):
            if values.get(field) is None and field not in errors:
                errors[field] = f"{field_label(field, values['medium'])} is required for {values['medium'].value}."
    else:  # BAC_WATER
        values["medium"] = None
        values["vial_size_mg"] = None
        values["vial_size_unit"] = DoseUnit.MG
        values["volume_ml"] = None
        values["units_per_package"] = None

    return values, errors


def _parse_order_form(raw: dict[str, str], session: Session, uid: int) -> tuple[dict, dict]:
    """Parses the Order fields (quantity, dates, tracking, vendor/cost/tax/shipping/lot/
    expiration/COA numbers). Used by create (the item's first order) and by Task 5's Add/Edit
    order routes."""
    errors: dict[str, str] = {}
    values: dict = {}

    values["quantity"] = None
    try:
        if raw["quantity"]:
            values["quantity"] = int(raw["quantity"])
        if not values["quantity"] or values["quantity"] <= 0:
            errors["quantity"] = "Quantity must be a whole number greater than 0."
    except ValueError:
        errors["quantity"] = "Quantity must be a whole number."

    values["order_date"] = _parse_date(raw["order_date"], "order_date", errors)
    if values["order_date"] is None and "order_date" not in errors:
        errors["order_date"] = "Order date is required."
    values["tracking_site"] = raw["tracking_site"] or None
    values["tracking_number"] = raw["tracking_number"] or None

    vendor = resolve_vendor(session, uid, raw["vendor"])
    values["vendor_id"] = vendor.id if vendor else None
    values["vendor"] = vendor.name if vendor else None
    values["lot_number"] = raw["lot_number"] or None

    values["cost_cents"] = _parse_money(raw["cost"], "cost", "Cost", errors)
    values["tax_cents"] = _parse_money(raw["tax"], "tax", "Tax", errors)
    values["shipping_cents"] = _parse_money(raw["shipping"], "shipping", "Shipping", errors)
    values["expiration_date"] = _parse_date(raw["expiration_date"], "expiration_date", errors)

    values["coa_vial_size_mg"] = _parse_positive_float(
        raw["coa_vial_size_mg"], "coa_vial_size_mg", "Lab vial size", errors)
    values["coa_purity_pct"] = None
    purity = raw["coa_purity_pct"].rstrip("%").strip()
    if purity:
        try:
            values["coa_purity_pct"] = float(purity)
            if not 0 <= values["coa_purity_pct"] <= 100:
                errors["coa_purity_pct"] = "Purity must be between 0 and 100%."
        except ValueError:
            errors["coa_purity_pct"] = "Purity must be a number, e.g. 99.2."

    return values, errors
```

Update `_form_values` (lines 148-174) to drop the now-item-removed fields and add `category`:

```python
def _form_values(item: InventoryItem) -> dict:
    """An item's Details values as the edit form expects them (all strings)."""
    def num(v):
        return "" if v is None else f"{v:g}"

    return {
        "id": item.id,
        "name": item.name,
        "category": item.category.value,
        "count": str(item.count),
        "vial_size_mg": num(item.vial_size_mg),
        "vial_size_unit": item.vial_size_unit.value,
        "medium": item.medium.value if item.medium else "",
        "volume_ml": num(item.volume_ml),
        "units_per_package": "" if item.units_per_package is None else str(item.units_per_package),
        "storage": item.storage.value if item.storage else "",
        "cost": "" if item.cost is None else f"{item.cost:.2f}",
        "vendor": item.vendor or "",
        "notes": item.notes or "",
    }
```

Update `_read_form`'s `FORM_FIELDS` usage (line 180) — no change needed, it already iterates
whatever `FORM_FIELDS` is.

Replace `create_item` (lines 314-332):

```python
@router.post("/inventory")
async def create_item(request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    raw, coa, _ = await _read_form(request)
    errors: dict[str, str] = {}
    category = _parse_choice(Category, raw["category"], Category.MEDICINE, "category", errors)
    values, item_errors = _parse_item_fields(raw, session, uid, category)
    errors.update(item_errors)

    order_values = {}
    if category != Category.SUPPLY:
        order_values, order_errors = _parse_order_form(raw, session, uid)
        errors.update(order_errors)

    coa_filename = None
    if not errors and coa and category != Category.SUPPLY:
        try:
            coa_filename = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        return _render_list(request, session, form=raw, errors=errors, status_code=422)

    item = InventoryItem(**values, owner_id=uid)
    if category != Category.SUPPLY:
        item.orders.append(Order(**order_values, coa_filename=coa_filename))
    session.add(item)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)
```

Replace `update_item` (lines 335-361) — Details-only, category comes from the existing item, never
from the submitted form:

```python
@router.post("/inventory/{item_id}")
async def update_item(item_id: int, request: Request, session: Session = Depends(get_session),
                      uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")

    raw, coa, remove_coa = await _read_form(request)
    values, errors = _parse_item_fields(raw, session, uid, item.category)  # category is immutable

    if errors:
        return _render_list(request, session, form=raw, errors=errors, editing=item, status_code=422)

    for key, value in values.items():
        if key == "category":
            continue  # never reassigned after creation
        setattr(item, key, value)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)
```

Note `coa`/`remove_coa` are now unused in `update_item` (COA moves to per-order, Task 5 adds its
own edit-order route for that) — `_read_form` still returns them since the same helper is reused,
but this route intentionally ignores them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -v`
Expected: the 5 new tests PASS. Existing tests in this file that post the old flat
vendor/cost/lot/order_date/arrival_date fields to `/inventory` will now FAIL — fix them in this
same step by rewriting their `client.post("/inventory", data={...})` calls to the new
`category`/`quantity`/`order_date` shape (each such test already exists in this file; adjust its
payload and any assertion that read `item.vendor`/`item.lot_number`/etc. directly on the item to
instead read `item.orders[0].vendor`/`.lot_number`/etc., or drop the assertion if Task 2 doesn't
cover that specific field's round-trip — Task 5's tests cover Order editing in depth).

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass. `test_inventory_rules.py` and `test_backup.py` will likely fail here (backup
still uses the old flat fields) — Task 7 fixes backup; if `test_inventory_rules.py` fails, it's
almost certainly `required_fields_for`/`field_label` calls that only ever depended on `Medium`,
unaffected by this task — investigate any failure there with
`superpowers:systematic-debugging` before assuming it's expected.

```bash
git add app/routers/inventory.py tests/test_inventory.py
git commit -m "feat: category-gated Add Item parsing, first Order on create"
```

---

## Task 3: Add Item frontend — category radio, conditional sections

**Files:**
- Modify: `app/templates/inventory/list.html` (the `#item-dialog` form, lines 138-305)
- Modify: `app/static/js/inventory.js` (the `openFor`/`syncMediumFields` logic, lines 1-62)
- Test: `tests/test_inventory.py` (template-rendering assertions only — JS behavior itself has no
  test harness in this suite, matching this repo's established pattern for JS-only changes)

**Interfaces:**
- Consumes: `category` field name and `Category` values from Task 2.
- Produces: no new backend interface — purely template/JS.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inventory.py
def test_add_item_dialog_has_category_radios(client, db):
    t = text(client.get("/inventory"))
    assert 'name="category" value="Medicine"' in t
    assert 'name="category" value="BAC Water"' in t
    assert 'name="category" value="Supply"' in t


def test_add_item_dialog_has_order_and_supply_field_groups(client, db):
    t = text(client.get("/inventory"))
    assert 'data-category-group="order"' in t
    assert 'data-category-group="supply"' in t
    assert 'name="quantity"' in t and 'name="tracking_site"' in t and 'name="tracking_number"' in t
    assert 'name="tax"' in t and 'name="shipping"' in t
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -k "category_radios or field_groups" -v`
Expected: FAIL — the dialog has no category radios or these field groups yet.

- [ ] **Step 3: Implement**

Replace the `<fieldset class="form-section"><legend>Item</legend>...` block (lines 156-226) with
a category radio group followed by three conditionally-shown groups. Insert right after the
`<h2 data-title>` header (after line 147, before the errors alert at line 149):

```html
    <fieldset class="form-section" data-category-group="category">
      <legend>Category</legend>
      <div class="chips" role="radiogroup" aria-label="Category">
        <label class="chip-radio"><input type="radio" name="category" value="Medicine" {{ 'checked' if f.category in (None, 'Medicine') }}> Medicine</label>
        <label class="chip-radio"><input type="radio" name="category" value="BAC Water" {{ 'checked' if f.category == 'BAC Water' }}> BAC Water</label>
        <label class="chip-radio"><input type="radio" name="category" value="Supply" {{ 'checked' if f.category == 'Supply' }}> Supply</label>
      </div>
    </fieldset>
```

Then replace the existing `<fieldset class="form-section"><legend>Item</legend>` block (lines
156-226) with a Medicine/BAC-Water-only fields group (drop `count`, `lot_number`,
`expiration_date` — those move to Supply's group or Order's group):

```html
    <fieldset class="form-section" data-category-group="medicine">
      <legend>Item</legend>
      <div class="grid">
        <label {{ cls('name', 'span-2') }}>
          <span>Item name <em>*</em></span>
          <input name="name" required maxlength="200" autocomplete="off" value="{{ f.name }}" placeholder="e.g. BPC-157">
          {{ err('name') }}
        </label>

        <label {{ cls('medium') }} data-field-group="medium-only">
          <span>Medium</span>
          <select name="medium" id="inv-medium">
            <option value="">— Select —</option>
            {% for m in mediums %}
            <option value="{{ m.value }}" {{ 'selected' if f.medium == m.value }}>{{ m.value }}</option>
            {% endfor %}
          </select>
          {{ err('medium') }}
        </label>

        <div {{ cls('vial_size_mg') }} data-field="vial_size_mg" data-field-group="medium-only">
          <span data-label-for="vial_size_mg">Amount</span>
          <div class="inline-inputs">
            <input name="vial_size_mg" type="number" min="0" step="any" inputmode="decimal" value="{{ f.vial_size_mg }}" placeholder="e.g. 10">
            <select name="vial_size_unit">
              {% for u in dose_units %}<option value="{{ u.value }}" {{ 'selected' if f.vial_size_unit == u.value }}>{{ u.label }}</option>{% endfor %}
            </select>
          </div>
          {{ err('vial_size_mg') }}
        </div>

        <label {{ cls('volume_ml') }} data-field="volume_ml" data-field-group="medium-only" hidden>
          <span>Volume (mL)</span>
          <input name="volume_ml" type="number" min="0" step="any" inputmode="decimal" value="{{ f.volume_ml }}" placeholder="e.g. 2">
          {{ err('volume_ml') }}
        </label>

        <label {{ cls('units_per_package') }} data-field="units_per_package" data-field-group="medium-only" hidden>
          <span data-label-for="units_per_package">Units per package</span>
          <input name="units_per_package" type="number" min="1" step="1" inputmode="numeric" value="{{ f.units_per_package }}">
          {{ err('units_per_package') }}
        </label>

        <label {{ cls('storage') }}>
          <span>Storage</span>
          <select name="storage">
            <option value="">— Select —</option>
            {% for s in storage_locations %}<option value="{{ s.value }}" {{ 'selected' if f.storage == s.value }}>{{ s.label }}</option>{% endfor %}
          </select>
          {{ err('storage') }}
        </label>
      </div>
    </fieldset>

    <fieldset class="form-section" data-category-group="supply">
      <legend>Item</legend>
      <div class="grid">
        <label {{ cls('name', 'span-2') }}>
          <span>Item name <em>*</em></span>
          <input name="supply_name" maxlength="200" autocomplete="off" value="{{ f.name }}" placeholder="e.g. Alcohol prep pads" data-mirror="name">
        </label>
        <label {{ cls('count') }}>
          <span>Count</span>
          <input name="count" type="number" min="0" step="1" inputmode="numeric" value="{{ f.count or 1 }}">
          {{ err('count') }}
        </label>
        <label {{ cls('cost') }}>
          <span>Cost ($)</span>
          <input name="supply_cost" type="number" min="0" step="0.01" inputmode="decimal" value="{{ f.cost }}" placeholder="0.00" data-mirror="cost">
          {{ err('cost') }}
        </label>
        <label {{ cls('vendor') }}>
          <span>Vendor</span>
          <input name="supply_vendor" maxlength="200" value="{{ f.vendor }}" placeholder="Where you bought it" data-mirror="vendor">
        </label>
        <label {{ cls('storage') }}>
          <span>Storage</span>
          <select name="supply_storage" data-mirror="storage">
            <option value="">— Select —</option>
            {% for s in storage_locations %}<option value="{{ s.value }}" {{ 'selected' if f.storage == s.value }}>{{ s.label }}</option>{% endfor %}
          </select>
        </label>
      </div>
    </fieldset>
```

The `data-mirror` inputs above are a deliberate simplification: rather than duplicating every
`name`/`cost`/`vendor`/`storage` input under a second `name="..."` attribute (which the server
would then have to disambiguate), the Supply group's inputs are named `supply_*` and a small JS
change (below) copies their value into the real `name`/`cost`/`vendor`/`storage` field on submit,
so `_read_form`'s single `raw["name"]` etc. keeps working unchanged for both groups. Note the
"real" `cost`/`vendor` fields the Supply mirror writes into are the ones declared in the **Order**
fieldset below (`name="cost"`, `name="vendor"`), not new ones on the Medicine fieldset — the
Medicine fieldset deliberately has no item-level cost/vendor inputs any more (Task 2 moved those
to Order for Medicine/BAC Water). A hidden `<fieldset hidden>`'s inputs are still submitted by the
browser (the `hidden` attribute doesn't disable a control, only visually hides it), so this is
safe: whichever single group is visible is the one whose values end up in `raw["cost"]`/
`raw["vendor"]` by the time `_parse_item_fields`/`_parse_order_form` read them server-side.

Keep the existing `Order` fieldset (lines 228-261) but wrap it with a group marker and add
Quantity/Tracking, dropping the now-item-level-removed framing — replace that whole fieldset with:

```html
    <fieldset class="form-section" data-category-group="order">
      <legend>First order</legend>
      <div class="grid">
        <label {{ cls('quantity') }}>
          <span>Quantity</span>
          <input name="quantity" type="number" min="1" step="1" inputmode="numeric" value="1">
          {{ err('quantity') }}
        </label>

        <label {{ cls('order_date') }}>
          <span>Order date</span>
          <input name="order_date" type="date" value="{{ today_iso }}">
          {{ err('order_date') }}
        </label>

        <label {{ cls('tracking_site') }}>
          <span>Tracking site (URL)</span>
          <input name="tracking_site" type="url" maxlength="500" placeholder="https://...">
          {{ err('tracking_site') }}
        </label>

        <label {{ cls('tracking_number') }}>
          <span>Tracking number</span>
          <input name="tracking_number" maxlength="100">
          {{ err('tracking_number') }}
        </label>

        <label {{ cls('vendor') }}>
          <span>Vendor</span>
          <input name="vendor" maxlength="200" placeholder="Where you bought it">
          {{ err('vendor') }}
        </label>

        <label {{ cls('lot_number') }}>
          <span>Lot / Batch #</span>
          <input name="lot_number" maxlength="100" autocomplete="off">
          {{ err('lot_number') }}
        </label>

        <label {{ cls('cost') }}>
          <span>Cost ($)</span>
          <input name="cost" type="number" min="0" step="0.01" inputmode="decimal" placeholder="0.00">
          {{ err('cost') }}
        </label>
        <label {{ cls('tax') }}>
          <span>Tax ($)</span>
          <input name="tax" type="number" min="0" step="0.01" inputmode="decimal" placeholder="0.00">
          {{ err('tax') }}
        </label>
        <label {{ cls('shipping') }}>
          <span>Shipping ($)</span>
          <input name="shipping" type="number" min="0" step="0.01" inputmode="decimal" placeholder="0.00">
          {{ err('shipping') }}
        </label>

        <label {{ cls('expiration_date') }}>
          <span>Expiration date</span>
          <input name="expiration_date" type="date">
          {{ err('expiration_date') }}
        </label>
      </div>
    </fieldset>

    <fieldset class="form-section" data-category-group="order">
      <legend>COA</legend>
      <div class="grid">
        <div {{ cls('coa', 'span-2') }}>
          <label for="coa-input"><span>COA (photo or PDF)</span></label>
          <input id="coa-input" name="coa" type="file" accept="image/*,.heic,.heif,application/pdf">
          {{ err('coa') }}
          <img class="coa-preview" data-coa-preview alt="COA preview" hidden>
        </div>
        <label {{ cls('coa_vial_size_mg') }}>
          <span>Lab vial size (mg)</span>
          <input name="coa_vial_size_mg" type="number" min="0" step="any" inputmode="decimal" placeholder="As measured on COA">
          {{ err('coa_vial_size_mg') }}
        </label>
        <label {{ cls('coa_purity_pct') }}>
          <span>Lab purity (%)</span>
          <input name="coa_purity_pct" type="number" min="0" max="100" step="any" inputmode="decimal" placeholder="e.g. 99.2">
          {{ err('coa_purity_pct') }}
        </label>
      </div>
    </fieldset>
```

Remove the old bare `<fieldset class="form-section"><legend>Order</legend>` COA-adjacent
duplicate — the file previously had one "Order" fieldset (lines 228-261) and one "COA" fieldset
(lines 263-289); both are now replaced by the two `data-category-group="order"` fieldsets above,
so delete the old ones in full (nothing named `order_date`/`shipped_date`/`arrival_date` remains
on this form — shipped/arrival dates only ever live on the detail page's Order History, per the
spec).

Pass a new `today_iso` (a `date.today().isoformat()` string, for the Add Item form's date input
default) into the template context from `_render_list` in `app/routers/inventory.py` — add
`"today_iso": date.today().isoformat(),` to the dict at line ~299. **Do not rename or repurpose
the existing `"today": date.today()` entry** in that same dict — it's a raw `date` object the
Active Vials section already relies on for `{% set expired = v.discard_by < today %}` (a
date-to-date comparison); overwriting it with a string would silently break that comparison
(comparing a `date` to a `str` raises `TypeError` in Python 3). Both keys coexist.

Add JS to `app/static/js/inventory.js` — replace `syncMediumFields` (lines 23-36) and the field
list (lines 9-14) and `openFor` (lines 49-62) with:

```javascript
  const fields = [
    "name", "category", "count", "vial_size_mg", "vial_size_unit", "medium", "volume_ml",
    "units_per_package", "storage", "cost", "vendor", "notes",
  ];
  const rules = JSON.parse(document.getElementById("inv-rules").textContent);
  const mediumSelect = form.elements.medium;
  const categoryRadios = form.querySelectorAll('input[name="category"]');
  const fieldWrappers = {
    vial_size_mg: form.querySelector('[data-field="vial_size_mg"]'),
    volume_ml: form.querySelector('[data-field="volume_ml"]'),
    units_per_package: form.querySelector('[data-field="units_per_package"]'),
  };

  function syncMediumFields() {
    const medium = mediumSelect.value;
    const rule = rules[medium] || { required: [], labels: {} };
    fieldWrappers.volume_ml.hidden = !rule.required.includes("volume_ml");
    fieldWrappers.units_per_package.hidden = !rule.required.includes("units_per_package");
    for (const [field, wrapper] of Object.entries(fieldWrappers)) {
      const input = wrapper.querySelector("input");
      input.required = rule.required.includes(field);
      const labelEl = wrapper.querySelector(`[data-label-for="${field}"]`);
      if (labelEl && rule.labels[field]) labelEl.textContent = rule.labels[field];
    }
  }
  mediumSelect.addEventListener("change", syncMediumFields);

  function syncCategoryFields() {
    const category = form.querySelector('input[name="category"]:checked')?.value || "Medicine";
    const isEdit = dialog.dataset.mode === "edit";
    dialog.querySelector('[data-category-group="medicine"]').hidden = category === "Supply";
    dialog.querySelector('[data-category-group="supply"]').hidden = category !== "Supply";
    dialog.querySelectorAll('[data-category-group="order"]').forEach((el) => {
      el.hidden = category === "Supply" || isEdit;
    });
    dialog.querySelector('[data-category-group="category"]').hidden = isEdit;  // immutable once created
    if (category === "Medicine") syncMediumFields();
  }
  categoryRadios.forEach((r) => r.addEventListener("change", syncCategoryFields));
```

Replace `openFor` (lines 49-62):

```javascript
  function openFor(item) {
    clearErrors();
    form.reset();
    resetPreview();
    form.action = item ? `/inventory/${item.id}` : "/inventory";
    title.textContent = item ? "Edit item" : "New inventory item";
    dialog.dataset.mode = item ? "edit" : "add";
    for (const f of fields) {
      if (f === "category") continue;  // radios, set below
      form.elements[f].value = item ? item[f] ?? "" : f === "count" ? "1" : f === "vial_size_unit" ? "mg" : "";
    }
    const category = item ? item.category : "Medicine";
    form.querySelectorAll('input[name="category"]').forEach((r) => { r.checked = r.value === category; });
    coaExisting.hidden = true;  // COA lives per-order now (Task 5), never on this dialog
    syncCategoryFields();
    dialog.showModal();
    form.elements.name.focus();
  }
```

Finally, add the Supply-field mirroring before submit — insert right before the existing
`document.addEventListener("click", ...)` block (before line 64):

```javascript
  form.addEventListener("submit", () => {
    form.querySelectorAll("[data-mirror]").forEach((el) => {
      form.elements[el.dataset.mirror].value = el.value;
    });
  });
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -v`
Expected: all pass.

- [ ] **Step 5: Manual browser check**

Start the dev server (`preview_start`), open `/inventory`, click **+ Add item**, and confirm: the
Category radios switch which fieldsets show (Medicine shows medium/amount + First order + COA;
BAC Water shows just name/storage + First order + COA; Supply shows name/count/cost/vendor/storage
only, no First order/COA); submitting a Supply item and a Medicine item both work end to end. This
step has no `Expected:` line to compare against a command's output — it's a visual check, so
report what you saw rather than a pass/fail.

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (aside from `test_backup.py`, still pending Task 7 — confirm no *new*
failures beyond what Task 2 already left).

```bash
git add app/templates/inventory/list.html app/static/js/inventory.js app/routers/inventory.py tests/test_inventory.py
git commit -m "feat: category-driven Add Item form (Medicine/BAC Water/Supply)"
```

---

## Task 4: Item detail page — Details & Inventory sections

**Files:**
- Create: `app/templates/inventory/detail.html`
- Modify: `app/routers/inventory.py` (new `GET /inventory/{id}` route; `_render_list`'s row link)
- Modify: `app/templates/inventory/list.html` (row becomes a link to the detail page)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `available_count`, `Category` from Task 1; `_visible_item` (existing helper).
- Produces: `GET /inventory/{id}` route, rendering `inventory/detail.html`. Task 5 adds the Order
  History section to this same template and route.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inventory.py
def test_item_detail_page_shows_details_and_available_count(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = text(client.get(f"/inventory/{item_id}"))
    assert "Retatrutide" in t
    assert "10 mg" in t
    assert "0" in t.split('id="inv-available-count"')[1][:20]  # not arrived yet


def test_item_detail_page_404s_for_someone_elses_private_item(client, db):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "DetailOther", "password": "DetailOther1!", "confirm": "DetailOther1!"})
    other.post("/inventory", data={"name": "Private Item", "category": "Supply", "count": "1"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Private Item"))
    assert client.get(f"/inventory/{item_id}").status_code == 404


def test_inventory_list_row_links_to_detail_page(client, db):
    client.post("/inventory", data={"name": "Alcohol Pads", "category": "Supply", "count": "5"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Alcohol Pads"))
    t = text(client.get("/inventory"))
    assert f'href="/inventory/{item_id}"' in t
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -k "detail_page or row_links" -v`
Expected: FAIL — `GET /inventory/{id}` doesn't exist (404 either way, but the passing test expects
200 with specific content; the "someone else's item" 404 test may pass by accident since the route
doesn't exist at all yet — check its failure message is about the *other* two tests, not a false
green here).

- [ ] **Step 3: Implement**

Add to `app/routers/inventory.py`, after `list_inventory` (after line 311):

```python
@router.get("/inventory/{item_id}")
def item_detail(item_id: int, request: Request, session: Session = Depends(get_session),
                uid: int = Depends(current_user_id)):
    item = _visible_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    arrived = sum(o.quantity for o in item.orders if o.arrival_date is not None)
    return templates.TemplateResponse(request, "inventory/detail.html", {
        "item": item,
        "is_owner": item.owner_id == uid,
        "arrived": arrived,
        "storage_locations": list(StorageLocation),
        "mediums": list(Medium),
        "dose_units": list(DoseUnit),
        "medium_rules": {
            m.value: {
                "required": sorted(required_fields_for(m)),
                "labels": {f: field_label(f, m) for f in ("vial_size_mg", "units_per_package")},
            }
            for m in Medium
        },
        "edit_data": _form_values(item),
    })
```

Create `app/templates/inventory/detail.html`:

```html
{% extends "base.html" %}
{% set active_nav = "inventory" %}
{% block title %}{{ item.name }}{% endblock %}

{% block content %}
<div class="page-head">
  <div>
    <p class="muted small"><a href="/inventory">&larr; Inventory</a></p>
    <h1>{{ item.name }}</h1>
    <p class="muted"><span class="tag">{{ item.category.value }}</span></p>
  </div>
</div>

<section aria-labelledby="details-heading">
  <h2 id="details-heading" class="section-title">Details</h2>
  <dl class="kv">
    {% if item.category.value == 'Medicine' %}
    <dt>Medium</dt><dd>{{ item.medium.value if item.medium else '—' }}</dd>
    <dt>Amount</dt><dd>{% if item.vial_size_mg %}{{ '%g' % item.vial_size_mg }} {{ item.vial_size_unit.value }}{% else %}—{% endif %}</dd>
    {% endif %}
    <dt>Storage</dt><dd>{{ item.storage.label if item.storage else '—' }}</dd>
    {% if item.category.value == 'Supply' %}
    <dt>Cost</dt><dd>{{ item.cost | money or '—' }}</dd>
    <dt>Vendor</dt><dd>{{ item.vendor or '—' }}</dd>
    {% endif %}
    <dt>Notes</dt><dd>{{ item.notes or '—' }}</dd>
  </dl>
  {% if is_owner %}
  <button type="button" class="btn btn-ghost" data-action="edit-item">Edit</button>
  {% endif %}
</section>

<section aria-labelledby="inventory-heading">
  <h2 id="inventory-heading" class="section-title">Inventory</h2>
  {% if item.category.value == 'Supply' %}
  <p>Count: <strong id="inv-available-count">{{ item.available_count }}</strong></p>
  {% else %}
  <dl class="kv">
    <dt>Arrived</dt><dd>{{ arrived }}</dd>
    <dt>Reconstituted</dt><dd>{{ item.reconstituted_count }}</dd>
    <dt>Sold</dt><dd>{{ item.sold_count }}</dd>
    <dt>Available</dt><dd id="inv-available-count"><strong>{{ item.available_count }}</strong></dd>
  </dl>
  {% endif %}
</section>
{% endblock %}
```

Update `app/templates/inventory/list.html`'s row (line 47-53): wrap the item name in a link and
drop the row-level Edit button's job of opening the dialog for viewing (Edit still opens the
dialog, per Task 3, but the row itself now navigates):

```html
        <td data-label="Item" class="item-name">
          <a href="/inventory/{{ item.id }}">{{ item.name }}</a>
          {% if item.owner_id != viewer_id %}<div class="muted small">Shared by {{ owner_names[item.owner_id] }}</div>{% endif %}
        </td>
```

(The `lot_number`/`expiration_date`/`notes` sub-lines that were here are removed in Task 6's list
resectioning, which rewrites this whole row — this task only needs the `<a>` wrapper to exist so
`test_inventory_list_row_links_to_detail_page` passes; leave the rest of the row as-is for now.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (aside from the already-known `test_backup.py` gap, pending Task 7).

```bash
git add app/routers/inventory.py app/templates/inventory/detail.html app/templates/inventory/list.html tests/test_inventory.py
git commit -m "feat: item detail page (Details + Inventory sections)"
```

---

## Task 5: Order History section — list, add order, edit order, per-order COA

**Files:**
- Modify: `app/templates/inventory/detail.html` (add Order History section)
- Modify: `app/routers/inventory.py` (add-order route, edit-order route, order-scoped COA route,
  remove the old item-scoped COA route)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `_parse_order_form` from Task 2; `available_count` from Task 1.
- Produces: `POST /inventory/{item_id}/orders` (add order), `POST
  /inventory/{item_id}/orders/{order_id}` (edit order — the route that fills in
  shipped_date/arrival_date), `GET /inventory/{item_id}/orders/{order_id}/coa` (per-order COA,
  replaces `GET /inventory/{item_id}/coa`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inventory.py
def test_add_order_creates_a_second_order_and_updates_available_count(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "arrival_date": "",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        s.get(InventoryItem, item_id).orders[0].arrival_date = date(2026, 8, 10)  # simulate first order having arrived
        s.commit()

    r = client.post(f"/inventory/{item_id}/orders", data={
        "quantity": "5", "order_date": "2026-09-20", "tracking_number": "LY456",
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert len(item.orders) == 2
        assert item.available_count == 10  # the new order hasn't arrived yet


def test_edit_order_filling_in_arrival_date_updates_available_count(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        order_id = s.get(InventoryItem, item_id).orders[0].id
        assert s.get(InventoryItem, item_id).available_count == 0  # not arrived yet

    r = client.post(f"/inventory/{item_id}/orders/{order_id}", data={
        "quantity": "10", "order_date": "2026-08-01", "shipped_date": "2026-08-03",
        "arrival_date": "2026-08-10",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).available_count == 10


def test_add_order_requires_ownership(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "OrderOther", "password": "OrderOther1!", "confirm": "OrderOther1!"})
    r = other.post(f"/inventory/{item_id}/orders", data={"quantity": "5", "order_date": "2026-09-20"})
    assert r.status_code == 404


def test_shared_item_order_history_is_visible_but_not_editable(client, db, me):
    client.post("/inventory", data={
        "name": "Shared Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_number": "LY123",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Shared Retatrutide"))

    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "SharedGrantee", "password": "SharedGrantee1!", "confirm": "SharedGrantee1!"})
    with SessionLocal() as s:
        grantee_id = s.scalar(select(User.id).where(User.username_key == "sharedgrantee"))
    try:
        client.post(f"/settings/sharing/{grantee_id}/inventory")  # matches this app's existing Share-grant route

        t = text(other.get(f"/inventory/{item_id}"))
        assert "LY123" in t  # order history visible to the grantee
        assert 'data-action="add-order"' not in t  # but not editable

        assert other.post(f"/inventory/{item_id}/orders", data={"quantity": "1", "order_date": "2026-09-01"}).status_code == 404
        with SessionLocal() as s:
            order_id = s.get(InventoryItem, item_id).orders[0].id
        assert other.post(f"/inventory/{item_id}/orders/{order_id}", data={"quantity": "1", "order_date": "2026-09-01"}).status_code == 404
    finally:
        client.post(f"/settings/sharing/{grantee_id}/inventory")  # toggle back off -- keep suite state clean


def test_detail_page_lists_order_history(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_site": "https://track.example/x",
        "tracking_number": "LY123",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = text(client.get(f"/inventory/{item_id}"))
    assert "LY123" in t
    assert 'href="https://track.example/x"' in t
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -k "add_order or edit_order or order_history" -v`
Expected: FAIL — none of these routes exist (404 on POST to `/inventory/{id}/orders`, and the
detail page has no Order History section to assert against).

- [ ] **Step 3: Implement**

Add to `app/routers/inventory.py`, after `item_detail` (Task 4's new route):

```python
def _own_order(session: Session, item_id: int, order_id: int, uid: int) -> Order | None:
    item = _own_item(session, item_id, uid)
    if item is None:
        return None
    order = session.get(Order, order_id)
    return order if order is not None and order.inventory_item_id == item.id else None


@router.post("/inventory/{item_id}/orders")
async def add_order(item_id: int, request: Request, session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None or item.category == Category.SUPPLY:
        raise HTTPException(404, "Inventory item not found")

    raw, coa, _ = await _read_form(request)
    values, errors = _parse_order_form(raw, session, uid)

    coa_filename = None
    if not errors and coa:
        try:
            coa_filename = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "order_errors": errors, "order_form": raw,
            "arrived": sum(o.quantity for o in item.orders if o.arrival_date is not None),
            "storage_locations": list(StorageLocation), "mediums": list(Medium),
            "dose_units": list(DoseUnit), "edit_data": _form_values(item)},
            status_code=422)

    item.orders.append(Order(**values, coa_filename=coa_filename))
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@router.post("/inventory/{item_id}/orders/{order_id}")
async def update_order(item_id: int, order_id: int, request: Request,
                       session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    order = _own_order(session, item_id, order_id, uid)
    if order is None:
        raise HTTPException(404, "Order not found")

    raw, coa, remove_coa = await _read_form(request)
    values, errors = _parse_order_form(raw, session, uid)

    new_coa = None
    if not errors and coa:
        try:
            new_coa = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        item = order.inventory_item
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "order_errors": errors, "order_form": raw,
            "editing_order": order,
            "arrived": sum(o.quantity for o in item.orders if o.arrival_date is not None),
            "storage_locations": list(StorageLocation),
            "mediums": list(Medium), "dose_units": list(DoseUnit), "edit_data": _form_values(item)},
            status_code=422)

    for key, value in values.items():
        setattr(order, key, value)
    if new_coa or remove_coa:
        uploads.delete_coa(order.coa_filename)
        order.coa_filename = new_coa
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)


@router.get("/inventory/{item_id}/orders/{order_id}/coa")
def get_order_coa(item_id: int, order_id: int, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    item = _visible_item(session, item_id, uid)
    order = session.get(Order, order_id) if item else None
    if item is None or order is None or order.inventory_item_id != item.id or not order.coa_filename:
        raise HTTPException(404, "No COA on file")
    path = uploads.coa_path(order.coa_filename)
    if not path.exists():
        raise HTTPException(404, "COA file is missing from disk")
    return FileResponse(path, media_type=uploads.media_type(order.coa_filename),
                        headers={"X-Content-Type-Options": "nosniff"},
                        content_disposition_type="inline")
```

Remove the old `GET /inventory/{item_id}/coa` route (lines 401-411 in the pre-Task-2 file) — it's
fully superseded by `get_order_coa`. Also remove `uploads.delete_coa(item.coa_filename)` from
`delete_item` (the item no longer has its own `coa_filename`; deleting the item cascades to its
Orders via `ondelete="CASCADE"`, but the COA *files on disk* for those orders are not
auto-deleted by a DB cascade — leave a call that deletes each order's COA file before the item is
deleted):

```python
@router.post("/inventory/{item_id}/delete")
def delete_item(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    for order in item.orders:
        uploads.delete_coa(order.coa_filename)
    session.delete(item)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)
```

Add the Order History section to `app/templates/inventory/detail.html`, right before
`{% endblock %}`:

```html
{% if item.category.value != 'Supply' %}
<section aria-labelledby="orders-heading">
  <h2 id="orders-heading" class="section-title">Order history</h2>
  {% if is_owner %}
  <button type="button" class="btn btn-ghost" data-action="add-order">Add order</button>
  {% endif %}
  {% if item.orders %}
  <table class="inv-table">
    <thead><tr><th>Quantity</th><th>Ordered</th><th>Shipped</th><th>Arrived</th><th>Tracking</th><th>Vendor</th><th>Lot #</th><th>Cost</th><th>COA</th>{% if is_owner %}<th></th>{% endif %}</tr></thead>
    <tbody>
      {% for o in item.orders %}
      <tr>
        <td>{{ o.quantity }}</td>
        <td>{{ o.order_date | shortdate }}</td>
        <td>{{ o.shipped_date | shortdate or '—' }}</td>
        <td>{{ o.arrival_date | shortdate or '—' }}</td>
        <td>
          {% if o.tracking_site %}<a href="{{ o.tracking_site }}" target="_blank" rel="noopener">{{ o.tracking_number or 'Track' }}</a>{% else %}{{ o.tracking_number or '—' }}{% endif %}
        </td>
        <td>{{ o.vendor or '—' }}</td>
        <td>{{ o.lot_number or '—' }}</td>
        <td>{{ o.cost | money or '—' }}</td>
        <td>{% if o.coa_filename %}<a href="/inventory/{{ item.id }}/orders/{{ o.id }}/coa" target="_blank" rel="noopener">View</a>{% else %}—{% endif %}</td>
        {% if is_owner %}<td><button type="button" class="btn btn-ghost" data-action="edit-order" data-order-id="{{ o.id }}">Edit</button></td>{% endif %}
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="muted">No orders yet.</p>
  {% endif %}
</section>

<dialog id="order-dialog" class="dialog">
  <form method="post" class="item-form" enctype="multipart/form-data" novalidate>
    <header class="dialog-head">
      <h2 data-title>Add order</h2>
      <button type="button" class="btn btn-ghost btn-icon" data-action="close-order" aria-label="Close">×</button>
    </header>
    <div class="grid">
      <label class="field"><span>Quantity</span><input name="quantity" type="number" min="1" step="1"></label>
      <label class="field"><span>Order date</span><input name="order_date" type="date"></label>
      <label class="field"><span>Shipped date</span><input name="shipped_date" type="date"></label>
      <label class="field"><span>Arrival date</span><input name="arrival_date" type="date"></label>
      <label class="field"><span>Tracking site (URL)</span><input name="tracking_site" type="url"></label>
      <label class="field"><span>Tracking number</span><input name="tracking_number"></label>
      <label class="field"><span>Vendor</span><input name="vendor"></label>
      <label class="field"><span>Lot / Batch #</span><input name="lot_number"></label>
      <label class="field"><span>Cost ($)</span><input name="cost" type="number" min="0" step="0.01"></label>
      <label class="field"><span>Tax ($)</span><input name="tax" type="number" min="0" step="0.01"></label>
      <label class="field"><span>Shipping ($)</span><input name="shipping" type="number" min="0" step="0.01"></label>
      <label class="field"><span>Expiration date</span><input name="expiration_date" type="date"></label>
      <label class="field span-2"><span>COA (photo or PDF)</span><input name="coa" type="file" accept="image/*,.heic,.heif,application/pdf"></label>
      <label class="field"><span>Lab vial size (mg)</span><input name="coa_vial_size_mg" type="number" min="0" step="any"></label>
      <label class="field"><span>Lab purity (%)</span><input name="coa_purity_pct" type="number" min="0" max="100" step="any"></label>
    </div>
    <footer class="dialog-foot">
      <button type="button" class="btn" data-action="close-order">Cancel</button>
      <button type="submit" class="btn btn-primary">Save</button>
    </footer>
  </form>
</dialog>
{% endif %}
```

Add the dialog-wiring JS to a new `app/static/js/inventory-detail.html`-scoped inline script or a
small addition to `inventory.js` (this repo's existing pattern is one shared `inventory.js` for
the whole inventory area, so extend it) — append to `app/static/js/inventory.js`:

```javascript
// ---------------------------------------------------------------- item detail: order dialog
(() => {
  const dialog = document.getElementById("order-dialog");
  if (!dialog) return;  // Supply items have no Order History section
  const form = dialog.querySelector("form");

  document.querySelectorAll('[data-action="add-order"]').forEach((btn) => btn.addEventListener("click", () => {
    form.reset();
    form.action = window.location.pathname + "/orders";
    dialog.querySelector("[data-title]").textContent = "Add order";
    dialog.showModal();
  }));
  document.querySelectorAll('[data-action="edit-order"]').forEach((btn) => btn.addEventListener("click", () => {
    form.action = `${window.location.pathname}/orders/${btn.dataset.orderId}`;
    dialog.querySelector("[data-title]").textContent = "Edit order";
    dialog.showModal();
  }));
  dialog.querySelectorAll('[data-action="close-order"]').forEach((btn) => btn.addEventListener("click", () => dialog.close()));
})();
```

Wire in `app/templates/inventory/detail.html`'s `{% block scripts %}` (add if not already
present, mirroring `list.html`'s pattern at lines 339-341):

```html
{% block scripts %}
<script src="{{ static_url('js/inventory.js') }}" defer></script>
{% endblock %}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass except the already-known `test_backup.py` gap (Task 7).

```bash
git add app/routers/inventory.py app/templates/inventory/detail.html app/static/js/inventory.js tests/test_inventory.py
git commit -m "feat: Order History section, add/edit order, per-order COA"
```

---

## Task 6: Reconstitute integration — `available_count`/`reconstituted_count`

**Files:**
- Modify: `app/routers/calculator.py` (inventory dropdown query, reconstitute commit route)
- Modify: `app/templates/inventory/list.html` (Reconstitute button's count/medium checks — this
  gets rewritten again in Task 7, but keep it correct now so this task is independently testable)
- Test: `tests/test_active_vials.py`

**Interfaces:**
- Consumes: `Category`, `available_count`, `reconstituted_count` from Task 1.
- Produces: the Calculator and Reconstitute now read/write derived fields instead of `count`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_active_vials.py -- add near the other calculator/reconstitute tests
def test_reconstitute_only_counts_arrived_orders_not_in_transit_ones(client, db):
    client.post("/inventory", data={
        "name": "AV Order Test", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Order Test"))
        assert s.get(InventoryItem, item_id).available_count == 0  # first order hasn't arrived

    t = text(client.get("/calculator"))
    assert f'<option value="{item_id}"' not in t  # count 0 -- excluded from the dropdown, same as before

    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    }, follow_redirects=False)
    assert r.status_code == 303 and "reconstitute_error" in r.headers["location"]

    with SessionLocal() as s:
        assert s.query(ActiveVial).filter_by(inventory_item_id=item_id).count() == 0


def test_reconstitute_commit_increments_reconstituted_count_not_raw_count(client, db, me):
    client.post("/inventory", data={
        "name": "AV Reconstituted Count", "category": "Medicine", "medium": "Lyophilized",
        "vial_size_mg": "10", "quantity": "2", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV Reconstituted Count"))
        s.get(InventoryItem, item_id).orders[0].arrival_date = date(2026, 8, 10)
        s.commit()

    client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.reconstituted_count == 1
        assert item.count == 0  # untouched -- vestigial for Medicine
        assert item.available_count == 1  # 2 arrived - 1 reconstituted


def test_bac_water_item_never_reconstitutable(client, db):
    client.post("/inventory", data={"name": "AV BAC Water", "category": "BAC Water", "quantity": "4", "order_date": "2026-08-01"})
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "AV BAC Water"))
        s.get(InventoryItem, item_id).orders[0].arrival_date = date(2026, 8, 10)
        s.commit()
    t = text(client.get("/calculator"))
    assert f'<option value="{item_id}"' not in t
    r = client.post("/calculator/reconstitute", data={
        "inventory_item_id": str(item_id), "water_ml": "2", "dose_value": "250", "dose_unit": "mcg",
        "discard_by": "2026-12-31",
    })
    assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_active_vials.py -k "in_transit or reconstituted_count or bac_water_item_never" -v`
Expected: FAIL — `calculator.py` still filters on `InventoryItem.vial_size_mg.is_not(None)` with
no category check, and still reads/writes `item.count` directly.

- [ ] **Step 3: Implement**

In `app/routers/calculator.py`, add `Category` to the `app.models` import (line 20). Replace the
inventory query in `calculator_page` (lines 58-63):

```python
    inventory = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.owner_id == uid, InventoryItem.category == Category.MEDICINE,
              InventoryItem.medium == Medium.LYOPHILIZED, InventoryItem.vial_size_mg.is_not(None),
              InventoryItem.vial_size_unit == DoseUnit.MG)
        .order_by(InventoryItem.name.collate("NOCASE"))
    ).all()
    inventory = [i for i in inventory if i.available_count > 0]
```

Update the `data["inventory"]` dict comprehension (line 86) to use `available_count`:

```python
        "inventory": [{"id": i.id, "name": i.name, "vial_mg": i.vial_size_mg, "count": i.available_count} for i in inventory],
```

Replace the reconstitute route's item lookup and count checks (lines 108-118, 146):

```python
    item = session.get(InventoryItem, item_id)
    if item is None or item.owner_id != uid or item.category != Category.MEDICINE or item.vial_size_unit != DoseUnit.MG:
        raise HTTPException(status_code=404)
    ...
    if item.available_count <= 0:
        errors.append("This item has none left in stock to reconstitute.")
```

and replace `item.count -= 1` (line 146) with `item.reconstituted_count += 1`.

In `app/templates/inventory/list.html`, update the Reconstitute button's guards (lines 75, 79):

```html
          {% if item.category.value == 'Medicine' and item.medium and item.medium.value == 'Lyophilized' and item.vial_size_mg and item.vial_size_unit.value == 'mg' %}
          <button type="button" class="btn btn-ghost" data-action="reconstitute" data-item-id="{{ item.id }}"
                  data-item-name="{{ item.name }}"
                  {% if item.id in open_vials %}data-active-vial='{{ {"concentration": open_vials[item.id].concentration_mg_ml, "doses": open_vials[item.id].doses_total, "discard_by": open_vials[item.id].discard_by.isoformat()} | tojson }}'{% endif %}
                  {% if item.available_count == 0 %}disabled{% endif %}>Reconstitute</button>
          {% endif %}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_active_vials.py -v`
Expected: all pass. Some pre-existing `test_active_vials.py` tests that post the old flat
vendor/lot/order fields to `/inventory` for their `lyo_item`/`mcg_item` fixtures will need their
payloads updated to the new `category`/`quantity`/`order_date` shape (and an `arrival_date` set on
the resulting order, directly via `SessionLocal`, since the Add Item form no longer accepts
arrival_date) — update `tests/test_active_vials.py`'s `lyo_item` and `mcg_item` fixtures in this
same step so every test in that file keeps passing.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass except `test_backup.py` (Task 7).

```bash
git add app/routers/calculator.py app/templates/inventory/list.html tests/test_active_vials.py
git commit -m "feat: Reconstitute reads/writes available_count and reconstituted_count"
```

---

## Task 7: Inventory list resectioning — categories, In-Transit, drop Notes column

**Files:**
- Modify: `app/routers/inventory.py` (`_render_list` — group by category, build In-Transit list)
- Modify: `app/templates/inventory/list.html` (replace the single table with five sections)
- Modify: `app/static/js/inventory.js` (search/filter/sort currently assumes one `#inv-table` —
  needs to work per-section or be simplified; see Step 3)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `Category`, `available_count` from Task 1; the detail-page link from Task 4.
- Produces: `_render_list` passes `medicine_items`, `bac_water_items`, `supply_items`,
  `in_transit_orders` (each a list) instead of one flat `items` list for the table (still passes
  `items`/`owner_names` for the search box and the Add-item edit_data map, unchanged).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inventory.py
def test_inventory_page_sections_items_by_category(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    client.post("/inventory", data={"name": "Bac Water", "category": "BAC Water", "quantity": "4", "order_date": "2026-08-01"})
    client.post("/inventory", data={"name": "Alcohol Pads", "category": "Supply", "count": "250"})
    t = text(client.get("/inventory"))
    assert t.index("Retatrutide") < t.index("Bac Water") < t.index("Alcohol Pads")
    assert '<h2 id="medicines-heading"' in t
    assert '<h2 id="bac-water-heading"' in t
    assert '<h2 id="supplies-heading"' in t


def test_inventory_page_shows_in_transit_orders(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_number": "LY123",
    })
    t = text(client.get("/inventory"))
    section = t.split('id="in-transit"')[1].split("</section>")[0]
    assert "Retatrutide" in section and "LY123" in section


def test_inventory_page_notes_removed_from_list_columns(client, db):
    client.post("/inventory", data={"name": "Alcohol Pads", "category": "Supply", "count": "5", "notes": "keep in the closet"})
    t = text(client.get("/inventory"))
    section = t.split('id="supplies-heading"')[1].split("</section>")[0]
    assert "keep in the closet" not in section
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -k "sections_items or in_transit or notes_removed" -v`
Expected: FAIL — the current list page is one flat table with no per-category headings and no
In-Transit section.

- [ ] **Step 3: Implement**

In `app/routers/inventory.py`, replace `_render_list` (lines 262-304) to add the grouped lists:

```python
def _render_list(request: Request, session: Session, *, form: dict | None = None, errors=None,
                 editing: InventoryItem | None = None, status_code: int = 200):
    uid = request.state.user.id
    items, owner_names = _visible_items(session, uid)
    medicine_items = [i for i in items if i.category == Category.MEDICINE]
    bac_water_items = [i for i in items if i.category == Category.BAC_WATER]
    supply_items = [i for i in items if i.category == Category.SUPPLY]
    in_transit_orders = [
        (item, order) for item in medicine_items + bac_water_items for order in item.orders
        if order.arrival_date is None
    ]
    own_lyo_ids = [i.id for i in medicine_items if i.owner_id == uid and i.medium == Medium.LYOPHILIZED]
    open_vials = _open_active_vials(session, own_lyo_ids)
    vials, vial_items, vial_owner_names = _visible_active_vials(session, uid)
    now = now_utc()
    expired_prompts = {
        v.id for v in vials
        if v.owner_id == uid and v.discard_by < date.today()
        and (v.last_discard_prompt_at is None or now - v.last_discard_prompt_at > timedelta(hours=24))
    }
    return templates.TemplateResponse(
        request,
        "inventory/list.html",
        {
            "items": items,
            "medicine_items": medicine_items,
            "bac_water_items": bac_water_items,
            "supply_items": supply_items,
            "in_transit_orders": in_transit_orders,
            "viewer_id": uid,
            "owner_names": owner_names,
            "open_vials": open_vials,
            "active_vials": vials,
            "vial_items": vial_items,
            "vial_owner_names": vial_owner_names,
            "expired_prompts": expired_prompts,
            "today": date.today(),  # raw date object -- Active Vials' `v.discard_by < today` needs this, not a string
            "today_iso": date.today().isoformat(),  # Add Item form's order_date input default
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

In `app/templates/inventory/list.html`, replace the whole `{% if items %}...{% endif %}` block
(lines 16-101, the search/filter toolbar plus the single `<table class="inv-table">`) with a
macro-driven per-category table plus an In-Transit section. Add near the top of `{% block
content %}` (right after the `page-head` div, before line 16):

```html
{% macro item_table(section_id, heading, items, show_medium) %}
<section id="{{ section_id }}" aria-labelledby="{{ section_id }}-heading">
  <h2 id="{{ section_id }}-heading" class="section-title">{{ heading }}</h2>
  {% if items %}
  <div class="table-wrap">
    <table class="inv-table">
      <thead>
        <tr>
          <th>Item</th>
          <th class="num">Count</th>
          {% if show_medium %}<th class="num">Amount</th><th>Medium</th>{% else %}<th class="num">Cost</th><th>Vendor</th>{% endif %}
          <th>Storage</th>
          <th><span class="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        {% for item in items %}
        <tr>
          <td data-label="Item" class="item-name">
            <a href="/inventory/{{ item.id }}">{{ item.name }}</a>
            {% if item.owner_id != viewer_id %}<div class="muted small">Shared by {{ owner_names[item.owner_id] }}</div>{% endif %}
          </td>
          <td data-label="Count" class="num">{{ item.available_count }}</td>
          {% if show_medium %}
          <td data-label="Amount" class="num">{% if item.vial_size_mg %}{{ '%g' % item.vial_size_mg }} {{ item.vial_size_unit.value }}{% else %}—{% endif %}</td>
          <td data-label="Medium">{% if item.medium %}<span class="tag">{{ item.medium.value }}</span>{% else %}—{% endif %}</td>
          {% else %}
          <td data-label="Cost" class="num">{{ item.cost | money or '—' }}</td>
          <td data-label="Vendor">{{ item.vendor or '—' }}</td>
          {% endif %}
          <td data-label="Storage">{{ item.storage.label if item.storage else '—' }}</td>
          <td class="actions">
            {% if item.owner_id == viewer_id %}
            {% if item.category.value == 'Medicine' and item.medium and item.medium.value == 'Lyophilized' and item.vial_size_mg and item.vial_size_unit.value == 'mg' %}
            <button type="button" class="btn btn-ghost" data-action="reconstitute" data-item-id="{{ item.id }}"
                    data-item-name="{{ item.name }}"
                    {% if item.id in open_vials %}data-active-vial='{{ {"concentration": open_vials[item.id].concentration_mg_ml, "doses": open_vials[item.id].doses_total, "discard_by": open_vials[item.id].discard_by.isoformat()} | tojson }}'{% endif %}
                    {% if item.available_count == 0 %}disabled{% endif %}>Reconstitute</button>
            {% endif %}
            <form method="post" action="/inventory/{{ item.id }}/delete" class="inline" data-confirm="Delete “{{ item.name }}”? This can't be undone.">
              <button type="submit" class="btn btn-ghost btn-danger">Delete</button>
            </form>
            {% else %}
            <span class="muted small">Read only</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <p class="muted">None yet.</p>
  {% endif %}
</section>
{% endmacro %}

{{ item_table('medicines', 'Peptides / Medicines', medicine_items, true) }}
{{ item_table('bac-water', 'BAC Water', bac_water_items, false) }}
{{ item_table('supplies', 'Supplies', supply_items, false) }}

<section id="in-transit" aria-labelledby="in-transit-heading">
  <h2 id="in-transit-heading" class="section-title">In-transit</h2>
  {% if in_transit_orders %}
  <div class="table-wrap">
    <table class="inv-table">
      <thead><tr><th>Item</th><th class="num">Quantity</th><th>Ordered</th><th>Shipped</th><th>Tracking</th></tr></thead>
      <tbody>
        {% for item, order in in_transit_orders %}
        <tr>
          <td><a href="/inventory/{{ item.id }}">{{ item.name }}</a></td>
          <td class="num">{{ order.quantity }}</td>
          <td>{{ order.order_date | shortdate }}</td>
          <td>{{ order.shipped_date | shortdate or '—' }}</td>
          <td>{% if order.tracking_site %}<a href="{{ order.tracking_site }}" target="_blank" rel="noopener">{{ order.tracking_number or 'Track' }}</a>{% else %}{{ order.tracking_number or '—' }}{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <p class="muted">Nothing in transit.</p>
  {% endif %}
</section>
```

Remove the old single-table block (the original lines 16-101) entirely — the macro calls above
replace it. Keep the empty-state block (original lines 96-100, `{% else %}...No inventory
yet...{% endif %}`) but move its condition to check `items` still being empty overall (unchanged
logic, just no longer wrapping a single table).

The Active Vials section (lines 103-136 in the original) is unchanged — leave it exactly as-is,
in place right after the four sections above (Active Vials still renders first visually per the
spec's ordering: Active Vials → Medicines → BAC Water → Supplies → In-Transit — so the macro calls
above and the In-Transit section must come *after* the existing `<section
id="active-vials">...</section>` block in the file, not before; reorder accordingly. Active Vials'
existing markup is untouched, only its position relative to the new sections matters).

The `#inv-search`/filter-chips toolbar (original lines 17-23) operated on the single `#inv-table`;
with five separate tables it no longer has one table to filter. Remove that toolbar entirely for
this task (search/filter across the new sectioned layout is not in this spec's scope — note it in
this task's ledger as a deferred minor, since the spec didn't call for search redesign and adding
it now would be scope creep beyond "resection the list").

In `app/static/js/inventory.js`, the "search / filter / sort" IIFE (lines 111-158 in the original)
assumes a single `#inv-table` and will no-op harmlessly once that toolbar/table no longer exist in
the DOM (`document.getElementById("inv-table")` returns `null`, and the function returns early —
check the existing early-return guard at line 114, `if (!table) return;`). No JS change is
strictly required for this to fail safely, but delete this now-dead IIFE (lines 111-158) since it
references DOM elements (`#inv-search`, `[data-filter]`) that no longer exist anywhere in the
page — leaving it in is dead code, not a graceful degradation.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py tests/test_active_vials.py -v`
Expected: all pass. Existing tests in both files that assert on the old single-`#inv-table`
structure (e.g. anything doing `t.split('id="inv-table"')` or asserting column headers like
"Vendor"/"Arrived"/"COA" in the main table) need their assertions updated to the new per-section
structure in this same step — locate them with
`.venv/Scripts/python -m pytest tests/test_inventory.py tests/test_active_vials.py -v` and fix
each failure by re-pointing the assertion at the correct section id (`#medicines`, `#bac-water`,
`#supplies`, `#in-transit`) instead of the removed `#inv-table`.

- [ ] **Step 5: Manual browser check**

Start the dev server, open `/inventory` with at least one item in each category plus one
in-transit order, and confirm the section order top-to-bottom is: Active vials, Peptides/
Medicines, BAC Water, Supplies, In-transit — and that Notes no longer appears anywhere on this
page (only on the item detail page). Report what you saw.

- [ ] **Step 6: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass except `test_backup.py` (Task 8, next).

```bash
git add app/routers/inventory.py app/templates/inventory/list.html app/static/js/inventory.js tests/test_inventory.py tests/test_active_vials.py
git commit -m "feat: resection Inventory list by category, add In-Transit, drop Notes column"
```

---

## Task 8: Backup export/import — Orders and category

**Files:**
- Modify: `app/routers/backup.py` (`_inventory_row`, `CSV_COLUMNS`, `_import_inventory_row`,
  JSON export/import payload shape)
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: `Category`, `Order` from Task 1.
- Produces: JSON export gains an `"orders"` key per inventory row (a list); CSV export gains a
  second CSV section for orders (mirroring how this file already would extend for a second
  entity — there's no existing second-sheet CSV precedent in this codebase, so this plan uses a
  simple `---ORDERS---` marker line splitting two CSV blocks within the one response body, the
  simplest correct approach for a single-file CSV download).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backup.py
def test_json_export_includes_orders_and_category(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_number": "LY123",
    })
    payload = client.get("/backup/export.json").json()
    item = next(i for i in payload["inventory"] if i["name"] == "Retatrutide")
    assert item["category"] == "Medicine"
    assert item["orders"][0]["quantity"] == 10 and item["orders"][0]["tracking_number"] == "LY123"


def test_json_import_recreates_item_and_its_orders(client, db):
    payload = {
        "inventory": [{
            "name": "Imported Peptide", "category": "Medicine", "medium": "Lyophilized",
            "vial_size_mg": 10, "vial_size_unit": "mg",
            "orders": [{"quantity": 5, "order_date": "2026-08-01", "arrival_date": "2026-08-10",
                       "tracking_number": "LY999"}],
        }],
    }
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Imported Peptide"))
        assert item.category == Category.MEDICINE
        assert item.orders[0].quantity == 5 and item.orders[0].tracking_number == "LY999"
        assert item.available_count == 5


def test_json_import_tolerates_old_backup_shape_with_no_orders_key(client, db):
    payload = {"inventory": [{"name": "Old Supply", "category": "Supply", "count": 3}]}
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Old Supply"))
        assert item.category == Category.SUPPLY and item.available_count == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_backup.py -k "orders_and_category or recreates_item_and_its_orders or old_backup_shape" -v`
Expected: FAIL — `_inventory_row`/`_import_inventory_row` still reference the removed item-level
`vendor`/`lot_number`/`order_date`/etc. columns directly (this will actually raise
`AttributeError` on `_inventory_row`, since those columns no longer exist on `InventoryItem` after
Task 1 — confirm the failure is that, not a silent wrong-value pass).

- [ ] **Step 3: Implement**

Replace `_inventory_row` and add an order-row helper in `app/routers/backup.py`:

```python
def _inventory_row(i: InventoryItem) -> dict:
    return {
        "name": i.name, "category": i.category.value, "count": i.count, "vial_size_mg": i.vial_size_mg,
        "vial_size_unit": i.vial_size_unit.value, "medium": i.medium.value if i.medium else None,
        "volume_ml": i.volume_ml, "units_per_package": i.units_per_package,
        "storage": i.storage.value if i.storage else None,
        "cost": i.cost, "vendor": i.vendor, "notes": i.notes,
        "orders": [_order_row(o) for o in i.orders],
    }


def _order_row(o: Order) -> dict:
    return {
        "quantity": o.quantity, "order_date": _iso(o.order_date), "shipped_date": _iso(o.shipped_date),
        "arrival_date": _iso(o.arrival_date), "tracking_site": o.tracking_site,
        "tracking_number": o.tracking_number, "vendor": o.vendor, "lot_number": o.lot_number,
        "cost": o.cost, "tax": o.tax, "shipping": o.shipping, "expiration_date": _iso(o.expiration_date),
        "coa_vial_size_mg": o.coa_vial_size_mg, "coa_purity_pct": o.coa_purity_pct,
    }
```

Add `Category`, `Order` to the `app.models` import (line 19-22).

`export_json`'s query (lines 71-72) needs `selectinload(InventoryItem.orders)` to avoid N+1 (the
existing `Protocol` query already uses `selectinload` for its nested collections — match that
pattern):

```python
    inventory = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid)
        .options(selectinload(InventoryItem.orders)).order_by(InventoryItem.name)
    ).all()
```

Replace `CSV_COLUMNS` and `export_inventory_csv` (lines 90-111) — one CSV response with two
sections, items first, then a blank line, then orders:

```python
CSV_COLUMNS = [
    ("Name", "name"), ("Category", "category"), ("Count", "count"), ("Amount", "vial_size_mg"),
    ("Unit", "vial_size_unit"), ("Medium", "medium"), ("Volume (mL)", "volume_ml"),
    ("Units per package", "units_per_package"), ("Storage", "storage"), ("Cost", "cost"),
    ("Vendor", "vendor"), ("Notes", "notes"),
]
ORDER_CSV_COLUMNS = [
    ("Item", "item_name"), ("Quantity", "quantity"), ("Order date", "order_date"),
    ("Shipped date", "shipped_date"), ("Arrival date", "arrival_date"), ("Tracking site", "tracking_site"),
    ("Tracking number", "tracking_number"), ("Vendor", "vendor"), ("Lot/Batch #", "lot_number"),
    ("Cost", "cost"), ("Tax", "tax"), ("Shipping", "shipping"), ("Expiration", "expiration_date"),
]


@router.get("/backup/export/inventory.csv")
def export_inventory_csv(session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    inventory = session.scalars(
        select(InventoryItem).where(InventoryItem.owner_id == uid)
        .options(selectinload(InventoryItem.orders)).order_by(InventoryItem.name)
    ).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for header, _ in CSV_COLUMNS])
    for i in inventory:
        row = _inventory_row(i)
        writer.writerow(["" if row[key] is None else row[key] for _, key in CSV_COLUMNS])
    writer.writerow([])
    writer.writerow([header for header, _ in ORDER_CSV_COLUMNS])
    for i in inventory:
        for o in i.orders:
            row = {**_order_row(o), "item_name": i.name}
            writer.writerow(["" if row[key] is None else row[key] for _, key in ORDER_CSV_COLUMNS])
    filename = f"amide-inventory-{date.today().isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
```

Replace `_import_inventory_row` (lines 116-131):

```python
def _import_inventory_row(session: Session, uid: int, row: dict) -> None:
    medium = Medium(row["medium"]) if row.get("medium") else None
    item = InventoryItem(
        owner_id=uid, name=row["name"], category=Category(row.get("category") or "Medicine"),
        count=row.get("count", 1), vial_size_mg=row.get("vial_size_mg"),
        vial_size_unit=DoseUnit(row.get("vial_size_unit") or "mg"), medium=medium,
        volume_ml=row.get("volume_ml"), units_per_package=row.get("units_per_package"),
        storage=StorageLocation(row["storage"]) if row.get("storage") else None,
        cost_cents=round(row["cost"] * 100) if row.get("cost") is not None else None,
        vendor=row.get("vendor"), notes=row.get("notes"),
    )
    for o in row.get("orders", []):  # absent entirely in a pre-Order-history backup file -- treat as none
        item.orders.append(Order(
            quantity=o["quantity"], order_date=date.fromisoformat(o["order_date"]),
            shipped_date=date.fromisoformat(o["shipped_date"]) if o.get("shipped_date") else None,
            arrival_date=date.fromisoformat(o["arrival_date"]) if o.get("arrival_date") else None,
            tracking_site=o.get("tracking_site"), tracking_number=o.get("tracking_number"),
            vendor=o.get("vendor"), lot_number=o.get("lot_number"),
            cost_cents=round(o["cost"] * 100) if o.get("cost") is not None else None,
            tax_cents=round(o["tax"] * 100) if o.get("tax") is not None else None,
            shipping_cents=round(o["shipping"] * 100) if o.get("shipping") is not None else None,
            expiration_date=date.fromisoformat(o["expiration_date"]) if o.get("expiration_date") else None,
            coa_vial_size_mg=o.get("coa_vial_size_mg"), coa_purity_pct=o.get("coa_purity_pct"),
        ))
    session.add(item)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_backup.py -v`
Expected: all pass. Fix any other pre-existing `test_backup.py` test whose payload/assertions
still reference the old flat item-level order fields, in this same step.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass — this is the last task with a known pending gap, so the whole suite should be
green now.

```bash
git add app/routers/backup.py tests/test_backup.py
git commit -m "feat: backup export/import carries Order history and category"
```

---

## Task 9: JSON API — category, `available_count`, orders

**Files:**
- Modify: `app/routers/inventory.py` (`_to_json`)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `Category`, `available_count`, `Order` from Task 1.
- Produces: `/api/inventory` and `/api/inventory/{id}` responses gain `category`,
  `available_count`, `orders`; drop the removed item-level fields.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_inventory.py
def test_api_inventory_includes_category_available_count_and_orders(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_number": "LY123",
    })
    body = client.get("/api/inventory").json()
    item = next(i for i in body if i["name"] == "Retatrutide")
    assert item["category"] == "Medicine"
    assert item["available_count"] == 0  # not arrived yet
    assert item["orders"][0]["tracking_number"] == "LY123"
    assert "lot_number" not in item  # moved to orders, no longer a bare item field
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -k api_inventory_includes -v`
Expected: FAIL — `_to_json` still tries to read the removed `item.lot_number`/`item.order_date`/
etc. attributes (`AttributeError`), or (if this task runs after Task 8's model already dropped
those columns) the response simply lacks `category`/`available_count`/`orders`.

- [ ] **Step 3: Implement**

Replace `_to_json` in `app/routers/inventory.py` (lines 421-446):

```python
def _to_json(item: InventoryItem) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category.value,
        "available_count": item.available_count,
        "count": item.count,
        "vial_size_mg": item.vial_size_mg,
        "vial_size_unit": item.vial_size_unit.value,
        "medium": item.medium.value if item.medium else None,
        "volume_ml": item.volume_ml,
        "units_per_package": item.units_per_package,
        "storage": item.storage.value if item.storage else None,
        "cost": item.cost,
        "vendor": item.vendor,
        "notes": item.notes,
        "orders": [{
            "id": o.id, "quantity": o.quantity, "order_date": _iso(o.order_date),
            "shipped_date": _iso(o.shipped_date), "arrival_date": _iso(o.arrival_date),
            "tracking_site": o.tracking_site, "tracking_number": o.tracking_number,
            "vendor": o.vendor, "lot_number": o.lot_number, "cost": o.cost, "tax": o.tax,
            "shipping": o.shipping, "expiration_date": _iso(o.expiration_date),
            "has_coa": bool(o.coa_filename), "coa_vial_size_mg": o.coa_vial_size_mg,
            "coa_purity_pct": o.coa_purity_pct,
        } for o in item.orders],
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_inventory.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

```bash
git add app/routers/inventory.py tests/test_inventory.py
git commit -m "feat: /api/inventory exposes category, available_count, orders"
```

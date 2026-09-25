# Multi-Item Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `Order` into a shared header plus per-item `OrderItem` lines, so one real-world
shipment containing several items (a restock, a brand-new peptide, some BAC Water) can be entered,
tracked, and checked in as one order — with damaged/short receipts caught at check-in never
reaching usable inventory.

**Architecture:** `Order` (header: dates, tracking, vendor, tax/shipping totals) gets a new
`OrderItem` (line: quantity, cost, lot/expiration/COA, and `received_quantity` — null until
check-in, then editable down from the ordered quantity). `InventoryItem.available_count` sums
`received_quantity` instead of `quantity` across arrived lines. A single combined "check-in" action
sets `Order.arrival_date` and every line's `received_quantity` at once — there is no intermediate
"physically arrived but unchecked" state. A new "New Order" form lets one order contain several
lines, each either restocking an existing item or introducing a new one inline.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + Alembic (SQLite) + Jinja2 + vanilla JS — same stack as
the rest of the app.

**Spec:** `docs/superpowers/specs/2026-09-25-multi-item-orders-design.md`

## Global Constraints

- Money is stored as integer cents, converted with the existing `_parse_money` helper.
- `received_quantity` must satisfy `0 <= received_quantity <= quantity` (`ck_order_item_received_range`).
- A short/damaged check-in must never inflate `available_count` — only `received_quantity` counts,
  never the original `quantity` ordered.
- A multi-item order's new-item lines are Medicine or BAC Water only — never Supply.
- Ownership is `_own_item` (already defined in `app/routers/inventory.py`) — never trust a posted
  item id, including a new order line's `item_id`, without it.
- Deleting an item's last remaining `OrderItem` line also deletes the now-orphaned `Order` header.

## Review Focus

- A short/damaged check-in (`received_quantity < quantity`) never inflates `available_count`.
- Before check-in, an in-transit line contributes zero to `available_count`, even though a quantity
  was entered at order time.
- A brand-new item created inline as one line of a multi-item order gets the same category
  validation (required Medium/Amount fields) as one created through the existing Add Item form.
- Deleting an item that was the sole remaining line in its order also removes the orphaned `Order`
  header; deleting one line of a still-multi-line order leaves the header and its other lines intact.
- The shipping/tax weighted split (per-line allocated cost) sums exactly back to the entered
  order-level totals, with no dropped or invented cent (largest-remainder allocation).

---

### Task 1: `Order`/`OrderItem` model split and migration

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0013_multi_item_orders.py`
- Modify: `tests/test_inventory.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `Order` (header-only), `OrderItem` (line, with `received_quantity`/`received_note`),
  `InventoryItem.order_items` relationship, updated `InventoryItem.available_count` — every later
  task in this plan depends on these exact names and semantics.

- [ ] **Step 1: Write the failing model tests**

Add to `tests/test_inventory.py`, replacing the existing
`test_available_count_medicine_sums_arrived_orders_minus_reconstituted_and_sold` test (it
constructs an `Order` the old way and must change to the new shape):

```python
def test_available_count_uses_received_quantity_not_ordered_quantity(db, me):
    from app.models import OrderItem

    item = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                         medium=Medium.LYOPHILIZED, vial_size_mg=10, reconstituted_count=2, sold_count=1)
    db.add(item)
    db.flush()
    arrived_order = Order(order_date=date(2026, 8, 1), arrival_date=date(2026, 8, 10))
    in_transit_order = Order(order_date=date(2026, 9, 20))
    db.add_all([arrived_order, in_transit_order])
    db.flush()
    item.order_items.append(OrderItem(order_id=arrived_order.id, quantity=10, received_quantity=8))
    item.order_items.append(OrderItem(order_id=in_transit_order.id, quantity=5))  # not checked in
    db.commit()
    db.refresh(item)
    # 8 received (not the 10 ordered -- 2 were short) - 2 reconstituted - 1 sold; the in-transit
    # line's 5 don't count at all yet.
    assert item.available_count == 8 - 2 - 1


def test_order_item_received_quantity_cannot_exceed_ordered_quantity(db, me):
    from app.models import OrderItem

    item = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                         medium=Medium.LYOPHILIZED, vial_size_mg=10)
    db.add(item)
    db.flush()
    order = Order(order_date=date(2026, 8, 1), arrival_date=date(2026, 8, 10))
    db.add(order)
    db.flush()
    with pytest.raises(IntegrityError):
        db.add(OrderItem(order_id=order.id, inventory_item_id=item.id, quantity=5, received_quantity=6))
        db.flush()
    db.rollback()
```

Add `import pytest` and `from sqlalchemy.exc import IntegrityError` to the top of
`tests/test_inventory.py` if not already present (check first).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -k "received_quantity" -v`
Expected: FAIL — `Order(order_date=..., arrival_date=...)` with no `inventory_item_id`/`quantity`
raises a `TypeError` against the current model (those columns don't exist as constructor kwargs
after this task, but do right now — so today's `Order` still requires `inventory_item_id`; this
test fails today with a different `TypeError` about a missing required argument, or an
`AttributeError` on `OrderItem` not existing yet. Either failure confirms the test exercises code
that doesn't exist yet).

- [ ] **Step 3: Rewrite `Order`, add `OrderItem`, update `InventoryItem`**

In `app/models.py`, replace the `orders` relationship on `InventoryItem` (currently lines 140-141)
with:

```python
    order_items: Mapped[list["OrderItem"]] = relationship(
        back_populates="inventory_item", cascade="all, delete-orphan")
```

Replace `available_count` (currently lines 146-154) with:

```python
    @property
    def available_count(self) -> int:
        """Medicine/BAC Water: received quantity (from checked-in orders only) minus
        reconstituted/sold. Supply: the plain count column. A short or damaged receipt caught at
        check-in never inflates this -- only OrderItem.received_quantity counts, never the
        original quantity ordered."""
        if self.category == Category.SUPPLY:
            return self.count
        arrived = sum(
            (li.received_quantity or 0) for li in self.order_items if li.order.arrival_date is not None)
        return arrived - self.reconstituted_count - self.sold_count
```

Replace the entire `Order` class (currently lines 157-205) with:

```python
class Order(Base):
    """One shipment/vendor order, possibly containing several items (see OrderItem). Filling in
    arrival_date is the single combined "checked in" action -- see OrderItem.received_quantity."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("tax_cents IS NULL OR tax_cents >= 0", name="ck_order_tax_nonneg"),
        CheckConstraint("shipping_cents IS NULL OR shipping_cents >= 0", name="ck_order_shipping_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_date: Mapped[date] = mapped_column(Date)
    shipped_date: Mapped[date | None] = mapped_column(Date)
    arrival_date: Mapped[date | None] = mapped_column(Date)
    tracking_site: Mapped[str | None] = mapped_column(String(500))
    tracking_number: Mapped[str | None] = mapped_column(String(100))
    vendor: Mapped[str | None] = mapped_column(String(200))
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"))
    tax_cents: Mapped[int | None] = mapped_column(Integer)
    shipping_cents: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    @property
    def tax(self) -> float | None:
        return None if self.tax_cents is None else self.tax_cents / 100

    @property
    def shipping(self) -> float | None:
        return None if self.shipping_cents is None else self.shipping_cents / 100


class OrderItem(Base):
    """One line of an Order: a quantity of one InventoryItem, with its own cost/lot/expiration/COA
    (different peptides, different lots of the same peptide, and BAC Water can each carry their
    own COA/lot/expiration even within one shipment). received_quantity is null until the parent
    Order is checked in; check-in fills it in for every line at once (pre-filled to `quantity`,
    editable down for anything short or damaged) -- nothing here counts toward
    InventoryItem.available_count until then."""

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_quantity_pos"),
        CheckConstraint(
            "received_quantity IS NULL OR (received_quantity >= 0 AND received_quantity <= quantity)",
            name="ck_order_item_received_range"),
        CheckConstraint("cost_cents IS NULL OR cost_cents >= 0", name="ck_order_item_cost_nonneg"),
        CheckConstraint("coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0", name="ck_order_item_coa_vial_size_pos"),
        CheckConstraint("coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)",
                        name="ck_order_item_coa_purity_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    received_quantity: Mapped[int | None] = mapped_column(Integer)
    received_note: Mapped[str | None] = mapped_column(String(300))
    cost_cents: Mapped[int | None] = mapped_column(Integer)
    lot_number: Mapped[str | None] = mapped_column(String(100))
    expiration_date: Mapped[date | None] = mapped_column(Date)
    coa_filename: Mapped[str | None] = mapped_column(String(100))
    coa_vial_size_mg: Mapped[float | None] = mapped_column(Float)
    coa_purity_pct: Mapped[float | None] = mapped_column(Float)

    order: Mapped["Order"] = relationship(back_populates="items")
    inventory_item: Mapped["InventoryItem"] = relationship(back_populates="order_items")

    @property
    def cost(self) -> float | None:
        return None if self.cost_cents is None else self.cost_cents / 100
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py -k "received_quantity" -v`
Expected: FAIL — `sqlalchemy.exc.OperationalError: no such table: order_items` (the model exists,
the table doesn't yet — Steps 5-8 add the migration).

- [ ] **Step 5: Write the failing migration test**

Add to `tests/test_migrations.py`, directly after `test_0012_adds_sales_table`:

```python
def test_0013_splits_orders_into_order_items(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0012")
    with sqlite3.connect(db) as c:
        c.execute("insert into users(username,username_key,password_hash,is_admin,totp_enabled,"
                  "failed_attempts,created_at) values ('A','a','x',0,0,0,'2026-09-25')")
        c.execute("insert into inventory_items(name,count,vial_size_unit,category,created_at,"
                  "updated_at,owner_id) values ('Retatrutide',0,'mg','Medicine','2026-09-25',"
                  "'2026-09-25',1)")
        item_id = c.execute("select id from inventory_items where name='Retatrutide'").fetchone()[0]
        # An arrived order (should backfill received_quantity = quantity).
        c.execute("insert into orders(inventory_item_id,quantity,order_date,arrival_date,cost_cents,"
                  "created_at) values (?,10,'2026-08-01','2026-08-10',8400,'2026-08-01')", (item_id,))
        # An in-transit order (should backfill received_quantity = NULL).
        c.execute("insert into orders(inventory_item_id,quantity,order_date,created_at) "
                  "values (?,5,'2026-09-20','2026-09-20')", (item_id,))
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("pragma table_info(order_items)")}
        assert {"order_id", "inventory_item_id", "quantity", "received_quantity", "received_note",
               "cost_cents", "lot_number", "expiration_date", "coa_filename", "coa_vial_size_mg",
               "coa_purity_pct"} <= cols
        order_cols = {r[1] for r in c.execute("pragma table_info(orders)")}
        assert "inventory_item_id" not in order_cols and "quantity" not in order_cols
        assert {"order_date", "arrival_date", "tax_cents", "shipping_cents"} <= order_cols

        rows = c.execute("select quantity, received_quantity, cost_cents from order_items "
                         "order by quantity desc").fetchall()
        assert rows == [(10, 10, 8400), (5, None, None)]
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into order_items(order_id,inventory_item_id,quantity,received_quantity) "
                      "values (1,?,5,6)", (item_id,))  # received > ordered
    command.downgrade(cfg, "0012")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert "order_items" not in tables
        assert "inventory_item_id" in {r[1] for r in c.execute("pragma table_info(orders)")}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_migrations.py::test_0013_splits_orders_into_order_items -v`
Expected: FAIL — no revision `0013` exists yet.

- [ ] **Step 7: Write the migration**

Create `migrations/versions/0013_multi_item_orders.py`:

```python
"""multi-item orders: split Order into an order header + OrderItem lines

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'order_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('inventory_item_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('received_quantity', sa.Integer(), nullable=True),
        sa.Column('received_note', sa.String(length=300), nullable=True),
        sa.Column('cost_cents', sa.Integer(), nullable=True),
        sa.Column('lot_number', sa.String(length=100), nullable=True),
        sa.Column('expiration_date', sa.Date(), nullable=True),
        sa.Column('coa_filename', sa.String(length=100), nullable=True),
        sa.Column('coa_vial_size_mg', sa.Float(), nullable=True),
        sa.Column('coa_purity_pct', sa.Float(), nullable=True),
        sa.CheckConstraint('quantity > 0', name='ck_order_item_quantity_pos'),
        sa.CheckConstraint(
            'received_quantity IS NULL OR (received_quantity >= 0 AND received_quantity <= quantity)',
            name='ck_order_item_received_range'),
        sa.CheckConstraint('cost_cents IS NULL OR cost_cents >= 0', name='ck_order_item_cost_nonneg'),
        sa.CheckConstraint('coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0', name='ck_order_item_coa_vial_size_pos'),
        sa.CheckConstraint('coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)',
                           name='ck_order_item_coa_purity_range'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_order_items_order_id', 'order_items', ['order_id'])
    op.create_index('ix_order_items_inventory_item_id', 'order_items', ['inventory_item_id'])

    # Backfill: one OrderItem per existing Order row. received_quantity backfills to quantity
    # wherever arrival_date is already set (that stock already counted as available under the old
    # model) and stays NULL otherwise (still in transit, nothing to check in retroactively).
    rows = bind.execute(text("""
        SELECT id, inventory_item_id, quantity, cost_cents, lot_number, expiration_date,
               coa_filename, coa_vial_size_mg, coa_purity_pct, arrival_date
        FROM orders
    """)).fetchall()
    for r in rows:
        bind.execute(text("""
            INSERT INTO order_items (order_id, inventory_item_id, quantity, received_quantity,
                                     cost_cents, lot_number, expiration_date, coa_filename,
                                     coa_vial_size_mg, coa_purity_pct)
            VALUES (:order_id, :item_id, :qty, :received_qty, :cost_cents, :lot_number,
                    :expiration_date, :coa_filename, :coa_vial_size_mg, :coa_purity_pct)
        """), {"order_id": r.id, "item_id": r.inventory_item_id, "qty": r.quantity,
              "received_qty": r.quantity if r.arrival_date is not None else None,
              "cost_cents": r.cost_cents, "lot_number": r.lot_number,
              "expiration_date": r.expiration_date, "coa_filename": r.coa_filename,
              "coa_vial_size_mg": r.coa_vial_size_mg, "coa_purity_pct": r.coa_purity_pct})

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_constraint('ck_order_quantity_pos', type_='check')
        batch_op.drop_constraint('ck_order_cost_nonneg', type_='check')
        batch_op.drop_constraint('ck_order_coa_vial_size_pos', type_='check')
        batch_op.drop_constraint('ck_order_coa_purity_range', type_='check')
        batch_op.drop_column('inventory_item_id')
        batch_op.drop_column('quantity')
        batch_op.drop_column('cost_cents')
        batch_op.drop_column('lot_number')
        batch_op.drop_column('expiration_date')
        batch_op.drop_column('coa_filename')
        batch_op.drop_column('coa_vial_size_mg')
        batch_op.drop_column('coa_purity_pct')
    op.drop_index('ix_orders_inventory_item_id', table_name='orders')


def downgrade() -> None:
    op.create_index('ix_orders_inventory_item_id', 'orders', ['inventory_item_id'])
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('coa_purity_pct', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coa_vial_size_mg', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('coa_filename', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('lot_number', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('cost_cents', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('quantity', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('inventory_item_id', sa.Integer(), nullable=True))
        batch_op.create_check_constraint('ck_order_coa_purity_range',
                                         'coa_purity_pct IS NULL OR (coa_purity_pct >= 0 AND coa_purity_pct <= 100)')
        batch_op.create_check_constraint('ck_order_coa_vial_size_pos', 'coa_vial_size_mg IS NULL OR coa_vial_size_mg > 0')
        batch_op.create_check_constraint('ck_order_cost_nonneg', 'cost_cents IS NULL OR cost_cents >= 0')
        batch_op.create_check_constraint('ck_order_quantity_pos', 'quantity > 0')

    # Best-effort: only correct if every order still has exactly one line (true immediately after
    # this migration's upgrade with no multi-item orders committed since -- downgrading after real
    # multi-item orders exist collapses each order down to just its first line).
    bind = op.get_bind()
    rows = bind.execute(text("""
        SELECT order_id, inventory_item_id, quantity, cost_cents, lot_number, expiration_date,
               coa_filename, coa_vial_size_mg, coa_purity_pct
        FROM order_items
        WHERE id IN (SELECT MIN(id) FROM order_items GROUP BY order_id)
    """)).fetchall()
    for r in rows:
        bind.execute(text("""
            UPDATE orders SET inventory_item_id=:item_id, quantity=:qty, cost_cents=:cost_cents,
                              lot_number=:lot_number, expiration_date=:expiration_date,
                              coa_filename=:coa_filename, coa_vial_size_mg=:coa_vial_size_mg,
                              coa_purity_pct=:coa_purity_pct
            WHERE id=:order_id
        """), {"order_id": r.order_id, "item_id": r.inventory_item_id, "qty": r.quantity,
              "cost_cents": r.cost_cents, "lot_number": r.lot_number,
              "expiration_date": r.expiration_date, "coa_filename": r.coa_filename,
              "coa_vial_size_mg": r.coa_vial_size_mg, "coa_purity_pct": r.coa_purity_pct})
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.alter_column('inventory_item_id', nullable=False)
        batch_op.alter_column('quantity', nullable=False)
    op.drop_index('ix_order_items_inventory_item_id', table_name='order_items')
    op.drop_index('ix_order_items_order_id', table_name='order_items')
    op.drop_table('order_items')
```

- [ ] **Step 8: Run both tests to verify they pass**

Run: `pytest tests/test_migrations.py::test_0013_splits_orders_into_order_items tests/test_inventory.py -k "received_quantity" -v`
Expected: Both PASS.

- [ ] **Step 9: Run the full suite**

Run: `pytest`
Expected: Widespread failures — every place that constructs `Order(inventory_item_id=..., quantity=...)`
or reads `item.orders` breaks, since those no longer exist on `Order`/`InventoryItem`. This is
expected and matches this codebase's own precedent (the Order-history plan's Task 1 had the same
"expect breakage, fixed by Task 2" note) — Task 2 fixes the application code.

- [ ] **Step 10: Commit**

```bash
git add app/models.py migrations/versions/0013_multi_item_orders.py tests/test_inventory.py tests/test_migrations.py
git commit -m "feat: split Order into an order header and OrderItem lines"
```

---

### Task 2: Update existing single-item routes to the new model

**Files:**
- Modify: `app/routers/inventory.py`
- Modify: `app/routers/settings.py`
- Modify: `tests/test_inventory.py`
- Modify: `tests/test_settings.py`

**Interfaces:**
- Consumes: `Order`/`OrderItem`/`InventoryItem.order_items` from Task 1.
- Produces: `_parse_order_header_fields`, `_parse_order_line_fields`, `_arrived_quantity`,
  `_own_order_item` — Task 3 (check-in) and Task 7 (multi-item order) both reuse
  `_parse_order_header_fields`/`_parse_order_line_fields`. Task 4's template reads
  `item.order_items` (each with `.order` for header fields) instead of `item.orders`.

This task makes every existing single-item flow (Add Item's first order, an item detail page's
restock "Add order", editing that line) work against the new model with the *same user-visible
behavior as before*, except: the merged Add/Edit form's `arrival_date` field is removed entirely
(arrival is Task 3's job now), and editing an already-arrived line also exposes
`received_quantity` for a manual correction.

- [ ] **Step 1: Write the failing tests**

In `tests/test_inventory.py`, every existing test that used `arrival_date` on the single-item
add/edit forms needs updating to check in via the new check-in route instead (which doesn't exist
until Task 3) — **for this task**, adjust tests to stop asserting `arrival_date` can be set through
`/inventory`, `/inventory/{id}/orders`, or `/inventory/{id}/orders/{id}` at all (posting it should
simply be ignored, since the field is gone from `ORDER_LINE_FIELDS`/`ORDER_HEADER_FIELDS`), and add
these focused replacements:

```python
def test_add_medicine_item_creates_item_and_first_order_header_and_line(client, db):
    r = client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "tracking_site": "https://track.example/x",
        "tracking_number": "LY123", "vendor": "PeptideCo", "cost": "84.00", "tax": "5.00", "shipping": "10.00",
        "lot_number": "LOT1", "expiration_date": "2028-01-01",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        assert item.available_count == 0  # not checked in yet -- in transit
        [li] = item.order_items
        assert li.quantity == 10 and li.lot_number == "LOT1" and li.cost_cents == 8400
        assert li.received_quantity is None
        assert li.order.tracking_number == "LY123" and li.order.tax_cents == 500 and li.order.shipping_cents == 1000
        assert li.order.arrival_date is None  # arrival only happens via check-in (Task 3)


def test_editing_a_line_before_checkin_updates_header_and_line_together(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li_id = s.get(InventoryItem, item_id).order_items[0].id

    r = client.post(f"/inventory/{item_id}/orders/{li_id}", data={
        "quantity": "12", "order_date": "2026-08-01", "tracking_number": "LY999", "cost": "90.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        li = s.get(InventoryItem, item_id).order_items[0]
        assert li.quantity == 12 and li.cost_cents == 9000
        assert li.order.tracking_number == "LY999"


def test_editing_a_checked_in_line_can_correct_received_quantity(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = 9  # simulate a prior check-in that received 9 of 10
        s.commit()

    r = client.post(f"/inventory/{item_id}/orders/{li.id}", data={
        "quantity": "10", "order_date": "2026-08-01", "received_quantity": "10",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).order_items[0].received_quantity == 10
        assert s.get(InventoryItem, item_id).available_count == 10


def test_deleting_sole_line_deletes_orphaned_order_header(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        order_id = s.get(InventoryItem, item_id).order_items[0].order_id

    client.post(f"/inventory/{item_id}/delete", follow_redirects=False)
    with SessionLocal() as s:
        from app.models import Order
        assert s.get(Order, order_id) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -k "order_header_and_line or before_checkin or checked_in_line or orphaned_order" -v`
Expected: FAIL — routes still reference the old `Order`/`item.orders` shape.

- [ ] **Step 3: Import `OrderItem` and split the constants**

In `app/routers/inventory.py` line 16, add `OrderItem`:

```python
from app.models import ActiveVial, Category, DoseUnit, InventoryItem, Medium, Order, OrderItem, Sale, Share, ShareCategory, StorageLocation, User
```

Replace `ORDER_FIELDS`/`FORM_FIELDS` (lines 24-27) with:

```python
ORDER_HEADER_FIELDS = ("order_date", "shipped_date", "tracking_site", "tracking_number", "vendor", "tax", "shipping")
ORDER_LINE_FIELDS = ("quantity", "cost", "lot_number", "expiration_date", "coa_vial_size_mg", "coa_purity_pct")
FORM_FIELDS = tuple(dict.fromkeys(ITEM_FIELDS + ORDER_HEADER_FIELDS + ORDER_LINE_FIELDS + ("received_quantity",)))
```

- [ ] **Step 4: Replace `_parse_order_form` with header/line parsers**

Replace `_parse_order_form` (currently lines 144-203) with two functions:

```python
def _parse_order_header_fields(raw: dict[str, str], session: Session, uid: int, errors: dict) -> dict:
    """Parses the fields shared by every line in one order: dates, tracking, vendor, tax/shipping.
    No arrival_date here -- that's set only by check-in (see check_in_order)."""
    values: dict = {}
    values["order_date"] = _parse_date(raw["order_date"], "order_date", errors)
    if values["order_date"] is None and "order_date" not in errors:
        errors["order_date"] = "Order date is required."
    values["shipped_date"] = _parse_date(raw["shipped_date"], "shipped_date", errors)
    if (values["shipped_date"] and values["order_date"] and "order_date" not in errors
            and "shipped_date" not in errors and values["shipped_date"] < values["order_date"]):
        errors["shipped_date"] = "Shipped date can't be before the order date."

    values["tracking_site"] = raw["tracking_site"] or None
    if values["tracking_site"] and not values["tracking_site"].lower().startswith(("http://", "https://")):
        errors["tracking_site"] = "Tracking site must be a valid http(s) URL."
    values["tracking_number"] = raw["tracking_number"] or None

    vendor = resolve_vendor(session, uid, raw["vendor"])
    values["vendor_id"] = vendor.id if vendor else None
    values["vendor"] = vendor.name if vendor else None

    values["tax_cents"] = _parse_money(raw["tax"], "tax", "Tax", errors)
    values["shipping_cents"] = _parse_money(raw["shipping"], "shipping", "Shipping", errors)
    return values


def _parse_order_line_fields(raw: dict[str, str], *, prefix: str = "") -> tuple[dict, dict]:
    """Parses one order line's own fields (quantity/cost/lot/expiration/COA numbers) -- the part
    that varies per item within an order, as opposed to _parse_order_header_fields' shared fields.
    `prefix` namespaces error keys for the New Order form's repeated lines (e.g. 'lines-0-')."""
    errors: dict[str, str] = {}
    values: dict = {}

    values["quantity"] = None
    try:
        if raw["quantity"]:
            values["quantity"] = int(raw["quantity"])
        if not values["quantity"] or values["quantity"] <= 0:
            errors[f"{prefix}quantity"] = "Quantity must be a whole number greater than 0."
    except ValueError:
        errors[f"{prefix}quantity"] = "Quantity must be a whole number."

    values["cost_cents"] = _parse_money(raw["cost"], f"{prefix}cost", "Cost", errors)
    values["lot_number"] = raw["lot_number"] or None
    values["expiration_date"] = _parse_date(raw["expiration_date"], f"{prefix}expiration_date", errors)
    values["coa_vial_size_mg"] = _parse_positive_float(
        raw["coa_vial_size_mg"], f"{prefix}coa_vial_size_mg", "Lab vial size", errors)
    values["coa_purity_pct"] = None
    purity = raw["coa_purity_pct"].rstrip("%").strip()
    if purity:
        try:
            values["coa_purity_pct"] = float(purity)
            if not 0 <= values["coa_purity_pct"] <= 100:
                errors[f"{prefix}coa_purity_pct"] = "Purity must be between 0 and 100%."
        except ValueError:
            errors[f"{prefix}coa_purity_pct"] = "Purity must be a number, e.g. 99.2."

    return values, errors
```

- [ ] **Step 5: Add `_arrived_quantity` and `_own_order_item`, replace `_own_order`**

Add directly after `_bac_water_options` (after line 352):

```python
def _arrived_quantity(item: InventoryItem) -> int:
    """Sum of received_quantity across this item's checked-in order lines -- the numerator
    available_count itself uses, exposed separately for the detail page's "Arrived" stat."""
    return sum((li.received_quantity or 0) for li in item.order_items if li.order.arrival_date is not None)
```

Replace `_own_order` (currently lines 525-530) with:

```python
def _own_order_item(session: Session, item_id: int, order_item_id: int, uid: int) -> OrderItem | None:
    item = _own_item(session, item_id, uid)
    if item is None:
        return None
    li = session.get(OrderItem, order_item_id)
    return li if li is not None and li.inventory_item_id == item.id else None
```

- [ ] **Step 6: Update `item_detail`, `update_item`'s error branch, and `add_order`'s error branch
  to use `_arrived_quantity`**

In `item_detail` (line 446), `update_item`'s error branch (line 498), `add_order`'s error branch
(line 554), and `update_order`'s error branch (line 637), replace
`sum(o.quantity for o in item.orders if o.arrival_date is not None)` with `_arrived_quantity(item)`.

- [ ] **Step 7: Update `create_item`**

Replace the order-creation block in `create_item` (currently lines 464-467 and 480-481):

```python
    order_header = {}
    order_line = {}
    if category != Category.SUPPLY:
        order_header = _parse_order_header_fields(raw, session, uid, errors)
        order_line, line_errors = _parse_order_line_fields(raw)
        errors.update(line_errors)
```

and:

```python
    item = InventoryItem(**values, owner_id=uid)
    if category != Category.SUPPLY:
        order = Order(**order_header)
        session.add(order)
        li = OrderItem(**order_line, coa_filename=coa_filename)
        item.order_items.append(li)
        order.items.append(li)
    session.add(item)
    session.commit()
```

(This replaces the two-line block `item = InventoryItem(**values, owner_id=uid)` /
`if category != Category.SUPPLY: item.orders.append(Order(**order_values, coa_filename=coa_filename))`.)

- [ ] **Step 8: Update `add_order`**

Replace `add_order` (currently lines 533-560), keeping its signature and 404/COA-upload structure
identical, only changing the parsing and the final append:

```python
@router.post("/inventory/{item_id}/orders")
async def add_order(item_id: int, request: Request, session: Session = Depends(get_session),
                    uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None or item.category == Category.SUPPLY:
        raise HTTPException(404, "Inventory item not found")

    raw, coa, _ = await _read_form(request)
    errors: dict[str, str] = {}
    header_values = _parse_order_header_fields(raw, session, uid, errors)
    line_values, line_errors = _parse_order_line_fields(raw)
    errors.update(line_errors)

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
            "arrived": _arrived_quantity(item),
            **_detail_context(session, item, uid)},
            status_code=422)

    order = Order(**header_values)
    session.add(order)
    li = OrderItem(**line_values, coa_filename=coa_filename)
    item.order_items.append(li)
    order.items.append(li)
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)
```

- [ ] **Step 9: Update `update_order`**

Replace `update_order` (currently lines 614-647):

```python
@router.post("/inventory/{item_id}/orders/{order_item_id}")
async def update_order(item_id: int, order_item_id: int, request: Request,
                       session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    li = _own_order_item(session, item_id, order_item_id, uid)
    if li is None:
        raise HTTPException(404, "Order not found")

    raw, coa, remove_coa = await _read_form(request)
    errors: dict[str, str] = {}
    header_values = _parse_order_header_fields(raw, session, uid, errors)
    line_values, line_errors = _parse_order_line_fields(raw)
    errors.update(line_errors)

    received_quantity = li.received_quantity
    if li.order.arrival_date is not None:
        raw_received = str((await request.form()).get("received_quantity") or "").strip()
        if raw_received:
            try:
                received_quantity = int(raw_received)
                if not 0 <= received_quantity <= line_values["quantity"]:
                    errors["received_quantity"] = f"Must be between 0 and {line_values['quantity']}."
            except ValueError:
                errors["received_quantity"] = "Must be a whole number."

    new_coa = None
    if not errors and coa:
        try:
            new_coa = await uploads.save_coa(coa)
        except uploads.UploadError as e:
            errors["coa"] = str(e)

    if errors:
        item = li.inventory_item
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "order_errors": errors, "order_form": raw,
            "editing_order": li,
            "arrived": _arrived_quantity(item),
            **_detail_context(session, item, uid)},
            status_code=422)

    for key, value in header_values.items():
        setattr(li.order, key, value)
    for key, value in line_values.items():
        setattr(li, key, value)
    li.received_quantity = received_quantity
    if new_coa or remove_coa:
        uploads.delete_coa(li.coa_filename)
        li.coa_filename = new_coa
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)
```

Note: `request.form()` is read twice here (once inside `_read_form`, once for
`received_quantity`) — `Request.form()` caches its parse the first time, so a second `await
request.form()` returns the cached result rather than re-reading the body; this matches how
FastAPI/Starlette requests already behave elsewhere in this file and is not a new concern.

- [ ] **Step 10: Update `delete_item` for orphaned-order cleanup**

Replace `delete_item` (currently lines 513-522):

```python
@router.post("/inventory/{item_id}/delete")
def delete_item(item_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    item = _own_item(session, item_id, uid)
    if item is None:
        raise HTTPException(404, "Inventory item not found")
    order_ids = {li.order_id for li in item.order_items}
    for li in item.order_items:
        uploads.delete_coa(li.coa_filename)
    session.delete(item)
    session.flush()
    for order_id in order_ids:
        order = session.get(Order, order_id)
        if order is not None and not order.items:
            session.delete(order)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)
```

- [ ] **Step 11: Update `get_order_coa`**

Replace `get_order_coa` (currently lines 650-662):

```python
@router.get("/inventory/{item_id}/orders/{order_item_id}/coa")
def get_order_coa(item_id: int, order_item_id: int, session: Session = Depends(get_session),
                  uid: int = Depends(current_user_id)):
    item = _visible_item(session, item_id, uid)
    li = session.get(OrderItem, order_item_id) if item else None
    if item is None or li is None or li.inventory_item_id != item.id or not li.coa_filename:
        raise HTTPException(404, "No COA on file")
    path = uploads.coa_path(li.coa_filename)
    if not path.exists():
        raise HTTPException(404, "COA file is missing from disk")
    return FileResponse(path, media_type=uploads.media_type(li.coa_filename),
                        headers={"X-Content-Type-Options": "nosniff"},
                        content_disposition_type="inline")
```

- [ ] **Step 12: Update `_render_list`'s In-Transit computation and `_to_json`**

In `_render_list` (line 383-386), replace the `in_transit_orders` computation with a version
pre-grouped by order — grouping this in Python rather than in the Jinja template keeps Task 6's
template simple and avoids relying on Jinja's more obscure tuple-indexing behavior for
`selectattr` on a list of `(item, line)` pairs:

```python
    in_transit_lines = [
        (item, li) for item in medicine_items + bac_water_items for li in item.order_items
        if li.order.arrival_date is None
    ]
    in_transit_by_order: dict[int, list] = {}
    for item, li in in_transit_lines:
        in_transit_by_order.setdefault(li.order_id, []).append((item, li))
    in_transit_groups = [{"order": lines[0][1].order, "lines": lines} for lines in in_transit_by_order.values()]
```

Add `"in_transit_groups": in_transit_groups` to the returned context dict, replacing the existing
`"in_transit_orders": in_transit_orders` key.

(Template usage of `order.quantity`/`order.tracking_site` etc. in list.html changes in Task 6 to
read `li.quantity`/`li.order.tracking_site` — that rename is Task 6's job, not this one's; this
step only changes what `_render_list` puts in the context tuple.)

In `_to_json` (lines 698-728), replace the `"orders"` array:

```python
        "orders": [{
            "id": li.id, "quantity": li.quantity, "received_quantity": li.received_quantity,
            "order_date": _iso(li.order.order_date), "shipped_date": _iso(li.order.shipped_date),
            "arrival_date": _iso(li.order.arrival_date), "tracking_site": li.order.tracking_site,
            "tracking_number": li.order.tracking_number, "vendor": li.order.vendor,
            "lot_number": li.lot_number, "cost": li.cost, "tax": li.order.tax,
            "shipping": li.order.shipping, "expiration_date": _iso(li.expiration_date),
            "has_coa": bool(li.coa_filename), "coa_vial_size_mg": li.coa_vial_size_mg,
            "coa_purity_pct": li.coa_purity_pct,
        } for li in item.order_items],
```

- [ ] **Step 13: Update `settings.py`'s admin-delete COA cleanup and add orphan-order cleanup**

In `app/routers/settings.py`, replace the loop at (currently) lines 292-297:

```python
    coa_filenames = []
    order_ids = set()
    for item in session.scalars(select(InventoryItem).where(InventoryItem.owner_id == target.id)):
        for li in item.order_items:
            if li.coa_filename:
                coa_filenames.append(li.coa_filename)
            order_ids.add(li.order_id)
        session.delete(item)
    session.flush()
    from app.models import Order
    for order_id in order_ids:
        order = session.get(Order, order_id)
        if order is not None and not order.items:
            session.delete(order)
```

(Move the `from app.models import Order` import to the top of the file alongside the existing
model imports instead of inline, if `Order` isn't already imported there — check the existing
import block first.)

Add a test to `tests/test_settings.py` near the existing admin-delete-user COA test (search for
`coa_filenames` or the admin delete test to find it) asserting that after deleting a user with one
Medicine item and one order line, the underlying `Order` row is also gone:

```python
def test_admin_delete_user_removes_orphaned_orders(client, db, admin_session):
    # adapt to this file's existing admin-delete-user test setup pattern (a second user with an
    # inventory item), then after deleting that user:
    from app.models import Order
    with SessionLocal() as s:
        assert s.query(Order).count() == 0
```

(Read the existing admin-delete-user test in `tests/test_settings.py` first and follow its exact
fixture/setup convention rather than inventing a new one — the sketch above is the assertion to
add, not a literal drop-in replacement for that file's own setup style.)

- [ ] **Step 14: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py tests/test_settings.py -v`
Expected: All PASS. Some pre-existing tests from Tasks 1-9 of the earlier Order-history plan may
still reference `arrival_date` on the merged add/edit forms or `item.orders` directly — fix each
one to either drop the `arrival_date` posting (since it's now a no-op field, simply remove it from
the test's POST data) or check in via a raw DB write (`li.order.arrival_date = ...;
li.received_quantity = ...`) instead of posting a form field, and rename `item.orders` /
`Order(inventory_item_id=...)` references to `item.order_items` / the new two-step
`Order`+`OrderItem` construction shown in this task's steps.

- [ ] **Step 15: Run the full suite**

Run: `pytest`
Expected: All pass (Tasks 3-9 still need their own new code, but nothing outside this task's scope
should be broken now — `calculator.py` and `app/routers/protocols.py` never touch `.orders`
directly, only the derived `available_count` property, so they need no changes).

- [ ] **Step 16: Commit**

```bash
git add app/routers/inventory.py app/routers/settings.py tests/test_inventory.py tests/test_settings.py
git commit -m "feat: adapt single-item order routes to the header/line model"
```

---

### Task 3: Check-in flow

**Files:**
- Modify: `app/routers/inventory.py`
- Modify: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `Order`/`OrderItem`, `_own_item`, `_detail_context`, `_parse_date` from Tasks 1-2.
- Produces: `POST /inventory/{item_id}/orders/{order_id}/check-in` route, `checkin_errors`/
  `checkin_form`/`checkin_order` template keys — Task 4's template reads these, Task 6's In-Transit
  section links to this route.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_inventory.py`, in a new section:

```python
# ---------------------------------------------------------------- Multi-item orders: Task 3 (check-in)


def test_check_in_order_sets_arrival_and_received_quantity(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "10",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.order_items[0].order.arrival_date == date(2026, 8, 10)
        assert item.order_items[0].received_quantity == 10
        assert item.available_count == 10


def test_check_in_order_with_short_receipt_does_not_inflate_available_count(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "8",
        f"received_note_{li_id}": "2 vials cracked in transit",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert item.available_count == 8
        assert item.order_items[0].received_note == "2 vials cracked in transit"


def test_check_in_rejects_received_quantity_above_ordered(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "11",
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.get(InventoryItem, item_id).order_items[0].order.arrival_date is None


def test_check_in_rejects_future_arrival_date(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2099-01-01", f"received_quantity_{li_id}": "10",
    })
    assert r.status_code == 422


def test_check_in_requires_ownership(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        order_id = s.get(InventoryItem, item_id).order_items[0].order_id
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "CheckinOther", "password": "CheckinOther1!", "confirm": "CheckinOther1!"})
    r = other.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={"arrival_date": "2026-08-10"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -k "check_in" -v`
Expected: FAIL — 404 (route doesn't exist).

- [ ] **Step 3: Extend `_detail_context` with `checkin_data`**

The check-in dialog needs to build its line rows (item name, ordered quantity, a received-quantity
input) for whichever in-transit order the user clicks "Check in" on — and the item detail page can
be opened directly with no prior click, so those rows can never rely on a single
server-picked "current" order the way `order_form`/`sale_form` do for their own dialogs. Instead,
`_detail_context` gains a JSON-ready dict of every in-transit order this item is part of, keyed by
order id, each holding every line in that order (including sibling items in the same shipment) —
Task 5's JS builds the dialog from this rather than from server-rendered rows.

In `app/routers/inventory.py`, update `_detail_context` (defined earlier in the codebase, modify
in place) to add one more key to its returned dict:

```python
        "checkin_data": {
            li.order_id: [
                {"id": sib.id, "quantity": sib.quantity, "item_name": sib.inventory_item.name}
                for sib in li.order.items
            ]
            for li in item.order_items if li.order.arrival_date is None
        },
```

- [ ] **Step 4: Add the check-in route**

Add directly after `_own_order_item` (from Task 2's Step 5):

```python
def _own_order_for_item(session: Session, item_id: int, order_id: int, uid: int) -> Order | None:
    """The Order if `item_id` is one of the caller's own items with a line in it -- check-in acts
    on every line in the order at once, not just this item's."""
    item = _own_item(session, item_id, uid)
    if item is None:
        return None
    order = session.get(Order, order_id)
    if order is None or not any(li.inventory_item_id == item.id for li in order.items):
        return None
    return order


@router.post("/inventory/{item_id}/orders/{order_id}/check-in")
async def check_in_order(item_id: int, order_id: int, request: Request,
                         session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    order = _own_order_for_item(session, item_id, order_id, uid)
    if order is None:
        raise HTTPException(404, "Order not found")

    form = await request.form()
    raw = {"arrival_date": str(form.get("arrival_date") or "").strip()}
    for li in order.items:
        raw[f"received_quantity_{li.id}"] = str(form.get(f"received_quantity_{li.id}") or "").strip()
        raw[f"received_note_{li.id}"] = str(form.get(f"received_note_{li.id}") or "").strip()

    errors: dict[str, str] = {}
    arrival_date = _parse_date(raw["arrival_date"], "arrival_date", errors)
    if arrival_date is None and "arrival_date" not in errors:
        errors["arrival_date"] = "Arrival date is required."
    elif arrival_date and arrival_date > date.today():
        errors["arrival_date"] = "Arrival date can't be in the future."
    elif arrival_date and order.shipped_date and arrival_date < order.shipped_date:
        errors["arrival_date"] = "Arrival date can't be before the shipped date."
    elif arrival_date and arrival_date < order.order_date:
        errors["arrival_date"] = "Arrival date can't be before the order date."

    received: dict[int, tuple[int, str | None]] = {}
    for li in order.items:
        field = f"received_quantity_{li.id}"
        raw_qty = raw[field]
        try:
            qty = int(raw_qty) if raw_qty else li.quantity
        except ValueError:
            errors[field] = "Must be a whole number."
            continue
        if not 0 <= qty <= li.quantity:
            errors[field] = f"Must be between 0 and {li.quantity}."
            continue
        received[li.id] = (qty, raw[f"received_note_{li.id}"] or None)

    if errors:
        item = _own_item(session, item_id, uid)
        return templates.TemplateResponse(
            request, "inventory/detail.html",
            {"item": item, "is_owner": True, "checkin_errors": errors, "checkin_form": raw,
            "checkin_order": order,
            "arrived": _arrived_quantity(item),
            **_detail_context(session, item, uid)},
            status_code=422)

    order.arrival_date = arrival_date
    for li in order.items:
        qty, note = received[li.id]
        li.received_quantity = qty
        li.received_note = note
    session.commit()
    return RedirectResponse(f"/inventory/{item_id}", status_code=303)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py -k "check_in" -v`
Expected: All PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/inventory.py tests/test_inventory.py
git commit -m "feat: add order check-in route (arrival + received-quantity confirmation)"
```

---

### Task 4: Item detail page — Order History, Edit dialog, check-in dialog

**Files:**
- Modify: `app/templates/inventory/detail.html`
- Modify: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `item.order_items`, `checkin_errors`/`checkin_form`/`checkin_order`,
  `order_errors`/`order_form`/`editing_order` (now an `OrderItem`) from Tasks 1-3.
- Produces: `data-action="check-in"`, `#checkin-dialog` — Task 5's JS wires these up.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_inventory.py`:

```python
# ---------------------------------------------------------------- Multi-item orders: Task 4 (template)


def test_order_history_shows_lines_and_checkin_button(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="check-in"' in t
    assert 'id="checkin-dialog"' in t
    assert "10" in t  # quantity shown


def test_order_history_hides_checkin_button_once_arrived(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = 10
        s.commit()
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert 'data-action="check-in" data-order-id="' not in t


def test_checkin_validation_error_reopens_dialog(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        order_id, li_id = li.order_id, li.id

    r = client.post(f"/inventory/{item_id}/orders/{order_id}/check-in", data={
        "arrival_date": "2026-08-10", f"received_quantity_{li_id}": "11",
    })
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert "data-open-on-load" in t
    assert 'id="checkin-dialog"' in t
    assert "Must be between 0 and 10" in t
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -k "order_history_shows or hides_checkin or checkin_validation" -v`
Expected: FAIL — none of these markers exist in `detail.html` yet.

- [ ] **Step 3: Rewrite the Order History section**

In `app/templates/inventory/detail.html`, replace the Order History `<section>` (currently lines
148-183) — this includes the "also in this shipment" note (a line's siblings sharing the same
`order_id`) directly, rather than adding it as a follow-up:

```html
<section aria-labelledby="orders-heading">
  <h2 id="orders-heading" class="section-title">Order history</h2>
  {% if is_owner %}
  <button type="button" class="btn btn-ghost" data-action="add-order">Add order</button>
  {% endif %}
  {% if item.order_items %}
  <div class="table-wrap">
  <table class="inv-table">
    <thead><tr><th>Quantity</th><th>Received</th><th>Ordered</th><th>Shipped</th><th>Arrived</th><th>Expiration</th><th>Tracking</th><th>Vendor</th><th>Lot #</th><th>Cost</th><th>Tax</th><th>Shipping</th><th>COA</th>{% if is_owner %}<th></th>{% endif %}</tr></thead>
    <tbody>
      {% for li in item.order_items %}
      {% set siblings = item.order_items | selectattr('order_id', 'equalto', li.order_id) | rejectattr('id', 'equalto', li.id) | list %}
      <tr>
        <td>
          {{ li.quantity }}
          {% if siblings %}
          <div class="muted small">+ {{ siblings | length }} other item{{ '' if siblings | length == 1 else 's' }} in this shipment</div>
          {% endif %}
        </td>
        <td>{{ li.received_quantity if li.received_quantity is not none else '—' }}</td>
        <td>{{ li.order.order_date | shortdate }}</td>
        <td>{{ li.order.shipped_date | shortdate or '—' }}</td>
        <td>{{ li.order.arrival_date | shortdate or '—' }}</td>
        <td>{{ li.expiration_date | shortdate or '—' }}</td>
        <td>
          {% if li.order.tracking_site %}<a href="{{ li.order.tracking_site }}" target="_blank" rel="noopener">{{ li.order.tracking_number or 'Track' }}</a>{% else %}{{ li.order.tracking_number or '—' }}{% endif %}
        </td>
        <td>{{ li.order.vendor or '—' }}</td>
        <td>{{ li.lot_number or '—' }}</td>
        <td>{{ li.cost | money or '—' }}</td>
        <td>{{ li.order.tax | money or '—' }}</td>
        <td>{{ li.order.shipping | money or '—' }}</td>
        <td>{% if li.coa_filename %}<a href="/inventory/{{ item.id }}/orders/{{ li.id }}/coa" target="_blank" rel="noopener">View</a>{% else %}—{% endif %}</td>
        {% if is_owner %}
        <td>
          {% if li.order.arrival_date is none %}
          <button type="button" class="btn btn-ghost" data-action="check-in" data-order-id="{{ li.order_id }}">Check in</button>
          {% endif %}
          <button type="button" class="btn btn-ghost" data-action="edit-order" data-order-id="{{ li.id }}" data-order='{{ {"quantity": li.quantity, "order_date": li.order.order_date.isoformat() if li.order.order_date else "", "shipped_date": li.order.shipped_date.isoformat() if li.order.shipped_date else "", "tracking_site": li.order.tracking_site or "", "tracking_number": li.order.tracking_number or "", "vendor": li.order.vendor or "", "lot_number": li.lot_number or "", "cost": "" if li.cost is none else ("%.2f" % li.cost), "tax": "" if li.order.tax is none else ("%.2f" % li.order.tax), "shipping": "" if li.order.shipping is none else ("%.2f" % li.order.shipping), "expiration_date": li.expiration_date.isoformat() if li.expiration_date else "", "coa_vial_size_mg": "" if li.coa_vial_size_mg is none else ("%g" % li.coa_vial_size_mg), "coa_purity_pct": "" if li.coa_purity_pct is none else ("%g" % li.coa_purity_pct), "received_quantity": "" if li.received_quantity is none else li.received_quantity|string, "arrived": li.order.arrival_date is not none} | tojson }}'>Edit</button>
        </td>
        {% endif %}
      </tr>
      {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p class="muted">No orders yet.</p>
  {% endif %}
</section>
```

- [ ] **Step 4: Update the Edit-order dialog for the new field ownership**

Replace the `#order-dialog` dialog (currently lines 188-220) — the `arrival_date` field is removed
(arrival only happens via check-in) and a `received_quantity` field is added, shown only once the
line's order has arrived:

```html
{% set of = order_form or {} %}
{% macro oerr(field) %}{% if order_errors and order_errors[field] %}<small class="error">{{ order_errors[field] }}</small>{% endif %}{% endmacro %}
{% macro ocls(field, extra='') %}class="field {{ extra }} {{ 'has-error' if order_errors and order_errors[field] }}"{% endmacro %}
<dialog id="order-dialog" class="dialog" {% if order_errors %}data-open-on-load{% endif %}>
  <form method="post" class="item-form" enctype="multipart/form-data" novalidate
        action="{{ '/inventory/%d/orders/%d' % (item.id, editing_order.id) if editing_order else '/inventory/%d/orders' % item.id }}">
    <header class="dialog-head">
      <h2 data-title>{{ 'Edit order' if editing_order else 'Add order' }}</h2>
      <button type="button" class="btn btn-ghost btn-icon" data-action="close-order" aria-label="Close">×</button>
    </header>
    {% if order_errors %}
    <div class="alert" role="alert">Please fix the highlighted fields.</div>
    {% endif %}
    <div class="grid">
      <label {{ ocls('quantity') }}><span>Quantity</span><input name="quantity" type="number" min="1" step="1" value="{{ of.quantity }}">{{ oerr('quantity') }}</label>
      <label {{ ocls('order_date') }}><span>Order date</span><input name="order_date" type="date" value="{{ of.order_date }}">{{ oerr('order_date') }}</label>
      <label {{ ocls('shipped_date') }}><span>Shipped date</span><input name="shipped_date" type="date" value="{{ of.shipped_date }}">{{ oerr('shipped_date') }}</label>
      <label {{ ocls('tracking_site') }}><span>Tracking site (URL)</span><input name="tracking_site" type="url" value="{{ of.tracking_site }}">{{ oerr('tracking_site') }}</label>
      <label {{ ocls('tracking_number') }}><span>Tracking number</span><input name="tracking_number" value="{{ of.tracking_number }}">{{ oerr('tracking_number') }}</label>
      <label {{ ocls('vendor') }}><span>Vendor</span><input name="vendor" value="{{ of.vendor }}">{{ oerr('vendor') }}</label>
      <label {{ ocls('lot_number') }}><span>Lot / Batch #</span><input name="lot_number" value="{{ of.lot_number }}">{{ oerr('lot_number') }}</label>
      <label {{ ocls('cost') }}><span>Cost ($)</span><input name="cost" type="number" min="0" step="0.01" value="{{ of.cost }}">{{ oerr('cost') }}</label>
      <label {{ ocls('tax') }}><span>Tax ($)</span><input name="tax" type="number" min="0" step="0.01" value="{{ of.tax }}">{{ oerr('tax') }}</label>
      <label {{ ocls('shipping') }}><span>Shipping ($)</span><input name="shipping" type="number" min="0" step="0.01" value="{{ of.shipping }}">{{ oerr('shipping') }}</label>
      <label {{ ocls('expiration_date') }}><span>Expiration date</span><input name="expiration_date" type="date" value="{{ of.expiration_date }}">{{ oerr('expiration_date') }}</label>
      <label {{ ocls('coa', 'span-2') }}><span>COA (photo or PDF)</span><input name="coa" type="file" accept="image/*,.heic,.heif,application/pdf">{{ oerr('coa') }}</label>
      <label {{ ocls('coa_vial_size_mg') }}><span>Lab vial size (mg)</span><input name="coa_vial_size_mg" type="number" min="0" step="any" value="{{ of.coa_vial_size_mg }}">{{ oerr('coa_vial_size_mg') }}</label>
      <label {{ ocls('coa_purity_pct') }}><span>Lab purity (%)</span><input name="coa_purity_pct" type="number" min="0" max="100" step="any" value="{{ of.coa_purity_pct }}">{{ oerr('coa_purity_pct') }}</label>
      <label {{ ocls('received_quantity') }} data-field="received_quantity" {% if not of.arrived %}hidden{% endif %}>
        <span>Received quantity</span>
        <input name="received_quantity" type="number" min="0" step="1" value="{{ of.received_quantity }}">
        {{ oerr('received_quantity') }}
      </label>
    </div>
    <footer class="dialog-foot">
      <button type="button" class="btn" data-action="close-order">Cancel</button>
      <button type="submit" class="btn btn-primary">Save</button>
    </footer>
  </form>
</dialog>
```

- [ ] **Step 5: Add the check-in dialog**

The dialog's line rows are never server-rendered from a single "current" order the way
`order_form`/`sale_form` prefill their own dialogs — the item detail page can be opened directly
with no order pre-selected, and an item can have more than one order still in transit, so there is
no single order to render rows for on a plain GET. Instead the dialog starts with an empty rows
container, and Task 5's JS builds the rows for whichever order was clicked from the always-present
`checkin_data` JSON (Step 3). A validation-error re-render still needs to reopen showing exactly
what was submitted (including the error text and the values the user typed) — that's carried
through a second JSON blob, `checkin_error_data`, that Task 5's JS also reads.

Add directly after the `#order-dialog`'s closing `</dialog>` from Step 4:

```html
<dialog id="checkin-dialog" class="dialog" {% if checkin_errors %}data-open-on-load{% endif %}>
  <form method="post" class="item-form" novalidate action="#">
    <header class="dialog-head">
      <h2 data-title>Check in order</h2>
      <button type="button" class="btn btn-ghost btn-icon" data-action="close-checkin" aria-label="Close">×</button>
    </header>
    <div class="alert" role="alert" data-checkin-alert hidden>Please fix the highlighted fields.</div>
    <div class="grid">
      <label class="field" data-field="arrival_date">
        <span>Arrival date</span>
        <input name="arrival_date" type="date" max="{{ today_iso }}">
        <small class="error" data-error="arrival_date" hidden></small>
      </label>
    </div>
    <div data-checkin-lines></div>
    <footer class="dialog-foot">
      <button type="button" class="btn" data-action="close-checkin">Cancel</button>
      <button type="submit" class="btn btn-primary">Save</button>
    </footer>
  </form>
</dialog>
<script type="application/json" id="checkin-data">{{ checkin_data | tojson }}</script>
<script type="application/json" id="checkin-error-data">{{ {"order_id": checkin_order.id if checkin_order else None, "errors": checkin_errors or {}, "form": checkin_form or {}} | tojson }}</script>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py -k "order_history_shows or hides_checkin or checkin_validation" -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass — this includes re-checking the Task 2 tests that asserted on Edit-order dialog
HTML shape, since the dialog's field set changed (`arrival_date` removed, `received_quantity`
added). If any fail because they still expect the old field set, fix the test's assertions, not
the template.

- [ ] **Step 8: Commit**

```bash
git add app/templates/inventory/detail.html tests/test_inventory.py
git commit -m "feat: show order lines, shipment siblings, and check-in on the item detail page"
```

---

### Task 5: JS — check-in dialog and Edit-order dialog adjustments

**Files:**
- Modify: `app/static/js/inventory.js`

No new automated tests (no browser-level JS harness in this app, matching the established
precedent from the Sold-flow plan's Task 5) — verify manually per Step 3.

**Interfaces:**
- Consumes: `#checkin-dialog`, `#checkin-data`, `#checkin-error-data`, `[data-action="check-in"]`,
  `[data-action="close-checkin"]`, `data-open-on-load` from Task 4; the existing `#order-dialog`
  and its `data-order` JSON payload (now including `received_quantity`/`arrived`).

- [ ] **Step 1: Update the order-dialog IIFE's field list, and add the checkin-dialog IIFE**

In `app/static/js/inventory.js`, find the `item detail: order dialog` IIFE (the one guarding on
`document.getElementById("order-dialog")`). Add `"received_quantity"` to its `orderFields` array,
and in the `edit-order` click handler, after setting each field's value, also toggle the
`received_quantity` field group's visibility based on `order.arrived`:

```js
  document.querySelectorAll('[data-action="edit-order"]').forEach((btn) => btn.addEventListener("click", () => {
    form.reset();
    const order = JSON.parse(btn.dataset.order);
    for (const f of orderFields) {
      if (form.elements[f]) form.elements[f].value = order[f] ?? "";
    }
    const receivedGroup = form.querySelector('[data-field="received_quantity"]');
    if (receivedGroup) receivedGroup.hidden = !order.arrived;
    form.action = `${window.location.pathname}/orders/${btn.dataset.orderId}`;
    dialog.querySelector("[data-title]").textContent = "Edit order";
    dialog.showModal();
  }));
```

(This replaces the existing `edit-order` click handler in that IIFE — the `add-order` handler and
everything else in the IIFE is unchanged.)

Add a new IIFE at the end of the file for the check-in dialog. Unlike every other dialog in this
file, this one's rows are never server-rendered for a "current" item — it builds them fresh from
the `checkin-data` JSON blob (Task 4 Step 3/5) every time it opens, keyed by whichever order's
"Check in" button was clicked:

```js

// ---------------------------------------------------------------- item detail: check-in dialog
(() => {
  const dialog = document.getElementById("checkin-dialog");
  if (!dialog) return;  // Supply items, or a non-owner viewer, have no Order History section
  const form = dialog.querySelector("form");
  const linesContainer = dialog.querySelector("[data-checkin-lines]");
  const alertBox = dialog.querySelector("[data-checkin-alert]");
  const arrivalInput = form.elements.arrival_date;
  const arrivalError = dialog.querySelector('[data-error="arrival_date"]');
  const checkinData = JSON.parse(document.getElementById("checkin-data").textContent);

  function clearErrors() {
    alertBox.hidden = true;
    arrivalError.hidden = true;
    arrivalError.textContent = "";
    dialog.querySelectorAll(".has-error").forEach((el) => el.classList.remove("has-error"));
    dialog.querySelectorAll("[data-error]").forEach((el) => { el.hidden = true; el.textContent = ""; });
  }

  function buildLines(orderId, prefill) {
    linesContainer.innerHTML = "";
    const lines = checkinData[orderId] || [];
    for (const line of lines) {
      const row = document.createElement("div");
      row.className = "grid";
      const posted = prefill && prefill.form ? prefill.form[`received_quantity_${line.id}`] : null;
      const postedNote = prefill && prefill.form ? prefill.form[`received_note_${line.id}`] : null;
      const err = prefill && prefill.errors ? prefill.errors[`received_quantity_${line.id}`] : null;
      row.innerHTML = `
        <div class="field span-2"><span>${line.item_name} (ordered ${line.quantity})</span></div>
        <label class="field ${err ? "has-error" : ""}">
          <span>Received</span>
          <input name="received_quantity_${line.id}" type="number" min="0" max="${line.quantity}" step="1"
                 value="${posted != null && posted !== "" ? posted : line.quantity}">
          ${err ? `<small class="error">${err}</small>` : ""}
        </label>
        <label class="field">
          <span>Note (if short or damaged)</span>
          <input name="received_note_${line.id}" maxlength="300" value="${postedNote || ""}">
        </label>
      `;
      linesContainer.appendChild(row);
    }
  }

  document.querySelectorAll('[data-action="check-in"]').forEach((btn) => btn.addEventListener("click", () => {
    clearErrors();
    form.reset();
    const orderId = btn.dataset.orderId;
    form.action = `${window.location.pathname}/orders/${orderId}/check-in`;
    buildLines(orderId, null);
    dialog.showModal();
  }));
  dialog.querySelectorAll('[data-action="close-checkin"]').forEach((btn) =>
    btn.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });

  // Server re-rendered the page after a check-in validation error: rebuild this order's rows from
  // checkin-data (same as a fresh open) then overlay what was actually posted and each field's
  // error, using checkin-error-data -- there is no server-rendered row to just reopen as-is here.
  if (dialog.hasAttribute("data-open-on-load")) {
    const errorData = JSON.parse(document.getElementById("checkin-error-data").textContent);
    const orderId = errorData.order_id;
    form.action = `${window.location.pathname}/orders/${orderId}/check-in`;
    arrivalInput.value = errorData.form.arrival_date || "";
    if (errorData.errors.arrival_date) {
      alertBox.hidden = false;
      arrivalError.hidden = false;
      arrivalError.textContent = errorData.errors.arrival_date;
    }
    buildLines(orderId, errorData);
    if (Object.keys(errorData.errors).length) alertBox.hidden = false;
    dialog.showModal();
  }
})();
```

- [ ] **Step 2: Run the full suite**

Run: `pytest`
Expected: All pass (this task touches no Python; confirms nothing regressed).

- [ ] **Step 3: Manual verification**

Start the app and in a browser: create a Medicine item with a first order (in transit). On its
detail page, click "Check in" — the dialog opens with an Arrival date field and one line row
showing "ordered 10" with a Received field pre-filled to 10. Lower it to 8, add a note, submit —
confirm the redirect back to the detail page shows Available reduced to 8 (not 10), the Order
History row shows "Received: 8", and the "Check in" button is gone (order has arrived). Then open
"Edit" on that same line — confirm a "Received quantity" field is now visible and editable,
separate from the original "Quantity" field. Confirm a Supply item's page has no check-in button
(it never has Order History at all).

- [ ] **Step 4: Commit**

```bash
git add app/static/js/inventory.js
git commit -m "feat: wire up the check-in dialog and received-quantity field on Edit order"
```

---

### Task 6: Inventory list — In-Transit grouped by order

**Files:**
- Modify: `app/templates/inventory/list.html`
- Modify: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `in_transit_groups` (a list of `{"order": Order, "lines": [(item, li), ...]}` dicts,
  per Task 2's Step 12) from `_render_list`.
- Produces: `data-action="check-in"` buttons on the list page too (same check-in dialog markup,
  duplicated onto list.html the same way the order/sale dialogs already only exist on
  detail.html — **not** duplicated here; instead the list page's "Check in" links straight to the
  item's detail page, where the existing check-in dialog (Task 4/5) already lives. This avoids
  building a second copy of the check-in dialog and its JS for a page that already links to the
  page that has it.)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_inventory.py`:

```python
# ---------------------------------------------------------------- Multi-item orders: Task 6 (In-Transit grouping)


def test_in_transit_groups_multiple_items_from_one_order(client, db):
    from app.models import Order, OrderItem

    with SessionLocal() as s:
        me_id = s.scalar(select(User.id).where(User.username_key == "tester"))
        med = InventoryItem(owner_id=me_id, name="Retatrutide", category=Category.MEDICINE,
                            medium=Medium.LYOPHILIZED, vial_size_mg=10)
        bac = InventoryItem(owner_id=me_id, name="Bacteriostatic Water", category=Category.BAC_WATER)
        s.add_all([med, bac])
        s.flush()
        order = Order(order_date=date(2026, 9, 1), tracking_number="SHARED123")
        s.add(order)
        s.flush()
        order.items.append(OrderItem(inventory_item_id=med.id, quantity=5))
        order.items.append(OrderItem(inventory_item_id=bac.id, quantity=10))
        s.commit()

    t = html.unescape(client.get("/inventory").text)
    assert "SHARED123" in t
    assert t.count("SHARED123") == 1  # one row for the whole order, not one per line
    assert "Retatrutide" in t and "Bacteriostatic Water" in t
```

(`User` must already be imported in `tests/test_inventory.py` — check the top of the file first;
if not, add `from app.models import User` alongside the other model imports. The `"tester"`
username key matches this test file's session-scoped `client` fixture user, per `conftest.py`'s
`TEST_USER = "Tester"`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_inventory.py::test_in_transit_groups_multiple_items_from_one_order -v`
Expected: FAIL — today's In-Transit table has one row per line with its own tracking column
repeated, so `t.count("SHARED123")` is 2, not 1.

- [ ] **Step 3: Regroup the In-Transit table**

In `app/templates/inventory/list.html`, replace the In-Transit `<section>` (currently lines
119-141):

```html
<section id="in-transit" aria-labelledby="in-transit-heading">
  <h2 id="in-transit-heading" class="section-title">In-transit</h2>
  {% if in_transit_groups %}
  <div class="table-wrap">
    <table class="inv-table">
      <thead><tr><th>Items</th><th>Ordered</th><th>Shipped</th><th>Tracking</th><th></th></tr></thead>
      <tbody>
        {% for group in in_transit_groups %}
        <tr>
          <td>
            {% for item, li in group.lines %}
            <div><a href="/inventory/{{ item.id }}">{{ item.name }}</a> &times; {{ li.quantity }}</div>
            {% endfor %}
          </td>
          <td>{{ group.order.order_date | shortdate }}</td>
          <td>{{ group.order.shipped_date | shortdate or '—' }}</td>
          <td>{% if group.order.tracking_site %}<a href="{{ group.order.tracking_site }}" target="_blank" rel="noopener">{{ group.order.tracking_number or 'Track' }}</a>{% else %}{{ group.order.tracking_number or '—' }}{% endif %}</td>
          <td><a href="/inventory/{{ group.lines[0][0].id }}" class="btn btn-ghost">Check in</a></td>
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

(The "Check in" action here is a plain link to the first item's detail page, per this task's
Interfaces note — clicking it takes the user to the page with the actual check-in dialog rather
than duplicating that dialog here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_inventory.py::test_in_transit_groups_multiple_items_from_one_order -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add app/templates/inventory/list.html tests/test_inventory.py
git commit -m "feat: group the In-Transit section by order instead of by line"
```

---

### Task 7: New Order form — backend parsing and route

**Files:**
- Modify: `app/routers/inventory.py`
- Modify: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `_parse_order_header_fields`, `_parse_order_line_fields`, `_parse_item_fields`,
  `_own_item` from Tasks 1-2.
- Produces: `POST /inventory/orders` route, `_group_lines` helper, `order_form`/`order_line_form`/
  `order_errors` (list-page variants) template keys — Task 8's template reads these.

This task reuses the app's existing repeated-row form convention from
`app/protocols/forms.py` (`items-{i}-field`, parsed with a regex and grouped by index) rather than
inventing a new one — read `app/protocols/forms.py`'s `_ITEM_KEY`/parsing loop first for the
established pattern before writing this task's `_group_lines`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_inventory.py`:

```python
# ---------------------------------------------------------------- Multi-item orders: Task 7 (New Order route)


def test_new_order_creates_one_order_with_two_new_item_lines(client, db):
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01", "tracking_number": "MULTI1", "shipping": "10.00", "tax": "5.00",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-medium": "Lyophilized", "lines-0-vial_size_mg": "10", "lines-0-quantity": "5",
        "lines-0-cost": "80.00",
        "lines-1-mode": "new", "lines-1-category": "BAC Water", "lines-1-name": "Bacteriostatic Water",
        "lines-1-quantity": "10", "lines-1-cost": "15.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        med = s.scalar(select(InventoryItem).where(InventoryItem.name == "Retatrutide"))
        bac = s.scalar(select(InventoryItem).where(InventoryItem.name == "Bacteriostatic Water"))
        assert med.order_items[0].order_id == bac.order_items[0].order_id  # same shared order
        assert med.order_items[0].order.tracking_number == "MULTI1"
        assert med.order_items[0].quantity == 5 and bac.order_items[0].quantity == 10


def test_new_order_can_restock_an_existing_item_alongside_a_new_one(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "5", "order_date": "2026-08-01",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "existing", "lines-0-item_id": str(item_id), "lines-0-quantity": "5", "lines-0-cost": "80.00",
        "lines-1-mode": "new", "lines-1-category": "BAC Water", "lines-1-name": "Bacteriostatic Water",
        "lines-1-quantity": "10", "lines-1-cost": "15.00",
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.get(InventoryItem, item_id)
        assert len(item.order_items) == 2  # the original solo order, plus this new shared one


def test_new_order_requires_at_least_one_line(client, db):
    r = client.post("/inventory/orders", data={"order_date": "2026-09-01"})
    assert r.status_code == 422
    with SessionLocal() as s:
        from app.models import Order
        assert s.query(Order).count() == 0


def test_new_order_new_item_line_validates_category_fields(client, db):
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "new", "lines-0-category": "Medicine", "lines-0-name": "Retatrutide",
        "lines-0-quantity": "5",  # no medium -- Medicine requires it
    })
    assert r.status_code == 422
    with SessionLocal() as s:
        assert s.query(InventoryItem).count() == 0


def test_new_order_rejects_supply_category_for_new_lines(client, db):
    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "new", "lines-0-category": "Supply", "lines-0-name": "Syringes",
        "lines-0-quantity": "5",
    })
    assert r.status_code == 422


def test_new_order_rejects_existing_item_not_owned_by_caller(client, db):
    other = TestClient(app, follow_redirects=False)
    other.post("/notice", data={"understand": "1"})
    other.post("/register", data={"username": "NewOrderOther", "password": "NewOrderOther1!", "confirm": "NewOrderOther1!"})
    other.post("/inventory", data={
        "name": "Not Yours", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "5", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        their_item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Not Yours"))

    r = client.post("/inventory/orders", data={
        "order_date": "2026-09-01",
        "lines-0-mode": "existing", "lines-0-item_id": str(their_item_id), "lines-0-quantity": "5",
    })
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -k "test_new_order" -v`
Expected: FAIL — `404 Not Found` (route doesn't exist).

- [ ] **Step 3: Add `_group_lines` and the new-item-line resolver**

Add near the top of `app/routers/inventory.py`, after the existing imports (add `import re` to the
imports at the top of the file if not already present):

```python
_LINE_KEY = re.compile(r"^lines-(\d+)-(\w+)$")


def _group_lines(form: dict[str, list[str]]) -> dict[int, dict[str, str]]:
    """Groups a New Order form's repeated 'lines-{i}-field' keys by index, mirroring
    app/protocols/forms.py's identical 'items-{i}-field' convention."""
    lines: dict[int, dict[str, str]] = {}
    for key, values in form.items():
        m = _LINE_KEY.match(key)
        if m:
            i, field = int(m.group(1)), m.group(2)
            lines.setdefault(i, {})[field] = values[0] if values else ""
    return dict(sorted(lines.items()))


_NEW_LINE_ITEM_FIELDS = ("mode", "item_id", "name", "category", "medium", "vial_size_mg",
                        "vial_size_unit", "volume_ml", "units_per_package", "storage", "notes",
                        "quantity", "cost", "lot_number", "expiration_date", "coa_vial_size_mg",
                        "coa_purity_pct")
```

Note `"notes"` is required here even though the New Order form never collects it per line:
`_parse_item_fields` unconditionally reads `raw["notes"]` for every category (see its current
body) — omitting the key from `line_raw` would `KeyError` on every New Order submission. A new
item created via this form simply gets `notes=None`; nothing stops the owner from adding notes
later through the item's own Edit dialog.

- [ ] **Step 4: Add the New Order route**

Add near the end of the HTML routes section, after `update_order` and before `get_order_coa`:

```python
async def _read_new_order_form(request: Request) -> tuple[dict[str, list[str]], dict[int, UploadFile]]:
    form = await request.form()
    text_form: dict[str, list[str]] = {}
    coa_files: dict[int, UploadFile] = {}
    for key in form.keys():
        m = _LINE_KEY.match(key)
        if m and m.group(2) == "coa":
            f = form.get(key)
            if isinstance(f, UploadFile) and f.filename:
                coa_files[int(m.group(1))] = f
            continue
        text_form[key] = [str(v) for v in form.getlist(key)]
    return text_form, coa_files


@router.post("/inventory/orders")
async def create_multi_item_order(request: Request, session: Session = Depends(get_session),
                                  uid: int = Depends(current_user_id)):
    form, coa_files = await _read_new_order_form(request)
    header_raw = {f: (form.get(f) or [""])[0] for f in ORDER_HEADER_FIELDS}
    errors: dict[str, str] = {}
    header_values = _parse_order_header_fields(header_raw, session, uid, errors)

    line_groups = _group_lines(form)
    if not line_groups:
        errors["lines"] = "Add at least one item."

    parsed_lines = []
    for i, raw_group in line_groups.items():
        line_raw = {f: raw_group.get(f, "") for f in _NEW_LINE_ITEM_FIELDS}
        prefix = f"lines-{i}-"
        line_errors: dict[str, str] = {}
        existing_item = None
        new_item_values = None

        if line_raw["mode"] == "existing":
            existing_item = None
            if line_raw["item_id"].isdigit():
                existing_item = _own_item(session, int(line_raw["item_id"]), uid)
            if existing_item is None or existing_item.category == Category.SUPPLY:
                line_errors[f"{prefix}item_id"] = "Select an item you already track."
        else:
            category = _parse_choice(Category, line_raw["category"], Category.MEDICINE, f"{prefix}category", line_errors)
            if category == Category.SUPPLY:
                line_errors[f"{prefix}category"] = "New order lines can only be Medicine or BAC Water."
            else:
                new_item_values, item_errs = _parse_item_fields(line_raw, session, uid, category)
                for f, msg in item_errs.items():
                    line_errors[f"{prefix}{f}"] = msg

        line_values, line_val_errors = _parse_order_line_fields(line_raw, prefix=prefix)
        line_errors.update(line_val_errors)

        errors.update(line_errors)
        parsed_lines.append({"index": i, "existing_item": existing_item,
                             "new_item_values": new_item_values, "line_values": line_values})

    coa_filenames: dict[int, str | None] = {}
    if not errors:
        for line in parsed_lines:
            coa = coa_files.get(line["index"])
            if coa is not None:
                try:
                    coa_filenames[line["index"]] = await uploads.save_coa(coa)
                except uploads.UploadError as e:
                    errors[f"lines-{line['index']}-coa"] = str(e)
            else:
                coa_filenames[line["index"]] = None

    if errors:
        return _render_list(request, session, form=header_raw, errors=errors, status_code=422)

    order = Order(**header_values)
    session.add(order)
    for line in parsed_lines:
        item = line["existing_item"]
        if item is None:
            item = InventoryItem(**line["new_item_values"], owner_id=uid)
            session.add(item)
        li = OrderItem(**line["line_values"], coa_filename=coa_filenames[line["index"]])
        item.order_items.append(li)
        order.items.append(li)
    session.commit()
    return RedirectResponse("/inventory", status_code=303)
```

Note: this task deliberately re-renders `_render_list` on error without threading the raw
per-line form values back into it (`order_form`/`order_line_form` are NOT populated here) — Task 8
adds that plumbing when it builds the actual dialog, since the exact shape those values need to
take depends on the dialog markup Task 8 writes. If Task 8's own tests need the New Order dialog to
reopen pre-filled on a validation error (matching every other dialog in this app), that test belongs
to Task 8, not this one — this task's tests only check the route's validation/creation logic via
direct POSTs, not dialog re-rendering.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py -k "test_new_order" -v`
Expected: All PASS.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/inventory.py tests/test_inventory.py
git commit -m "feat: add multi-item New Order route (backend parsing only)"
```

---

### Task 8: New Order form — template

**Files:**
- Modify: `app/templates/inventory/list.html`
- Modify: `app/routers/inventory.py`
- Modify: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `create_multi_item_order` from Task 7.
- Produces: `data-action="new-order"`, `#new-order-dialog`, `[data-action="add-line"]`,
  `[data-line]`, `[data-line-mode]` — Task 9's JS wires these up.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_inventory.py`:

```python
def test_inventory_page_has_new_order_button_and_dialog(client, db):
    t = html.unescape(client.get("/inventory").text)
    assert 'data-action="new-order"' in t
    assert 'id="new-order-dialog"' in t
    assert 'data-action="add-line"' in t


def test_new_order_validation_error_reopens_dialog(client, db):
    r = client.post("/inventory/orders", data={"order_date": "2026-09-01"})
    assert r.status_code == 422
    t = html.unescape(r.text)
    assert "data-open-on-load" in t
    assert 'id="new-order-dialog"' in t
    assert "Add at least one item" in t
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -k "new_order_button or new_order_validation_error" -v`
Expected: FAIL — no such markers in `list.html` yet.

- [ ] **Step 3: Add the New Order button and pass through re-render context**

In `app/routers/inventory.py`'s `create_multi_item_order` (from Task 7), change the error
re-render to thread through what's needed for the dialog to reopen:

```python
    if errors:
        return _render_list(request, session, new_order_form=header_raw, new_order_errors=errors,
                            new_order_line_groups=line_groups, status_code=422)
```

(replacing the `_render_list(request, session, form=header_raw, errors=errors, status_code=422)`
call Task 7 wrote — `form`/`errors` there collided with the existing Add Item dialog's own
`form`/`errors` context keys, which would incorrectly reopen the Add Item dialog instead of the
New Order dialog; the renamed keys avoid that collision).

`_render_list` needs new parameters to accept and pass these through — in
`app/routers/inventory.py`, update `_render_list`'s signature and context dict:

```python
def _render_list(request: Request, session: Session, *, form: dict | None = None, errors=None,
                 editing: InventoryItem | None = None, status_code: int = 200,
                 new_order_form: dict | None = None, new_order_errors=None,
                 new_order_line_groups: dict | None = None):
```

and add to the returned context dict (alongside the existing `"form"`/`"errors"`/`"editing"` keys):

```python
            "new_order_form": new_order_form,
            "new_order_errors": new_order_errors or {},
            "new_order_line_groups": new_order_line_groups or {},
```

- [ ] **Step 4: Add the New Order button**

In `app/templates/inventory/list.html`, next to the existing "+ Add item" button (lines 11-13),
add:

```html
  <button type="button" class="btn btn-ghost" data-action="new-order">New order</button>
```

- [ ] **Step 5: Add the New Order dialog**

Add directly after the existing `#item-dialog`'s closing `</dialog>` (currently line 354), before
the `<script type="application/json" id="inv-rules">` line:

```html
{# ---------- New Order dialog (multi-item) ---------- #}
{% set nof = new_order_form or {} %}
{% macro noerr(field) %}{% if new_order_errors[field] %}<small class="error">{{ new_order_errors[field] }}</small>{% endif %}{% endmacro %}
{% macro nocls(field, extra='') %}class="field {{ extra }} {{ 'has-error' if new_order_errors[field] }}"{% endmacro %}
<dialog id="new-order-dialog" class="dialog" {% if new_order_errors %}data-open-on-load{% endif %}>
  <form method="post" enctype="multipart/form-data" class="item-form" action="/inventory/orders" novalidate>
    <header class="dialog-head">
      <h2 data-title>New order</h2>
      <button type="button" class="btn btn-ghost btn-icon" data-action="close-new-order" aria-label="Close">×</button>
    </header>

    {% if new_order_errors %}
    <div class="alert" role="alert">Please fix the highlighted fields.</div>
    {% endif %}
    {{ noerr('lines') }}

    <fieldset class="form-section">
      <legend>Order details</legend>
      <div class="grid">
        <label {{ nocls('order_date') }}><span>Order date</span><input name="order_date" type="date" value="{{ nof.order_date or today_iso }}">{{ noerr('order_date') }}</label>
        <label {{ nocls('shipped_date') }}><span>Shipped date</span><input name="shipped_date" type="date" value="{{ nof.shipped_date }}">{{ noerr('shipped_date') }}</label>
        <label {{ nocls('tracking_site') }}><span>Tracking site (URL)</span><input name="tracking_site" type="url" value="{{ nof.tracking_site }}">{{ noerr('tracking_site') }}</label>
        <label {{ nocls('tracking_number') }}><span>Tracking number</span><input name="tracking_number" value="{{ nof.tracking_number }}">{{ noerr('tracking_number') }}</label>
        <label {{ nocls('vendor') }}><span>Vendor</span><input name="vendor" value="{{ nof.vendor }}">{{ noerr('vendor') }}</label>
        <label {{ nocls('tax') }}><span>Tax ($)</span><input name="tax" type="number" min="0" step="0.01" value="{{ nof.tax }}">{{ noerr('tax') }}</label>
        <label {{ nocls('shipping') }}><span>Shipping ($)</span><input name="shipping" type="number" min="0" step="0.01" value="{{ nof.shipping }}">{{ noerr('shipping') }}</label>
      </div>
    </fieldset>

    <div data-lines-container></div>
    <button type="button" class="btn btn-ghost" data-action="add-line">+ Add another item</button>

    <footer class="dialog-foot">
      <button type="button" class="btn" data-action="close-new-order">Cancel</button>
      <button type="submit" class="btn btn-primary">Save order</button>
    </footer>
  </form>
</dialog>

<template id="new-order-line-template">
  <fieldset class="form-section" data-line>
    <legend>Item</legend>
    <div class="chips" role="radiogroup" aria-label="Item source">
      <label class="chip-radio"><input type="radio" name="lines-__I__-mode" value="existing" checked data-line-mode> Restock existing item</label>
      <label class="chip-radio"><input type="radio" name="lines-__I__-mode" value="new" data-line-mode> New item</label>
    </div>

    <div class="grid" data-line-group="existing">
      <label class="field span-2">
        <span>Item</span>
        <select name="lines-__I__-item_id">
          <option value="">— Select —</option>
          {% for i in medicine_items + bac_water_items %}
          <option value="{{ i.id }}">{{ i.name }} ({{ i.category.value }})</option>
          {% endfor %}
        </select>
      </label>
    </div>

    <div class="grid" data-line-group="new" hidden>
      <div class="chips" role="radiogroup" aria-label="Category">
        <label class="chip-radio"><input type="radio" name="lines-__I__-category" value="Medicine" checked> Medicine</label>
        <label class="chip-radio"><input type="radio" name="lines-__I__-category" value="BAC Water"> BAC Water</label>
      </div>
      <label class="field span-2"><span>Item name</span><input name="lines-__I__-name" maxlength="200"></label>
      <label class="field" data-new-field="medium"><span>Medium</span>
        <select name="lines-__I__-medium">
          <option value="">— Select —</option>
          {% for m in mediums %}<option value="{{ m.value }}">{{ m.value }}</option>{% endfor %}
        </select>
      </label>
      <div class="field" data-new-field="vial_size_mg">
        <span>Amount</span>
        <div class="inline-inputs">
          <input name="lines-__I__-vial_size_mg" type="number" min="0" step="any">
          <select name="lines-__I__-vial_size_unit">
            {% for u in dose_units %}<option value="{{ u.value }}">{{ u.label }}</option>{% endfor %}
          </select>
        </div>
      </div>
    </div>

    <div class="grid">
      <label class="field"><span>Quantity</span><input name="lines-__I__-quantity" type="number" min="1" step="1" value="1"></label>
      <label class="field"><span>Cost ($)</span><input name="lines-__I__-cost" type="number" min="0" step="0.01" placeholder="0.00"></label>
      <label class="field"><span>Lot / Batch #</span><input name="lines-__I__-lot_number"></label>
      <label class="field"><span>Expiration date</span><input name="lines-__I__-expiration_date" type="date"></label>
      <label class="field span-2"><span>COA (photo or PDF)</span><input name="lines-__I__-coa" type="file" accept="image/*,.heic,.heif,application/pdf"></label>
      <label class="field"><span>Lab vial size (mg)</span><input name="lines-__I__-coa_vial_size_mg" type="number" min="0" step="any"></label>
      <label class="field"><span>Lab purity (%)</span><input name="lines-__I__-coa_purity_pct" type="number" min="0" max="100" step="any"></label>
    </div>
    <button type="button" class="btn btn-ghost btn-danger" data-action="remove-line">Remove item</button>
  </fieldset>
</template>
```

The `__I__` placeholder in the `<template>` is a literal string Task 9's JS replaces with the
line's actual index when cloning it (`<template>` content isn't part of the live DOM, so its
`name` attributes are inert until cloned and rewritten — this mirrors how `medium_rules`/category
gating already work in the existing Add Item dialog, just applied per-cloned-line instead of once).

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_inventory.py -k "new_order_button or new_order_validation_error" -v`
Expected: All PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add app/routers/inventory.py app/templates/inventory/list.html tests/test_inventory.py
git commit -m "feat: add the New Order dialog markup (multi-item, existing or new per line)"
```

---

### Task 9: New Order form — JS

**Files:**
- Modify: `app/static/js/inventory.js`

No new automated tests — verify manually per Step 3, matching the established precedent.

**Interfaces:**
- Consumes: `#new-order-dialog`, `#new-order-line-template`, `[data-action="new-order"]`,
  `[data-action="add-line"]`, `[data-action="remove-line"]`, `[data-line-mode]`,
  `[data-line-group]`, `[data-new-field]` from Task 8.

- [ ] **Step 1: Add the new-order-dialog IIFE**

Add to `app/static/js/inventory.js`, at the end of the file:

```js

// ---------------------------------------------------------------- inventory list: New Order dialog
(() => {
  const dialog = document.getElementById("new-order-dialog");
  if (!dialog) return;  // Only the Inventory list page has this dialog
  const form = dialog.querySelector("form");
  const linesContainer = dialog.querySelector("[data-lines-container]");
  const template = document.getElementById("new-order-line-template");
  let lineCount = 0;

  function addLine() {
    const index = lineCount++;
    const fragment = template.content.cloneNode(true);
    fragment.querySelectorAll("[name]").forEach((el) => {
      el.name = el.name.replace("__I__", String(index));
    });
    const fieldset = fragment.querySelector("[data-line]");
    syncLineMode(fieldset);
    fieldset.querySelectorAll('[data-line-mode]').forEach((radio) =>
      radio.addEventListener("change", () => syncLineMode(fieldset)));
    fieldset.querySelector('[data-action="remove-line"]').addEventListener("click", () => fieldset.remove());
    linesContainer.appendChild(fragment);
  }

  function syncLineMode(fieldset) {
    const mode = fieldset.querySelector('[data-line-mode]:checked')?.value || "existing";
    fieldset.querySelector('[data-line-group="existing"]').hidden = mode !== "existing";
    fieldset.querySelector('[data-line-group="new"]').hidden = mode !== "new";
  }

  document.querySelectorAll('[data-action="new-order"]').forEach((btn) => btn.addEventListener("click", () => {
    form.reset();
    linesContainer.innerHTML = "";
    lineCount = 0;
    addLine();
    dialog.showModal();
  }));
  dialog.querySelectorAll('[data-action="close-new-order"]').forEach((btn) =>
    btn.addEventListener("click", () => dialog.close()));
  dialog.querySelector('[data-action="add-line"]').addEventListener("click", addLine);
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });

  // Server re-rendered the page after a validation error: reopen with at least one line so the
  // dialog isn't blank (the server doesn't thread posted line values back into new lines here --
  // a known simplification carried over from Task 7/8; the error banner and field-level messages
  // still show via new_order_errors, they just don't re-populate what was typed).
  if (dialog.hasAttribute("data-open-on-load")) {
    linesContainer.innerHTML = "";
    lineCount = 0;
    addLine();
    dialog.showModal();
  }
})();
```

- [ ] **Step 2: Run the full suite**

Run: `pytest`
Expected: All pass (no Python touched).

- [ ] **Step 3: Manual verification**

Start the app and in a browser: on the Inventory list page, click "New order" — the dialog opens
with one line defaulting to "Restock existing item". Switch it to "New item" — the category
radios and Medium/Amount fields appear. Click "+ Add another item" — a second line appears,
independently toggleable between restock/new. Fill in: line 1 restocking an existing Medicine
item, line 2 a brand-new BAC Water item, shared tracking number and shipping cost, submit —
confirm both items now show the new order in their Order History (with "also in this shipment"
linking them) and both are In-Transit as one grouped row. Confirm the "New item" category radios
never offer "Supply". Confirm removing a line via "Remove item" actually removes it before
submit.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/inventory.js
git commit -m "feat: wire up the New Order dialog (add/remove lines, existing/new per line)"
```

---

### Task 10: Backup / API for the new shape

**Files:**
- Modify: `app/routers/backup.py`
- Modify: `tests/test_backup.py`

**Interfaces:**
- Consumes: `item.order_items`, `OrderItem`/`Order` from Tasks 1-2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_backup.py` (following that file's own inline-item-creation convention, per its
existing `test_json_export_includes_orders_and_category` test):

```python
def test_json_export_includes_received_quantity(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01",
    })
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
        li = s.get(InventoryItem, item_id).order_items[0]
        li.order.arrival_date = date(2026, 8, 10)
        li.received_quantity = 9
        s.commit()

    payload = client.get("/backup/export.json").json()
    item = next(i for i in payload["inventory"] if i["name"] == "Retatrutide")
    assert item["orders"][0]["received_quantity"] == 9
    assert item["orders"][0]["arrival_date"] == "2026-08-10"


def test_json_import_recreates_order_with_received_quantity(client, db):
    payload = {
        "inventory": [{
            "name": "Imported Peptide", "category": "Medicine", "medium": "Lyophilized",
            "vial_size_mg": 10, "vial_size_unit": "mg",
            "orders": [{"quantity": 10, "received_quantity": 8, "order_date": "2026-08-01",
                       "arrival_date": "2026-08-10", "tracking_number": "LY999"}],
        }],
    }
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Imported Peptide"))
        li = item.order_items[0]
        assert li.quantity == 10 and li.received_quantity == 8
        assert li.order.arrival_date == date(2026, 8, 10)
        assert item.available_count == 8


def test_json_import_tolerates_backup_with_no_received_quantity_key(client, db):
    payload = {
        "inventory": [{
            "name": "Old Style", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": 10,
            "vial_size_unit": "mg",
            "orders": [{"quantity": 10, "order_date": "2026-08-01", "arrival_date": "2026-08-10"}],
        }],
    }
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    r = client.post("/backup/import", files=files, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        item = s.scalar(select(InventoryItem).where(InventoryItem.name == "Old Style"))
        # No received_quantity in the file -- since arrival_date is set, treat it as fully received
        # (matches this order's pre-multi-item-orders meaning: it already counted as available).
        assert item.order_items[0].received_quantity == 10
        assert item.available_count == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backup.py -k "received_quantity" -v`
Expected: FAIL — `KeyError: 'received_quantity'` on export; import test fails because the field is
ignored and `available_count` comes out wrong.

- [ ] **Step 3: Update `_order_row`/`_inventory_row` and the export queries**

In `app/routers/backup.py`, replace `_order_row` (currently around lines 47-54):

```python
def _order_row(li) -> dict:
    return {
        "quantity": li.quantity, "received_quantity": li.received_quantity,
        "order_date": _iso(li.order.order_date), "shipped_date": _iso(li.order.shipped_date),
        "arrival_date": _iso(li.order.arrival_date), "tracking_site": li.order.tracking_site,
        "tracking_number": li.order.tracking_number, "vendor": li.order.vendor,
        "lot_number": li.lot_number, "cost": li.cost, "tax": li.order.tax,
        "shipping": li.order.shipping, "expiration_date": _iso(li.expiration_date),
        "coa_vial_size_mg": li.coa_vial_size_mg, "coa_purity_pct": li.coa_purity_pct,
    }
```

Update `_inventory_row`'s `"orders"` line to `"orders": [_order_row(li) for li in i.order_items],`.

Update the `selectinload` in `export_json` and `export_inventory_csv` (both currently
`.options(selectinload(InventoryItem.orders), selectinload(InventoryItem.sales))`) to
`.options(selectinload(InventoryItem.order_items).selectinload(OrderItem.order), selectinload(InventoryItem.sales))`
— add `OrderItem` to the `from app.models import (...)` block at the top of the file.

Update the CSV export loop's `for o in i.orders:` (in `export_inventory_csv`) to `for li in
i.order_items:` and the row-building line to `row = {**_order_row(li), "item_name": i.name}`. Add
`"Received"` to `ORDER_CSV_COLUMNS` (`("Received", "received_quantity")`, placed right after
`("Quantity", "quantity")`).

- [ ] **Step 4: Update `_import_inventory_row`**

Replace the Order-import loop in `_import_inventory_row` (currently around lines 153-165):

```python
    for o in row.get("orders", []):  # absent entirely in a pre-multi-item-orders backup file -- treat as none
        order = Order(
            order_date=date.fromisoformat(o["order_date"]),
            shipped_date=date.fromisoformat(o["shipped_date"]) if o.get("shipped_date") else None,
            arrival_date=date.fromisoformat(o["arrival_date"]) if o.get("arrival_date") else None,
            tracking_site=o.get("tracking_site"), tracking_number=o.get("tracking_number"),
            vendor=o.get("vendor"),
            tax_cents=round(o["tax"] * 100) if o.get("tax") is not None else None,
            shipping_cents=round(o["shipping"] * 100) if o.get("shipping") is not None else None,
        )
        # A file with no received_quantity key predates multi-item orders -- if it had arrived, it
        # already counted as fully available under the old model, so backfill received_quantity to
        # quantity (matching migration 0013's own backfill rule) rather than leaving it NULL.
        received_quantity = o.get("received_quantity")
        if received_quantity is None and o.get("arrival_date"):
            received_quantity = o["quantity"]
        li = OrderItem(
            quantity=o["quantity"], received_quantity=received_quantity,
            lot_number=o.get("lot_number"),
            cost_cents=round(o["cost"] * 100) if o.get("cost") is not None else None,
            expiration_date=date.fromisoformat(o["expiration_date"]) if o.get("expiration_date") else None,
            coa_vial_size_mg=o.get("coa_vial_size_mg"), coa_purity_pct=o.get("coa_purity_pct"),
        )
        item.order_items.append(li)
        order.items.append(li)
        session.add(order)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_backup.py -v`
Expected: All PASS. Pre-existing backup tests that constructed a payload with a flat per-item
`"orders"` array (all of them already do, per this file's existing shape) continue to work
unchanged, since the export/import shape's field names are additive (only `received_quantity` is
new) — no existing test should need changes beyond what Task 1-2's model rename already forced.

- [ ] **Step 6: Run the full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/backup.py tests/test_backup.py
git commit -m "feat: backup export/import carries received_quantity per order line"
```

---

### Task 11: Shipping/tax weighted allocation (per-vial cost)

**Files:**
- Modify: `app/models.py`
- Modify: `app/templates/inventory/detail.html`
- Modify: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `Order`, `OrderItem` from Task 1.

This task implements the spec's per-vial cost accuracy goal: an order's `shipping_cents`/
`tax_cents` split across its lines weighted by each line's own `cost_cents`, using largest-remainder
rounding so the parts sum exactly back to the entered total.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_inventory.py`:

```python
# ---------------------------------------------------------------- Multi-item orders: Task 11 (cost allocation)


def test_shipping_allocation_sums_exactly_to_the_order_total(db, me):
    from app.models import OrderItem

    med = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                        medium=Medium.LYOPHILIZED, vial_size_mg=10)
    bac = InventoryItem(owner_id=me, name="Bacteriostatic Water", category=Category.BAC_WATER)
    db.add_all([med, bac])
    db.flush()
    order = Order(order_date=date(2026, 9, 1), shipping_cents=1000)  # $10.00, split three ways
    db.add(order)
    db.flush()
    li1 = OrderItem(inventory_item_id=med.id, quantity=1, cost_cents=6667)
    li2 = OrderItem(inventory_item_id=bac.id, quantity=1, cost_cents=3333)
    li3 = OrderItem(inventory_item_id=med.id, quantity=1, cost_cents=1)
    order.items.extend([li1, li2, li3])
    db.commit()
    db.refresh(order)
    total_allocated = li1.allocated_shipping_cents + li2.allocated_shipping_cents + li3.allocated_shipping_cents
    assert total_allocated == 1000  # no dropped or invented cent from rounding


def test_shipping_allocation_is_zero_when_order_has_no_shipping_cost(db, me):
    from app.models import OrderItem

    med = InventoryItem(owner_id=me, name="Retatrutide", category=Category.MEDICINE,
                        medium=Medium.LYOPHILIZED, vial_size_mg=10)
    db.add(med)
    db.flush()
    order = Order(order_date=date(2026, 9, 1))  # no shipping_cents set
    db.add(order)
    db.flush()
    li = OrderItem(inventory_item_id=med.id, quantity=1, cost_cents=8000)
    order.items.append(li)
    db.commit()
    db.refresh(order)
    assert li.allocated_shipping_cents == 0
    assert li.total_cost == 80.0


def test_item_detail_shows_total_cost_column(client, db):
    client.post("/inventory", data={
        "name": "Retatrutide", "category": "Medicine", "medium": "Lyophilized", "vial_size_mg": "10",
        "quantity": "10", "order_date": "2026-08-01", "cost": "80.00", "shipping": "10.00",
    }, follow_redirects=False)
    with SessionLocal() as s:
        item_id = s.scalar(select(InventoryItem.id).where(InventoryItem.name == "Retatrutide"))
    t = html.unescape(client.get(f"/inventory/{item_id}").text)
    assert "Total cost" in t
    assert "$90.00" in t  # $80 cost + all $10 shipping, since it's the only line on this order
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_inventory.py -k "shipping_allocation or total_cost_column" -v`
Expected: FAIL — `AttributeError: 'OrderItem' object has no attribute 'allocated_shipping_cents'`.

- [ ] **Step 3: Add the allocation helper and properties**

In `app/models.py`, add a module-level helper directly before the `OrderItem` class:

```python
def _allocate_across_lines(total_cents: int | None, lines: list["OrderItem"]) -> dict[int, int]:
    """Splits `total_cents` across `lines` weighted by each line's own cost_cents, using
    largest-remainder rounding so the parts sum exactly back to `total_cents` -- no dropped or
    invented cent. Lines with no cost_cents (None or 0), or when total_weight is 0, get 0. Keyed
    by each line's persisted `id` (these are always already-committed rows when this runs, since
    allocation is computed at display time, never during the same transaction that creates them)."""
    if not total_cents or not lines:
        return {li.id: 0 for li in lines}
    weights = [(li, li.cost_cents or 0) for li in lines]
    total_weight = sum(w for _, w in weights)
    if total_weight == 0:
        return {li.id: 0 for li in lines}
    shares: dict[int, int] = {}
    remainders: list[tuple[float, "OrderItem"]] = []
    allocated = 0
    for li, w in weights:
        exact = total_cents * w / total_weight
        floor = int(exact)
        shares[li.id] = floor
        allocated += floor
        remainders.append((exact - floor, li))
    leftover = total_cents - allocated
    remainders.sort(key=lambda r: r[0], reverse=True)
    for i in range(leftover):
        shares[remainders[i][1].id] += 1
    return shares
```

Add three properties to `OrderItem` (after its existing `cost` property):

```python
    @property
    def allocated_shipping_cents(self) -> int:
        return _allocate_across_lines(self.order.shipping_cents, self.order.items).get(self.id, 0)

    @property
    def allocated_tax_cents(self) -> int:
        return _allocate_across_lines(self.order.tax_cents, self.order.items).get(self.id, 0)

    @property
    def total_cost(self) -> float | None:
        """This line's own cost plus its share of the order's shipping/tax, for a per-vial cost
        that accounts for what the whole shipment actually cost -- None if this line has no cost
        at all (nothing to allocate onto)."""
        if self.cost_cents is None:
            return None
        return (self.cost_cents + self.allocated_shipping_cents + self.allocated_tax_cents) / 100
```

- [ ] **Step 4: Run model-level tests to verify they pass**

Run: `pytest tests/test_inventory.py -k "shipping_allocation" -v`
Expected: Both PASS.

- [ ] **Step 5: Add the "Total cost" column to the Order History table**

In `app/templates/inventory/detail.html`'s Order History table (from Task 4), add a column after
`<th>Shipping</th>` in the header row: `<th>Total cost</th>`, and a matching cell after the
`<td>{{ li.order.shipping | money or '—' }}</td>` line: `<td>{{ li.total_cost | money or '—'
}}</td>`.

- [ ] **Step 6: Run the full test to verify it passes**

Run: `pytest tests/test_inventory.py::test_item_detail_shows_total_cost_column -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/templates/inventory/detail.html tests/test_inventory.py
git commit -m "feat: allocate shipping/tax across order lines for a per-vial total cost"
```

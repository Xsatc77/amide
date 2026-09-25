# Multi-Item Orders Design

**Spec:** restructures `Order` (introduced in
`docs/superpowers/specs/2026-09-25-order-history-detail-page-design.md`) from one row per
item-shipment into a header-plus-lines model, so a single real-world shipment that contains
several different items (a peptide restock, a new peptide, some BAC Water) can be entered,
tracked, and checked in as one order.

## Goal

Let a user record one order with multiple item lines — some restocking items they already track,
some brand new — sharing one tracking number/vendor/dates/shipping/tax, and check the whole order
in at once when it arrives, catching anything short or damaged before it ever counts as usable
stock.

## Data model

`Order` becomes two tables. `Order` keeps everything that's true of the whole shipment; the new
`OrderItem` holds everything that's true of one line in it — including COA/lot/expiration, since
those vary per item and per batch even within the same shipment (confirmed: different peptides,
different lots of the same peptide, and BAC Water can each carry their own COA/lot/expiration).

```python
class Order(Base):
    """One shipment/vendor order, possibly containing several items. Filling in arrival_date is
    the single combined "checked in" action -- see OrderItem.received_quantity."""

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

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan")

    @property
    def tax(self) -> float | None: ...
    @property
    def shipping(self) -> float | None: ...


class OrderItem(Base):
    """One line of an Order: a quantity of one InventoryItem, with its own cost/lot/expiration/COA.
    `received_quantity` is null until the parent Order is checked in; check-in fills it in for
    every line at once (pre-filled to `quantity`, editable down for anything short or damaged) --
    nothing here counts toward InventoryItem.available_count until then."""

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
    def cost(self) -> float | None: ...
```

`InventoryItem.orders` is replaced by `order_items: Mapped[list["OrderItem"]]`
(`back_populates="inventory_item"`, `cascade="all, delete-orphan"`). It has no simple `order_by`
string (an item's lines don't carry the order date themselves); routes that display Order History
sort by joining to `Order.order_date` at query time instead of relying on relationship ordering.

`available_count` changes to sum `received_quantity` (not `quantity`) across lines whose order has
arrived — the confirmed rule that damaged/short units never reach usable inventory:

```python
@property
def available_count(self) -> int:
    if self.category == Category.SUPPLY:
        return self.count
    arrived = sum((li.received_quantity or 0) for li in self.order_items if li.order.arrival_date is not None)
    return arrived - self.reconstituted_count - self.sold_count
```

## Migration

A new migration splits the existing `orders` table (currently one row per item-shipment) the same
way migration 0011 split `InventoryItem`: create `order_items`, backfill it from `orders` via raw
SQL, then drop the now-relocated columns from `orders`.

Backfill: one `OrderItem` per existing `Order` row, carrying `inventory_item_id`, `quantity`,
`cost_cents`, `lot_number`, `expiration_date`, `coa_*`. `received_quantity` backfills to `quantity`
wherever `arrival_date` is already set (that stock already counted as available under the old
model — this preserves it) and stays `NULL` otherwise (still in transit, nothing to check in
retroactively). `orders` then drops `inventory_item_id`, `quantity`, `cost_cents`, `lot_number`,
`expiration_date`, `coa_filename`, `coa_vial_size_mg`, `coa_purity_pct` and their check
constraints; `tax_cents`/`shipping_cents`/dates/tracking/vendor/`created_at` stay put, since the
header keeps its own `id`s (nothing outside `orders` referenced its id before this, so ids are
free to keep their meaning as order headers).

## New Order form

A new entry point on the Inventory list page ("New Order"), alongside the existing "+ Add item".
One order-level field group (order date, shipped date, tracking site/number, vendor, tax,
shipping — the header fields), then one or more repeatable item-line groups. Each line is either:

- **Existing item** — pick a Medicine or BAC Water item the owner already tracks from a dropdown,
  enter quantity/cost/lot/expiration/COA for this line.
- **New item** — type a name, pick category (Medicine or BAC Water only — Supply items are never
  part of a multi-item order, they stay on today's simple add/count flow), fill in the
  category-appropriate Details fields (Medium/Amount for Medicine, nothing extra for BAC Water)
  plus the same line fields as above.

At least one line is required. "Add another item" appends a blank line client-side; each line
validates independently server-side using the same category-gated rules `_parse_item_fields`
already enforces for a single new item. Submitting creates one `Order` plus one `OrderItem` per
line (and one new `InventoryItem` per "new item" line) in a single commit.

The existing single-item paths are unchanged in feel: the Inventory list's "+ Add item" still
creates one item with its first order in one step; an item detail page's "Add order" button still
adds one restock line for just that item. Both now create an `Order` header with exactly one
`OrderItem` under the hood.

## Check-in

Replaces "Edit order" as the way an order's `arrival_date` gets set. Triggered from the Inventory
list's In-Transit section (now grouped one row per `Order`, listing every line's item name and
quantity) or from an item detail page. Shows every line in that order: item name, quantity
ordered, an editable "received" quantity (pre-filled to the ordered quantity), and a notes field
("1 vial cracked in transit"). One arrival date for the whole order. Submitting sets
`Order.arrival_date` and every line's `received_quantity`/`received_note` in one commit — the
single combined "arrived and checked in" event; there is no intermediate "physically arrived but
not yet checked in" state.

Editing a line after check-in (fixing a typo in cost, or "actually one more showed up later")
still uses the per-line Edit dialog, which now also exposes `received_quantity` once the parent
order has arrived, rather than adding a second edit surface just for corrections.

## Item detail page

"Order History" lists this item's own `OrderItem` lines (same columns as today, now also showing
`received_quantity` once arrived), sorted by the parent order's date. A line whose order has other
lines (other items in the same shipment) shows a small note of what else was in that order.

## Inventory list — In-Transit section

Groups by `Order`, one row per order (not per line): tracking info once, then the item names and
quantities inside. The row's action is "Check in" (opens the check-in dialog for every line at
once) instead of today's per-line "Edit".

## Backup / API

Each exported item still carries its own `"orders"` array — one entry per `OrderItem` line
involving it — now including `received_quantity`/`received_note`, and denormalized with a copy of
that line's order-header fields (tracking/vendor/dates/tax/shipping) rather than a separate
top-level orders list. This keeps the existing per-item export shape backward-compatible and
import simple: import is already additive-only, already never reunites data across items for
anything else, and creates one `Order`+`OrderItem` pair per array entry on import (two items whose
lines originally shared one order will import as two separate single-line orders — an accepted
simplification, consistent with import never trying to preserve cross-item relationships).

## Shipping/tax allocation for per-vial cost

Not stored — computed at display time wherever a per-vial cost breakdown is shown. An order's
`shipping_cents`/`tax_cents` splits across its lines weighted by each line's own `cost_cents`
relative to the order's total line cost. The split must sum exactly back to the entered total: the
standard largest-remainder fix (compute each line's floor share, then hand out leftover cents one
at a time to the lines with the largest fractional remainder) avoids losing or inventing a cent to
rounding.

## Error handling

- New Order form: at least one line required; each line is exactly one of "existing item" or "new
  item" (never both, never neither); each line's fields validate with the same category-gated
  rules as today's single-item Add form.
- Check-in: `received_quantity` per line must satisfy `0 <= received_quantity <= quantity`
  (enforced by `ck_order_item_received_range` too); `arrival_date` required, not future-dated
  (matches the existing Order date-ordering rule).
- Deleting an `InventoryItem` cascades to delete its own `OrderItem` lines (and their COA files),
  same as today. If that was the last remaining line in its `Order`, the now-empty `Order` header
  is deleted too, so shared orders never accumulate zero-line orphans.

## Review focus

1. A short/damaged check-in (`received_quantity < quantity`) never inflates `available_count` — it
   only ever counts what was actually received.
2. Before check-in, an in-transit line contributes zero to `available_count`, even though a
   quantity was entered at order time.
3. A brand-new item created inline as one line of a multi-item order gets the same category
   validation (required Medium/Amount fields, etc.) as one created through today's Add Item form.
4. Deleting an item that was the sole remaining line in its order also removes the now-orphaned
   `Order` header; deleting one line of a still-multi-line order leaves the header and its other
   lines intact.
5. The shipping/tax weighted split sums exactly back to the entered order-level totals, with no
   dropped or invented cent (largest-remainder allocation, not naive floor division).

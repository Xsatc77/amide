# Sold Flow Design

**Spec:** implements the "Sold" half of the original Order-History/Item-Detail request
(`docs/superpowers/specs/2026-09-25-order-history-detail-page-design.md` built the other half —
categories, Orders, the item detail page, `reconstituted_count`/`sold_count` columns).

## Goal

Let a user record selling stock straight out of their inventory — a Medicine item's
unreconstituted vials, optionally bundled with some BAC Water sold in the same transaction — and
see a per-item sale history, without ever letting reconstituted/active-vial stock be sold.

## Data model

New `Sale` table, modeled on `Order`: pure history, one row per item per transaction. It does
**not** replace `InventoryItem.sold_count` — that stored counter (added in the prior spec's
migration, currently unused) is what a sale increments; `Sale` rows exist so the item detail page
can show *when* and *for how much*, exactly the way `Order` rows back `arrived` while
`reconstituted_count`/`sold_count` stay their own stored counters.

```python
class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sale_quantity_pos"),
        CheckConstraint("price_cents >= 0", name="ck_sale_price_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    sale_date: Mapped[date] = mapped_column(Date)
    price_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    inventory_item: Mapped["InventoryItem"] = relationship()

    @property
    def price(self) -> float:
        return self.price_cents / 100
```

`InventoryItem` gets a `sales` relationship (`order_by="Sale.sale_date.desc()"`,
`cascade="all, delete-orphan"`, mirroring `orders`).

A bundled Medicine+BAC Water sale is **two independent `Sale` rows** — one per item, each with its
own quantity and price, sharing only the `sale_date` the user entered once. There is no link
column between them: per your answer, they're tracked separately for accurate per-item cost
history, and nothing in the app needs to re-associate them later (no edit/delete flow is in scope
— sales are append-only, like a receipt).

## Who gets a Sold button

Medicine and BAC Water item detail pages only — never Supply (per your answer), and never
anywhere on the Inventory list page (matches how Reconstitute already works: item detail is the
only place stock-changing actions live... actually Reconstitute is list-page today; Sold is
detail-page only, since it needs the item's own available_count and, for Medicine, a second
item's dropdown).

Enforced both in the template (button only rendered for `item.category.value in ('Medicine',
'BAC Water')`) and server-side (the route 404s for a Supply item, same pattern `add_order`
already uses).

## The Sold dialog

A `<dialog>` on the item detail page, wired up the same way as the Add/Edit-order dialog
(`data-action="sold"` opens it, JS IIFE guarded the same way the order dialog is).

Fields, in order:

1. **Sale date** — date input, defaults to today via `today_iso` (already passed to the template
   for Medicine/BAC Water pages), can't be set in the future (validated server-side, same style as
   the Order date-ordering checks added in the last review).
2. **Quantity** — a `<select>` populated client-side with `1..item.available_count`. If
   `available_count` is 0 the Sold button is disabled with a title tooltip ("Nothing available to
   sell") rather than opening an empty dialog.
3. **Price sold for** — dollar input (same `_parse_money` pattern as Order's cost/tax/shipping),
   this item's portion only.
4. **Include BAC Water** checkbox — **Medicine pages only**. When checked, reveals:
   - a **BAC Water item** `<select>`, populated server-side at page-render time from the owner's
     other BAC Water items that currently have `available_count > 0` (a viewer without edit access
     never sees the Sold button at all, so this list is always the owner's own)
   - a **BAC Water quantity** `<select>`, populated client-side with `1..selected_item.available_count`
     (the per-item available counts are embedded in the option's `data-available` attribute so no
     extra request is needed when the dropdown selection changes)
   - a **BAC Water price sold for** dollar input, independent of the Medicine's price field
5. **Total** — read-only, computed client-side as Medicine price + BAC Water price (0 if the
   checkbox is unchecked or its price field is empty), recalculated on every `input` event on
   either price field. Display only — never submitted, never stored (the two prices already are).

If the owner has no BAC Water items with stock, the checkbox still renders but checking it shows
"No BAC Water in stock" in place of the dropdowns and disables submit-with-BAC-Water (you can
still submit the Medicine-only sale).

On a BAC Water item's own detail page the dialog is the plain 3-field form (date, quantity,
price) — no checkbox, since BAC Water can't bundle another BAC Water sale.

## Server-side commit

`POST /inventory/{item_id}/sales`:

1. 404 if the item isn't the caller's own, or is a Supply item.
2. Parse and validate: quantity (1..`item.available_count`), sale_date (required, not future),
   price (required, >= 0). Re-render the detail page with `sale_errors`/`sale_form` on failure —
   same pattern as `add_order`'s `order_errors`/`order_form`.
3. If `include_bac_water` is checked: resolve the chosen BAC Water item via `_own_item` (never
   trust the posted id blindly), validate it actually is category BAC_WATER, validate its own
   quantity (1..`bac_item.available_count`) and price. Any failure here re-renders with both sets
   of errors together — it's one form.
4. Commit: `item.sales.append(Sale(quantity=.., sale_date=.., price_cents=..))`,
   `item.sold_count += quantity`; if BAC Water included, the same two lines against the BAC
   Water item. One `session.commit()` for both.
5. Redirect to `/inventory/{item_id}`.

## Sale History section

New section on the item detail page (Medicine and BAC Water only), placed after Order History,
same table styling: Date, Quantity, Price, newest first. No edit/delete controls — sales are a
receipt, not a mutable record (consistent with there being no requirement to correct a sale).

## Backup & API

- `backup.py` JSON/CSV export and import carry `sales` per item, exactly the way `orders` does
  today (`_sale_row`/`_import_inventory_row` extended in parallel with the existing
  `_order_row` handling).
- `_to_json` in `inventory.py` gains a `"sales"` array per item, same shape as `"orders"`.

## Review focus (things Task-review should specifically probe)

1. Selling exactly `available_count` (the boundary) succeeds; `available_count + 1` is rejected
   both client-side (dropdown doesn't offer it) and server-side (a hand-crafted POST past the
   dropdown must still 404/422, not silently oversell).
2. A hand-crafted POST selling a Supply item, or someone else's item, 404s.
3. A hand-crafted POST for the BAC Water portion referencing a BAC Water item the caller doesn't
   own, or that isn't category BAC_WATER, is rejected — never trust the posted `bac_item_id`.
4. Selling with the BAC Water checkbox checked but leaving its quantity/price blank is a
   validation error, not a silently-skipped bundle (the user asked for it, the form should not
   quietly drop it).
5. `available_count` after a sale reflects the new `sold_count` immediately (no stale value on
   the redirect-back render).

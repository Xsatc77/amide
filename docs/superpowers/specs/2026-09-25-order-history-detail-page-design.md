# Order History & Item Detail Page — Design Spec

**Status:** Spec A of a two-spec sequence. Spec B (Sold flow) is designed after this one ships,
and depends on the item categories and detail page this spec builds.

## Goal

Replace the single order_date/shipped_date/arrival_date/vendor/cost/lot/COA fields on an
`InventoryItem` with real order history (multiple orders/lots per item, each independently
tracked from order to arrival), give every item its own detail page, split items into
Medicine / BAC Water / Supply categories, and resection the Inventory list around those
categories plus a new In-Transit view.

## Out of scope (deferred)

- The Sold flow and Sales history (Spec B).
- A computed per-vial cost breakdown UI from cost+tax+shipping (the fields are added now so the
  data exists, but no report/view is built in this spec).
- Dose-log-driven depletion of active vials (already deferred to Phase 3 by the Active Vials spec).

## Data model

### `InventoryItem` (modified)

Kept as-is: `id`, `name`, `medium`, `vial_size_mg`, `vial_size_unit`, `volume_ml`,
`units_per_package`, `storage`, `notes`, `owner_id`, `created_at`, `updated_at`.

Added:
- `category: Category` (`medicine` | `bac_water` | `supply`), not null, no default at the Python
  level (every add-item flow must choose one) — the migration backfills existing rows (see
  Migration section).
- `reconstituted_count: int`, default 0 — running total of doses of this item consumed by
  Reconstitute. Medicine/BAC Water only; always 0 and unused for Supply.
- `sold_count: int`, default 0 — running total sold (Spec B writes this; the column is added now
  so the migration only has to run once). Medicine/BAC Water only.

Kept, but repurposed by category:
- `count: int` — for **Supply** items this is the same manually-typed count as today. For
  **Medicine**/**BAC Water** items this column is no longer written to directly; a Python
  property (see below) computes the effective count from Orders instead, and the raw column is
  left at whatever the migration set it to (informational only, never displayed for those
  categories).
- `cost_cents`, `vendor`, `vendor_id`, `expiration_date` — kept, but only meaningful for
  **Supply** items now (no Orders to hold them, and expiration isn't medium-gated today so Supply
  items can and do use it). For Medicine/BAC Water these become unused; the migration moves their
  existing values onto the item's synthesized first Order and leaves the item-level columns in
  place (SQLite has no cheap column drop before Alembic batch mode, and leaving them unused costs
  nothing — see Migration).

Removed *from use* for Medicine/BAC Water (columns dropped entirely, since they have no
Supply-item meaning to preserve): `lot_number`, `order_date`, `shipped_date`, `arrival_date`,
`coa_filename`, `coa_vial_size_mg`, `coa_purity_pct`.

Added Python property:

```python
@property
def available_count(self) -> int:
    """Medicine/BAC Water: arrived-order quantity minus reconstituted/sold. Supply: the plain
    count column. This is what the Inventory list and Calculator read -- never item.count
    directly for Medicine/BAC Water."""
    if self.category == Category.SUPPLY:
        return self.count
    arrived = sum(o.quantity for o in self.orders if o.arrival_date is not None)
    return arrived - self.reconstituted_count - self.sold_count
```

### `Order` (new table `orders`)

One row per shipment/lot of a Medicine or BAC Water `InventoryItem`.

```
id: int, primary key
inventory_item_id: int, FK -> inventory_items.id, ondelete=CASCADE, indexed
quantity: int, > 0
order_date: date, not null
shipped_date: date | None
arrival_date: date | None          # arriving is what makes this order count toward available_count
tracking_site: str | None          # a URL; rendered as a link that opens in a new tab
tracking_number: str | None
vendor: str | None                 # free text, kept in sync with vendor_id.name like InventoryItem.vendor today
vendor_id: int | None, FK -> vendors.id, ondelete=SET NULL
lot_number: str | None
cost_cents: int | None
tax_cents: int | None
shipping_cents: int | None
expiration_date: date | None
coa_filename: str | None           # same upload mechanism as today's item-level COA, now per-order
coa_vial_size_mg: float | None
coa_purity_pct: float | None
created_at: datetime
```

Check constraints mirror the existing item-level ones (`quantity > 0`, cost/tax/shipping >= 0 if
set, coa_purity_pct in [0, 100], coa_vial_size_mg > 0 if set).

### `Category` enum

```python
class Category(str, enum.Enum):
    MEDICINE = "Medicine"
    BAC_WATER = "BAC Water"
    SUPPLY = "Supply"
```

## Add Item form

The category is the first choice (radio, default Medicine) and switches the rest of the form:

- **Medicine** — today's medium-driven fields (medium, amount/unit, volume/units-per-package,
  storage) *minus* vendor/cost/lot/expiration/COA/dates, *plus*: Quantity, Order date (defaults
  today), Tracking site, Tracking number, and (optional, still on this same form per your answer)
  Vendor, Cost, Tax, Shipping, Lot #, Expiration, COA upload. Submitting creates the
  `InventoryItem` and its first `Order` in one transaction. Ship/Arrival dates are *not* on this
  form — they're filled in later on the detail page, which is what moves the order from
  "in-transit" to "arrived" (counted).
- **BAC Water** — same as Medicine but without the medium-specific fields (no vial size / volume
  / units-per-package — just name, storage). Same Quantity/Order date/Tracking/optional
  vendor-cost-lot-expiration-COA set, same first-Order creation.
- **Supply** — name, count (manual, typed directly, no Orders), cost, vendor, storage, notes.
  Exactly like today's no-medium items (alcohol pads, syringes).

Server-side validation mirrors `app/inventory/rules.py`'s existing per-medium required-field
table; the category choice gates which of the three field groups is validated, the same way
medium already gates medium-specific fields.

## Item detail page (`GET /inventory/{id}`, plus its own edit/order routes)

Clicking an Inventory-list row navigates here (row click, not just the Edit button). Sections:

1. **Details** — name, category (read-only after creation — changing category is out of scope;
   delete and re-add if you need to recategorize), medium/amount/storage for Medicine, storage
   for BAC Water, count/cost/vendor/storage for Supply, notes. Editable inline, same
   validation rules as Add Item for that category.
2. **Inventory** — `available_count`, with a read-only breakdown for Medicine/BAC Water: arrived
   total, minus reconstituted, minus sold (0 until Spec B), = available. Supply just shows count.
3. **Order History** (Medicine/BAC Water only; omitted entirely for Supply) — table of this
   item's Orders, newest first: quantity, order/ship/arrival dates, tracking site (rendered as a
   link, `target="_blank" rel="noopener"`) and number, vendor, lot #, expiration, cost/tax/
   shipping, COA. Each order has an **Edit** action (to fill in ship/arrival dates, or any other
   field, as they become known) and there's an **Add order** action at the top for reorders,
   using the same fields as the Add Item form's Medicine/BAC Water order fields (minus name/
   medium, since those belong to the item, not the order).

The existing item-level COA upload route (`/inventory/{id}/coa` or wherever it lives today) moves
to be order-scoped for Medicine/BAC Water (`/inventory/{item_id}/orders/{order_id}/coa`); Supply
items have no COA (they never did).

## Inventory list page

Sections, in order: **Active Vials** (unchanged) → **Peptides/Medicines** → **BAC Water** →
**Supplies** → **In-Transit**.

- Each of the three item-category sections is a table like today's, row-click navigates to the
  detail page. Columns: Item, Count (`available_count` for Medicine/BAC Water, `count` for
  Supply), Amount/Medium (Medicine only), Cost (Supply only — Medicine/BAC Water cost lives per
  order now), Storage. **Notes column removed** (view notes on the detail page). Vendor/Arrived/
  COA columns removed for Medicine/BAC Water (no single value to show — see detail page's Order
  History); Supply keeps Vendor/Cost since it has no Orders.
- Reconstitute button stays on Medicine rows only (unchanged trigger, but now reads/writes
  `available_count`/`reconstituted_count` instead of `count`). No Sold button here — Spec B's
  Sold button lives on the item detail page.
- **In-Transit** — a flat list across every Medicine/BAC Water item's Orders where
  `arrival_date IS NULL`: item name (links to its detail page), quantity, order date, shipped
  date (if set), tracking site/number. An item can appear both in its normal category section
  (showing its arrived `available_count`) and in In-Transit (showing a pending order) at once.

## Reconstitute integration

`/calculator/reconstitute` and the Calculator's own inventory dropdown (`app/routers/
calculator.py`) switch from reading/decrementing `InventoryItem.count` to reading
`available_count` and incrementing `reconstituted_count`. The existing restriction to
`category == Category.MEDICINE and vial_size_unit == DoseUnit.MG` applies (BAC Water items are
never reconstitutable — they're the water, not the peptide). The count-0 guard, error-redirect
banner, and vial-amount-lock fixes from the prior branch carry over unchanged, just re-pointed at
`available_count`.

## Migration

One Alembic revision:

1. Create `orders` table with the schema above.
2. Add `category`, `reconstituted_count` (default 0), `sold_count` (default 0) to
   `inventory_items`.
3. Data migration, per existing `InventoryItem` row:
   - `category` = `"Medicine"` if `medium IS NOT NULL`, else `"Supply"` (matches today's real
     usage — alcohol pads/syringes have no medium set; nothing existing maps to BAC Water
     automatically, since there's no reliable existing signal for it — users can recategorize by
     deleting and re-adding as BAC Water if needed).
   - For rows landing in `category = "Medicine"`: if the row has any of
     order_date/shipped_date/arrival_date/vendor/vendor_id/lot_number/cost_cents/
     expiration_date/coa_filename/coa_vial_size_mg/coa_purity_pct set, OR `count > 0`, create one
     `Order` row carrying those values across (`quantity = count`), treating existing stock as
     already arrived: `arrival_date = arrival_date or created_at.date()` (never leave pre-existing
     stock stranded in In-Transit). A row with `count == 0` and nothing else set gets no Order
     (nothing to migrate).
   - Rows landing in `category = "Supply"` are untouched beyond getting the category value — they
     keep using `count`/`cost_cents`/`vendor` directly, no Order created.
4. Drop `lot_number`, `order_date`, `shipped_date`, `arrival_date`, `coa_filename`,
   `coa_vial_size_mg`, `coa_purity_pct` from `inventory_items` (SQLite via Alembic batch mode,
   matching how this repo already does column drops elsewhere in `migrations/`).
   `cost_cents`/`vendor`/`vendor_id`/`expiration_date` are kept (still used by Supply).

## Backup export/import impact

`app/routers/backup.py`'s JSON and CSV export need updating: the JSON export gains an `orders`
array (each with its `inventory_item_id`), and drops the now-removed per-item fields for
Medicine/BAC Water rows from the item's own JSON object (still present for Supply). CSV export
gains a second sheet/section for Orders (matching however the existing CSV export already
structures multi-entity output, e.g. protocols' titration steps). Import needs to accept the new
shape; a backup taken before this migration is not required to still import cleanly (this app has
no versioned-backup-format compatibility guarantee stated anywhere today), but the import path
must not crash on missing `orders` — treat it as an empty list for old-shape files, per this
app's existing "additive-only" backfill leniency.

## Error handling

- Add Item / Add Order: server-side validation mirrors today's `_parse_form` pattern in
  `app/routers/inventory.py` — same category-gated required-field checks as medium does today,
  re-rendered inline with field-level errors, not a redirect.
- Arrival date can't be before shipped date, which can't be before order date, when more than one
  is set (mirrors no existing date-ordering check in this app, but this is a new user-facing
  gotcha worth catching at the point orders are entered/edited).
- Deleting an `InventoryItem` cascades to its Orders (`ondelete=CASCADE`), same pattern as
  `ActiveVial.inventory_item_id` already uses.
- A Medicine/BAC Water item's Order History with zero Orders (e.g. a very old, fully-consumed
  item, or one somehow created with none) shows `available_count = 0` and an empty Order History
  table with just the "Add order" action — not an error state.

## Testing focus (Review Focus candidates for the plan)

- Existing Lyophilized items with real order/shipped/arrival/vendor/COA data migrate into exactly
  one Order each, arrived, with `available_count` matching their pre-migration `count`.
- An existing no-medium item (alcohol pads) migrates to Supply with its count/cost/vendor intact
  and no Order created.
- Reconstitute against `available_count` (not raw `count`) for a Medicine item with 2 orders (one
  arrived, one still in-transit) only counts the arrived one.
- An Order with `arrival_date IS NULL` shows under In-Transit and does *not* contribute to
  `available_count`; filling in its arrival date moves it into the item's counted stock and off
  In-Transit in the same request cycle.
- Sharing: a shared Medicine item's Order History and cost/vendor/tracking fields are visible
  read-only to the grantee (mirrors today's `_visible_items` sharing pattern) but not editable.
- BAC Water items never show a Reconstitute button/route access, even with `vial_size_unit == mg`
  set on them (they don't have that field at all, per the Add Item form design above, but the
  server-side check should still explicitly gate on `category == Category.MEDICINE`, not just
  infer it from missing fields).

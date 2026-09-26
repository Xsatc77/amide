# Dashboard Design (Roadmap Phase 4)

**Goal:** a single-person, at-a-glance homepage — today's schedule across all active protocols,
supply/expiration/shipment alerts, a cost snapshot, an adherence snapshot, and clearly-labeled
placeholders for the not-yet-built Weight/Journal/Health-integration features — replacing
`/protocols` as the app's front page.

**Deferred, explicitly out of scope for this spec:** real Weight/Measurements tracking, real
Journal entries, any actual health-app integration, per-widget show/hide preferences, per-vendor
historical shipment-time averaging (needs the still-unbuilt Vendor management page from Phase 5).

## Architecture

One new router, `app/routers/dashboard.py`, exposing `GET /dashboard`. It is a read-only
aggregation page: every widget's data comes from existing models and existing pure-function
helpers (`occurrences()`, `DoseLog`, `InventoryItem.available_count`, `OrderItem.total_cost`) —
nothing here duplicates or reimplements logic that `dosing.py`/`inventory.py`/`calendar.py` already
own. Two new small pure-function modules compute the two genuinely new things: `app/alerts.py`
(the three alert kinds) and reuse of existing cost properties for the cost snapshot (no new module
needed there).

`app/routers/auth.py`'s `HOME` constant changes from `"/protocols"` to `"/dashboard"`.
`app/templates/base.html`'s nav gets a "Dashboard" link, first in the list.

## Data model changes

Two new nullable/defaulted columns, following the exact pattern `default_discard_days` already
establishes on `User`:

```python
# InventoryItem
low_stock_threshold: Mapped[int | None] = mapped_column(Integer)  # None -> use User.low_stock_default

# User
low_stock_default: Mapped[int | None] = mapped_column(Integer)  # None -> 5 at render time
shipment_delay_days: Mapped[int | None] = mapped_column(Integer)  # None -> 21 at render time
```

Both new `User` fields are editable in Settings (`app/routers/settings.py`/`settings.html`),
exactly like `default_discard_days`'s existing field: a `<input type="number" min="1">`, same
validation shape (whole number, greater than 0). `InventoryItem.low_stock_threshold` is editable on
the Inventory item's add/edit form, alongside its other Medicine/BAC-Water-only fields.

A new migration (`0015_dashboard_thresholds.py`) adds these three columns, all nullable, no
backfill needed (nullable-with-fallback is the whole point — `None` means "use the default").

## Alerts

`app/alerts.py`, pure functions, no database writes — read the relevant rows the caller already
has (or a caller-supplied session query), return a flat list of typed alert rows the template
renders. Three kinds, each with a `severity` of `"soon"` or `"expired"`/`"over"` (for visual
yellow/red distinction) and a link to the relevant page:

1. **Low stock** — `InventoryItem` (Medicine/BAC Water only, `available_count` already excludes
   in-transit/reconstituted/sold) where `available_count <= (item.low_stock_threshold or
   viewer.low_stock_default or 5)`. One severity (`"low"`) — there's no "critical vs. warning" tier
   requested, just a flag.
2. **Vial/BAC/stock expiration** — two sources, both already-existing date fields, both checked at
   two thresholds:
   - `ActiveVial.discard_by` (open vials only, `discarded_at IS NULL`): `< today` → `"expired"`;
     `today <= discard_by <= today + EXPIRING_SOON_DAYS` → `"soon"`.
   - `InventoryItem.expiration_date` (sealed stock, `available_count > 0`): same two thresholds
     against the same field.
   `EXPIRING_SOON_DAYS = 7`, a module constant, not user-configurable in v1 (keeps this addition
   small; can become a Setting later if wanted).
3. **Shipment running long** — `Order` where `arrival_date IS NULL` and `today - (shipped_date or
   order_date) > (viewer.shipment_delay_days or 21)` days. Severity `"over"`. Per-vendor historical
   averaging is explicitly future work (see Deferred above).

All three alert queries are scoped to the **viewer's own data only** when viewing yourself, or to
the **selected shared user's data** when the viewer switcher (below) is set to someone else — never
both combined.

## Cost snapshot

For each peptide with an active protocol, find its most recent checked-in `OrderItem` (`order.
arrival_date IS NOT NULL`, `received_quantity` not null) for the linked `InventoryItem`, and show:
- **Cost per vial**: `order_item.total_cost / order_item.received_quantity` (`total_cost` already
  exists as a property, already includes this order's allocated share of shipping/tax).
- **Cost per dose**: only shown when an `ActiveVial` exists for that item with a known
  `doses_total` — `cost_per_vial / doses_total`. Omitted (not computed/guessed) when no vial has
  been reconstituted yet.

No new columns, no new tables — this is a read/derive-at-render-time widget, same as
`OrderItem.total_cost` already is. A peptide whose `ProtocolItem.inventory_item_id` is `None` (no
linked stock) or that has never had a checked-in `OrderItem` is simply omitted from this widget —
never a guessed or zeroed cost row.

## Adherence snapshot

One number: percentage of `DoseLog` rows with `status IN (ON_TIME, LATE)` vs. total rows (any
status) with `scheduled_date` in the last 30 days, for the viewer's own/selected-shared protocols.
Simple, cheap, reuses existing `DoseLog` data with no new logic beyond a `COUNT`/`GROUP BY`.

## Today's Schedule widget

Read-only. Computed exactly like `dosing.py`'s `today_page` already computes `due` (same
`occurrences()` call, same "already logged today" filter against `DoseLog`), then rendered as a
compact list: peptide, dose, time-of-day, and a status pill (`Logged` / `Skipped` / `Due`) — no
Log/Skip buttons on this widget. Each row (and a header "View full Today page" link) goes to
`/today`, where the real interaction already lives, fully tested. This widget never posts anything
and never touches `ActiveVial`/`DoseLog` — display only.

## Placeholder widgets

Three static cards, each with a short label and a one-line "coming in a future update" note:
**Weight & Measurements**, **Journal**, **Health app integrations**. No routes, no models, no
settings — literally static template content, positioned in the layout where their real versions
will eventually go, so the layout doesn't need to be reshuffled later.

## Viewer switcher (single-person view, never blended)

A dropdown at the top of the Dashboard, defaulting to "You". Its other options are every user who
has shared *anything* with the viewer (`select distinct Share.owner_id where grantee_id = viewer`),
labeled by username. Selecting one re-renders the **entire page** as that person's data — never
merged with the viewer's own. This is a deliberate departure from Inventory/Active-Vials' existing
"blend my own + everyone's shared" pattern (`_visible_items`/`_visible_active_vials` in
`inventory.py`): the Dashboard's whole purpose is an uncrowded single-person view, so blending
would defeat it.

Since sharing is per-category (`ShareCategory.INVENTORY`, `ShareCategory.PERSONAL_DATA`), the
selected user's dashboard shows only the widgets covered by what they actually shared:

| Widget | Requires |
|---|---|
| Today's Schedule | `PERSONAL_DATA` (Protocols) |
| Alerts — low stock, expiration | `INVENTORY` |
| Alerts — shipment running long | `INVENTORY` (Orders live under Inventory's data) |
| Cost snapshot | `INVENTORY` |
| Adherence snapshot | `PERSONAL_DATA` |
| Weight/Journal/Health placeholders | always shown (no real data yet regardless of viewer) |

A widget whose required category isn't shared by the selected user is simply omitted from that
render — not shown empty, not shown as an error. The dropdown itself is only rendered at all when
at least one user has shared something with the viewer; otherwise the page has no viewer-switching
UI and always shows "You" implicitly.

## Review focus

1. A Medicine item with `low_stock_threshold = 0` (explicitly set, not "unset") must use `0`, not
   silently fall back to the user default — `None` is the only "use default" sentinel, `0` is a
   real, valid threshold (e.g. an item the user tracks but doesn't restock).
2. Viewing a shared user's dashboard must never let alert/cost/adherence queries leak the viewer's
   *own* data into that render, or vice versa — the two views are fully separate query scopes, not
   a filter toggle on one combined query.
3. The viewer switcher must only ever offer users who have an active `Share` row granting the
   viewer access — never every user in the system, and never someone whose share was since revoked
   (a deleted `Share` row must remove that option immediately, no caching).
4. An `Order` past its shipment-delay threshold that nonetheless already has a non-null
   `arrival_date` (e.g. arrived late but was checked in) must not appear in alerts — the query is
   `arrival_date IS NULL`, not "took longer than N days" unconditionally.
5. The Today's Schedule widget's "already logged today" computation must match `dosing.py`'s own
   exactly (same `DoseLog` filter shape), so a dose logged from the real `/today` page immediately
   stops showing as "Due" on the Dashboard without any caching lag — both read the same table at
   render time, no denormalized/cached status anywhere.

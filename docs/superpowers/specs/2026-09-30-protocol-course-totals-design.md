# Protocol Course Totals Popup — Design

## Context

This is the second half of the "How many vials do I need?" item parked on the roadmap on
2026-09-30, and the second of its two sub-projects — the first, Cycle On/Off, shipped earlier
today and is what makes this one's math correct (an item's actual due-days now correctly exclude
any cycle-off week-ranges). This spec consolidates the decisions already made across that earlier
conversation; nothing here is newly re-opened.

The owner's original ask: an icon on every saved protocol (any status — Scheduled, Active, Paused,
Ended), clicked to show a popup of each item's total quantity needed for the entire course,
including BAC water for reconstitution, in the item's own unit, plus an estimated vial count
rounded up for any fractional remainder.

## Decisions already made (from the prior conversation)

1. **End date stays optional.** No schema change, no forced migration. A protocol with no end date
   ("ongoing") simply can't have a course total computed; the popup says so instead of showing
   numbers.
2. **As-needed items** have no fixed schedule to project a dose total from. Their course total is a
   flat "1 vial" or "1 kit of 10 vials," driven by a new per-`InventoryItem` **purchasing unit**
   setting (Individual vial / Kit of 10) — not a computed dose-based quantity.
3. **IU-dosed items** have no valid IU→mg conversion anywhere in this app already (the Calculator
   page and `_dose_volume_ml` both already accept this limitation). The popup shows the IU total
   and skips vial/BAC math for those items, exactly like the rest of the app already does.
4. **BAC water** has no stored default anywhere (it's chosen per-vial, at actual reconstitution
   time, on the Calculator/Active Vials). The popup assumes a flat **1.5 mL per vial** for the
   estimate, applied uniformly to every vial of that item across the course — not editable, not
   read from reconstitution history.
5. **Alerts:** no new "exempt from low-stock alerts" feature — the existing per-item "Low stock
   threshold" field, set to 0, already achieves it. Confirmed out of scope for this spec.

## Data model

One new enum and one new nullable-with-default column on the existing `InventoryItem` table:

```python
class PurchasingUnit(LabeledEnum):
    INDIVIDUAL = ("individual", "Individual vial")
    KIT_OF_10 = ("kit_of_10", "Kit of 10 vials")
```

`InventoryItem.purchasing_unit: Mapped[PurchasingUnit] = mapped_column(_enum_column(PurchasingUnit), default=PurchasingUnit.INDIVIDUAL)`

Only meaningful for `Category.MEDICINE` items (the same category gate `vial_size_mg`/`medium`
already use) — `Category.SUPPLY` and `Category.BAC_WATER` items always get the default
(`INDIVIDUAL`) and the field is simply not shown on their forms, mirroring exactly how
`medium`/`vial_size_mg` are already handled per-category in `_parse_item_fields`. On the form
itself it's placed next to the existing "Amount" (`vial_size_mg`) field, labeled "Purchasing unit",
and reuses that same field's existing `data-field-group="medium-only"` visibility gating rather
than introducing a new, narrower one.

Migration: add the column with a server default of `'individual'` so every existing row backfills
correctly with no data loss.

## Calculation module: `app/protocols/course_totals.py`

New, pure-ish module (one DB read for the InventoryItem lookups, otherwise pure functions over
already-loaded objects — same style as `app/calendar/schedule.py`).

```python
@dataclass
class ItemTotal:
    peptide: str
    unit: str                      # item.dose_unit.value, e.g. "mg", "mcg", "IU"
    as_needed: bool
    total_amount: float | None      # None for as-needed items (no schedule to sum)
    vials_estimate: int | None       # None when not computable (IU, or no linked inventory/vial size)
    bac_water_ml: float | None       # None whenever vials_estimate is None
    note: str | None                 # e.g. "As needed — 1 vial", "No inventory item linked"

def compute_course_totals(protocol, inventory_by_id: dict[int, "InventoryItem"]) -> list[ItemTotal] | None:
    """None when protocol.end_date is None (an ongoing protocol has no course to total)."""
```

Walking the schedule: for each non-as-needed `ProtocolItem`, iterate every calendar day from
`protocol.start_date` to `protocol.end_date` inclusive, calling the existing, already-tested
`is_due(item, protocol.start_date, day)` (from `app/calendar/schedule.py` — already correctly
excludes cycle-off week-ranges) and, for days it returns `True`, the existing
`current_step(item.steps, current_week(protocol.start_date, day))` titration lookup (same as
`_due_item()` already does) to get that day's effective dose. Sum those doses in the item's own
`dose_unit` — **deliberately ignoring `protocol.paused` and `protocol.ended_on`**: this is the full
*planned* course total, not "how much is left as of today" or "how much was actually used before an
early end." (`is_due()` itself takes a bare `start`/`day` pair and was never coupled to
`protocol_window()`'s pause/end gating, so no change to it is needed to support this.)

As-needed items: `total_amount=None`, `as_needed=True`. `vials_estimate` = 1 if the linked
`InventoryItem.purchasing_unit` is `INDIVIDUAL` (or nothing is linked), 10 if `KIT_OF_10`.
`note` = `"As needed — 1 vial"` or `"As needed — 1 kit (10 vials)"`.

Vial/BAC math (non-as-needed items only, and only when `dose_unit` is `mg` or `mcg` **and** the
item has a linked `InventoryItem` with a `vial_size_mg` set and a `vial_size_unit` of `mg` or
`mcg`): convert both the summed total dose and the vial size to a common mg basis using the same
`_DOSE_UNITS_TO_MG = {"mg": 1.0, "mcg": 0.001}` table `app/calculator/reconstitution.py` and
`app/routers/dosing.py`'s `_dose_volume_ml` already use (imported, not duplicated). Then:

```python
raw_vials = total_mg / vial_mg
vials_estimate = math.ceil(raw_vials - 1e-9)   # any positive remainder, even 0.01 vial, rounds up;
                                                 # the 1e-9 epsilon only guards float noise at an
                                                 # exact whole-vial boundary (e.g. 2.0000000001)
bac_water_ml = vials_estimate * 1.5
```

When dose_unit is `IU`, or there's no linked inventory item, or the linked item has no usable vial
size: `vials_estimate=None`, `bac_water_ml=None`, and `note` explains why (`"IU — vial count not
calculable"` / `"No inventory item linked"` / `"Inventory item has no vial size set"`).

## UI

**Icon placement.** `.protocol-card` (used for Scheduled/Active/Paused, already
`position: relative` per its existing CSS) gets a small icon-button absolutely positioned in its
bottom-right corner — a different corner from the existing top-left status ribbon, so they never
collide. For Ended protocols, which render as a `<table>` row in "Saved protocols" rather than a
card, the same icon goes into that row's existing Actions `<td>` instead (no "corner" concept
applies to a table row).

**Popup.** One shared `<dialog id="course-totals-dialog">` on the Protocols page (mirroring the
Calendar page's existing `#cal-dialog` pattern exactly: a JSON blob embedded once per page,
`protocols.js` reads it and fills/opens the dialog on icon click — no new endpoint, no AJAX
round-trip). Content per protocol:

- If that protocol's computed totals are `None` (no end date): a single message, e.g. *"Set an end
  date on this protocol to see its total course quantities."*, with a link to its Edit page.
- Otherwise: one row per item — peptide name, total (`total_amount` formatted with Python's `%g`
  — the same trailing-zero-trimmed style already used for `water.consumed_oz` on the Dashboard —
  followed by its `unit`, or the as-needed `note` in that same column when `as_needed`), vials
  estimate (or `note` when not calculable), and BAC water total in mL, `%g`-formatted the same way
  (blank when not calculable). A small caption below the table: *"Estimated using 1.5 mL
  bacteriostatic water per vial."*

## Backend wiring

`GET /protocols` (the existing list route) computes `compute_course_totals(p, inventory_by_id)` for
every protocol in both `active_views` and `saved_views`, and embeds the results keyed by protocol id
in a `<script type="application/json">` block the same way Calendar's `data` blob already works.
`inventory_by_id` is one extra query (`select(InventoryItem).where(InventoryItem.owner_id == uid)`,
already-owned items only) built once per request, not per protocol.

## Review Focus

- A protocol with no `end_date` must show the "set an end date" message for *every* one of its
  items in one shot, not a per-item error or a 500.
- An as-needed item with no linked `InventoryItem` at all must still produce a sensible default (1
  vial, `INDIVIDUAL`'s default), not crash on a `None` lookup.
- A scheduled-item total that comes out to an exact whole number of vials (e.g. exactly 2.0) must
  **not** round up to 3 — only a genuine positive remainder rounds up, verified against the
  `1e-9` epsilon's boundary case specifically.
- Titration + cycle-off interaction: an item with both must sum correctly across the whole course
  even though some weeks are off (zero contribution) and others use a stepped-up dose — this is
  exactly what `is_due()`/`current_step()` already guarantee individually; the course-totals
  calculation must not re-implement or second-guess that logic, only consume it.
- A `Category.SUPPLY` or `Category.BAC_WATER` inventory item must never be offered the Purchasing
  Unit field (it's Medicine-only), and must never cause an error if somehow linked to a protocol
  item (defaults to `INDIVIDUAL` cleanly).

---

**For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
(recommended) or superpowers:executing-plans to implement the plan built from this spec.

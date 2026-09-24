# Phase 1 — Inventory Foundations — Design

Date: 2026-09-23 · Status: proceeding under auto mode ("do as much of Phase 1 as can be accomplished")

## 1. Scope

Roadmap Phase 1 bullets, decided as follows:

| Bullet | Decision |
| --- | --- |
| Medium-aware form | Built: amount+unit, volume (Liquid), units-per-package (Autoinjector/Pill), all mediums keep amount+unit |
| Units mg/mcg/IU | Built: new `vial_size_unit` column reuses the existing `DoseUnit` enum |
| Required fields per medium | Built: a pure rules module drives both server validation and the form's JS |
| More stock details (expiration, storage) | Built: `expiration_date`, `storage` (fridge/freezer/room temp) |
| Vendors as their own table | Built as a picker: type an existing vendor's name to reuse it, or a new name to create it — same UX as the protocol builder's peptide picker. A full standalone Vendors management page is Phase 5, not built here. |
| Search, sort, filter | Built: client-side (personal inventories are small), consistent with the Library page's pattern |
| Backup/export | Built: JSON export (inventory + protocols, portable — peptides by name, goals by slug), inventory CSV export, and an **additive-only** JSON import (never deletes/overwrites existing rows) |
| Settings page / sharing | **Not built.** Owner already deferred this to a future "settings buildout." |

## 2. Data (migration `0007`)

### `vendors` (new)
id · `owner_id` FK users NOT NULL · `name` NOCASE-collated, unique per `(owner_id, name)` · `website` nullable · `contact_info` nullable · `notes` nullable · `created_at`.

### `inventory_items` (new columns, all backward compatible — nothing existing is renamed)
- `vial_size_unit` — `DoseUnit` (mg/mcg/IU), default `mg`, NOT NULL. Existing rows backfill to `mg` (correct: all prior data was entered as mg).
- `volume_ml` — float, nullable, `> 0`.
- `units_per_package` — int, nullable, `> 0`.
- `expiration_date` — date, nullable.
- `storage` — new small enum (`fridge`/`freezer`/`room_temp`), nullable.
- `vendor_id` — FK `vendors.id`, nullable, `ON DELETE SET NULL`. The existing free-text `vendor` column is kept and kept in sync with the chosen vendor's name (so every place that already reads `item.vendor` keeps working unchanged).

`vial_size_mg` keeps its name (avoiding a risky rename through templates/tests/calculator) but its meaning becomes "amount, in whatever `vial_size_unit` says" — documented in the model.

## 3. Required fields per medium (`app/inventory/rules.py`, pure, both server- and JS-enforced)

| Medium | Required |
| --- | --- |
| Lyophilized | Amount |
| Liquid | Amount, Volume (mL) |
| Autoinjector | Units per package |
| Pill | Amount (per pill), Units per package (pills per bottle) |
| Inhaler / Drops / Salve | Amount |

Field labels adapt per medium in the UI (e.g. "Doses per pen" for Autoinjector, "Pills per bottle" for Pill) — cosmetic only, the underlying field and rule are the same (`units_per_package`).

## 4. Vendor resolution

On save, the vendor text field is resolved case-insensitively against the owner's vendors: an exact case-insensitive match reuses that vendor; otherwise a new one is created. `item.vendor_id` and `item.vendor` (text) are both set from the resolved vendor. Blank vendor clears both.

## 5. List page: search / sort / filter

- A search box matches name, vendor, notes (client-side, live, same pattern as the Library page).
- Medium filter chips (All + one per medium).
- Clickable column headers (Name, Count, Amount, Cost, Vendor, Arrived) toggle ascending/descending, reordering the existing rows client-side — no server round trip, no pagination needed at personal-inventory scale.

## 6. Backup & restore

Linked from the account menu (next to "Two-factor authentication") as `/backup`, until a real Settings page exists.

- **`GET /backup/export.json`** — the signed-in user's inventory items (all fields except `coa_filename`, since the file itself isn't included — noted on the page) and protocols (with items, steps, and goal slugs), peptides referenced by name so the file is portable across a database that has a different peptide library.
- **`GET /backup/export/inventory.csv`** — inventory only, one row per item, human-readable column headers.
- **`POST /backup/import`** — upload a previously-exported JSON. **Additive only**: creates new inventory items and protocols owned by the current user; never deletes, updates, or matches against existing rows. Peptides are matched by name case-insensitively or created (same rule as the protocol builder's custom-peptide handling); unknown goal slugs are skipped with a warning rather than failing the whole import. The page states plainly that this adds records, it doesn't replace anything.

## 7. Testing

Unit: `rules.required_fields_for` per medium; vendor resolve-or-create (exact case-insensitive reuse, new creation, blank clears); migration keeps existing data and backfills `vial_size_unit='mg'`; export/import round-trip (export then import into a fresh/second account reproduces the inventory and protocol data); import skips unknown goals without failing; import never touches another user's existing rows. Integration: form shows/hides and requires the right fields per medium (both a valid save and a validation-error path per medium); search/filter/sort markup present and correct data attributes; CSV has the right headers and rows; privacy (another user's inventory/vendors/backup never visible); existing inventory tests keep passing unmodified where possible (only the minimum needed to accommodate the new required-field rules).

# Active Vials Design

Completes Phase 2 (`Reconstitution Calculator + Active Vials`): tracking a reconstituted, opened vial from the moment you mix it until you discard it.

## Context

`InventoryItem` is sealed stock on the shelf (count = 5 vials). Reconstituting one takes 1 from that count and creates an `ActiveVial` — its own record with a concentration, a mixed date, a discard-by date, and a dose count, per the target data model already in `docs/ROADMAP.md`. There is no dose-logging feature yet (that's Phase 3), so an Active Vial's "doses remaining" is a static number computed once, not something that depletes over time in this pass.

## Decisions

- **Trigger:** a "Reconstitute" button on each Lyophilized row on the Inventory page (the only medium this applies to), disabled when that item's `count` is 0 (nothing to reconstitute from). It is a starting point, not a commitment — it navigates to the Calculator prefilled with that item.
- **Duplicate-vial check:** happens when "Reconstitute" is clicked, matched on the exact `InventoryItem` (not a fuzzy peptide-name match, since `InventoryItem` has no `Peptide` link). If that item already has a non-discarded `ActiveVial`, a dialog shows its state and asks whether to continue anyway. This is a warning, not a hard block — multiple concurrent open vials for the same stock line are allowed if the user confirms.
- **The Calculator stays practice-friendly:** picking an inventory item there never forces a decision. A "Reconstitute this vial" button sits alongside the existing compute display, visible whenever an item is selected, ignorable otherwise. Only clicking it starts the commit flow.
- **One confirmation modal, not a wizard:** clicking "Reconstitute this vial" opens a single summary — item, water added, resulting concentration, total content, per-dose amount, doses per vial, and an editable discard-by date (defaulted from a setting) — then commits on confirm. No multi-step wizard; Amide's existing forms (Protocol builder, Inventory form) are single-screen, and this matches that.
- **Doses remaining is static this phase:** computed once at reconstitution (same math the Calculator already does — `doses_per_vial`), using whatever dose amount was on the Calculator screen at that moment (typed manually, or picked from the existing protocol-dose prefill list). No new peptide-to-protocol auto-matching, and no manual "used one dose" decrement — real depletion is Phase 3's job once dose logging exists.
- **Discard window:** a per-user setting (`default_discard_days`, default 28) on the existing User section of Settings, alongside timezone/colorway. It only pre-fills the discard-by date on the confirmation modal; each vial's date is editable there.
- **Ending a vial:** a manual "Discard" action (own confirmation, since it can't be undone) is always available on every Active Vial card regardless of state. Additionally, once `discard_by` has passed, the card turns red and an interrupting popup asks whether to discard — declining stamps `last_discard_prompt_at` and turns the card yellow instead, and the popup does not fire again for that vial until 24 hours have passed. Discarding in response to the popup asks a follow-up: reconstitute a new one now (routes to the Calculator prefilled from the same item) or not.
- **Discarding never deletes the row** — it sets `discarded_at` and the vial drops out of the active list, kept for history (the ROADMAP's own future features, like a Phase 4 dashboard, will want this).
- **Real icon art, now:** the Active Vials card uses `app/static/img/icons/vial-blank-label.png` (already cropped) with a text overlay: peptide name, total content (e.g. "10mg") *and* concentration (e.g. "5 mg/mL") together, discard-by date, and doses total — kept visually restrained (small, clearly legible text, not cluttering the icon). The Calculator's own syringe-fill visual is explicitly **not** touched in this pass — it's existing UI this feature only adds a button to, not UI being rebuilt.
- **No admin/sharing changes:** Active Vials follow the exact same Inventory-sharing rule as `InventoryItem` (nothing new to build — see below).

## Data model

### `ActiveVial`

New table, `active_vials`:

| column | type | notes |
|---|---|---|
| `id` | int, PK | |
| `owner_id` | `FK users.id` | private per user, same pattern as `InventoryItem` |
| `inventory_item_id` | `FK inventory_items.id` | required — always drawn from an existing stock line |
| `concentration_mg_ml` | float | canonical concentration, mg/mL |
| `water_ml` | float | BAC water added |
| `dose_value`, `dose_unit` | float, enum (`DoseUnit`) | the per-dose amount used to compute `doses_total`, snapshotted for display |
| `doses_total` | int | `doses_per_vial` from `app.calculator.reconstitution.compute()` at creation time — static |
| `date_mixed` | date | defaults to today, not editable after creation |
| `discard_by` | date | defaults from `User.default_discard_days`, editable per vial at creation (not after) |
| `discarded_at` | datetime, nullable | set on manual discard; row stays, drops out of the active list |
| `last_discard_prompt_at` | datetime, nullable | powers the 24-hour re-ask throttle on the expiry popup |
| `created_at` | datetime | |

Indexed on `owner_id` and on `inventory_item_id` (the duplicate-vial check queries by the latter).

### `User.default_discard_days`

New nullable-with-default int column on `User`, alongside `timezone`/`colorway`. Application default is 28 when unset (matches the ROADMAP's "e.g. 28 days" example); the Settings form always shows a concrete number.

## Reconstitute flow

1. **`POST /inventory/{item_id}/reconstitute`** (or a same-effect GET — see plan) checks for a non-discarded `ActiveVial` on that `inventory_item_id`. If found, the Inventory page shows a confirmation dialog with the existing vial's concentration/doses-left/discard-by before continuing (same two-step-dialog pattern the Settings admin delete-user flow already uses: a `<dialog>` with a "Continue anyway" action). Continuing (or no existing vial) redirects to `/calculator?inventory_item_id=<id>` — the Calculator's existing inventory-prefill query param, unchanged.
2. On the Calculator page, whenever an inventory item is selected (prefilled or picked manually), a **"Reconstitute this vial"** button appears next to the compute results. Clicking it opens a confirmation modal populated with the current computed values (vial content, water, concentration, dose amount, doses per vial) and an editable discard-by date input, defaulted to `today + User.default_discard_days`.
3. Confirming **`POST /calculator/reconstitute`** (or `/inventory/{item_id}/active-vials`, see plan) creates the `ActiveVial`, decrements `InventoryItem.count` by 1 (never below 0 — if `count` is already 0 the confirm button is disabled with an explanatory message), and redirects to `/inventory#active-vials`.

## Active Vials section (Inventory page)

A new section, ordered soonest-discard-by first, rendered as cards (not a table, since each card needs the vial icon + overlay text). Each card:
- The vial icon with the label overlay: item name, "10mg · 5 mg/mL" (total content and concentration together), "Discard by <date>", "<N> doses".
- A **Discard** button below the card (own confirmation dialog: "Discard this vial? This can't be undone.").
- Color state: normal (border/background as any other card) while `discard_by >= today`; **red** once passed and not recently prompted; **yellow** once passed and prompted within the last 24 hours.

**Expiry popup**, evaluated when the Inventory page loads: for each visible Active Vial where `discard_by < today` and (`last_discard_prompt_at` is null or more than 24 hours old), show one interrupting dialog per such vial ("This vial passed its discard-by date — discard it?"):
- **Discard** → sets `discarded_at`, removes it from the active list, then asks a follow-up ("Reconstitute a new one now?") — **Yes** redirects to `/calculator?inventory_item_id=<id>`; **No** just closes.
- **Not yet** → sets `last_discard_prompt_at = now`, card renders yellow, no further popup for 24 hours (the manual Discard button underneath still works immediately, any time).

## Sharing

`ActiveVial` is owned data like `InventoryItem`. It is **not** independently shareable — Inventory sharing (from the existing Sharing feature) already governs visibility of a user's inventory; Active Vials, being derived from and displayed alongside Inventory, follow the same grant with no new `ShareCategory`. A user who has *not* been granted Inventory sharing sees nothing about another user's Active Vials, same as their `InventoryItem` rows today. (No test currently exercises this since Active Vials didn't exist when Sharing shipped — the plan adds one.)

## Testing plan

- **Model/migration:** new migration adds `active_vials` and `User.default_discard_days`; upgrades and downgrades cleanly.
- **Reconstitute flow:** creating an `ActiveVial` decrements `InventoryItem.count` by exactly 1; cannot reconstitute when count is already 0; the duplicate-vial check fires only for the same `inventory_item_id`, not other items with a similar name; doses_total matches `compute().doses_per_vial` for the values used.
- **Active Vials display:** a card shows the right item, concentration, total content, discard-by, and doses; cards sort soonest-discard-by first; a discarded vial (has `discarded_at`) never appears in the active list.
- **Expiry behavior:** a vial past `discard_by` with no prior prompt triggers the popup; declining stamps `last_discard_prompt_at` and the card is yellow; re-checking within 24 hours does not re-prompt; after 24 hours it prompts again; discarding via the popup offers the reconstitute-again bridge.
- **Sharing:** a user granted Inventory sharing on another user's item sees that owner's Active Vials tagged the same way inventory items already are; a user without that grant sees none of it.
- **Icon rendering:** the Active Vials section renders the vial icon with the expected overlay text fields present in the HTML.

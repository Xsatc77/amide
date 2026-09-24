# Sharing Design

Closes the last open item in Phase 1 of the roadmap: per-person sharing of Inventory and Personal Data (Protocols) between users on the same Amide install.

## Context

Amide is self-hosted and single-instance (one server, one database — see README). There is no "household" or "network" grouping concept in the code, and none is needed: "everyone on the same network" means every account registered on this install. Sharing is opt-in and private by default — the motivating case is a household where one person wants to share inventory but keep protocols (e.g. a health journal) private from another household member unless they explicitly choose to share it.

## Decisions

- **Scope of "network":** every other user account on this install — no separate groups.
- **Granularity:** per-person, not a single broadcast toggle. A user picks specifically who can see their Inventory and, independently, who can see their Personal Data (Protocols).
- **Direction:** one-directional grants. A shares with B; B sees nothing back unless B separately shares with A. No accept/pending flow.
- **Who controls it:** the owner of the data, self-service, on their own Settings page. Admin has no role in granting or revoking shares.
- **Personal Data scope (this pass):** Protocols only (with their goals and titration steps) — the only personal health data that exists in the app today. Future personal data (dose logs, journal entries) defaults private-until-shared, following the same rule, but isn't built now.
- **Admin visibility:** none. The admin account is subject to the exact same Share rules as anyone else when viewing inventory or protocols. Admin's existing account-management powers (add/reset-password/remove-2FA/delete user) are unchanged and unrelated to this.
- **Revocation:** immediate — unchecking removes the grant; the next page load stops showing that data. No grace period.
- **Vendors:** Vendor rows become a shared reference list across all users, like the Peptide library — not scoped to an owner. Seeing a vendor's *name* (e.g. via someone else's shared inventory, or because two people happen to buy from the same supplier) never required an inventory grant. What stays private is *what was bought from them* — an `InventoryItem` — which is only visible per the Inventory share rule above.

## Data model

### `Share`

New table, `shares`:

| column | type | notes |
|---|---|---|
| `id` | int, PK | |
| `owner_id` | `FK users.id` | whose data this grants access to |
| `grantee_id` | `FK users.id` | who can see it |
| `category` | enum: `inventory` \| `personal_data` | `ShareCategory(LabeledEnum)`, non-native enum column (matches `Colorway`) |
| `created_at` | datetime | |

Unique on `(owner_id, grantee_id, category)` — one row per grant; deleting the row revokes it. Indexed on `owner_id` and on `grantee_id` (both directions are queried: "who did I grant to" on Settings, "who granted to me" on Inventory/Protocols).

Deleting a user (admin's cascading delete) deletes every `Share` row where they are the `owner_id` or the `grantee_id`.

### `Vendor` becomes shared

`Vendor.owner_id` is renamed to `created_by_id` (nullable, FK `users.id`, no `ondelete` — see below) and stops being an access-control field; it's provenance only ("who first typed this vendor in"). The unique constraint changes from `(owner_id, name)` to `name` alone (case-insensitive, via the column's existing `NOCASE` collation) — vendor names are now globally unique, same as `Peptide.name`.

`resolve_vendor()` (`app/inventory/vendors.py`) drops the owner-scoped lookup and matches by name alone; a typed vendor name reuses the existing global row if one exists, or creates a new one attributed to the current user.

Because `created_by_id` has no `ondelete=CASCADE`, deleting a user must not delete their vendors (they may still be referenced by other users' shared inventory, and the vendor name itself is a shared resource worth keeping). Instead, the admin delete-user route sets `created_by_id = NULL` on any vendors the deleted user created, before deleting the user row — the vendor and its name survive.

### Migration `0009_sharing.py`

1. **Dedupe existing vendors** before changing the constraint: for any set of `Vendor` rows sharing the same name case-insensitively (which could only happen today across *different* owners, since the old constraint already prevented one owner having two), keep the lowest `id`, repoint any `InventoryItem.vendor_id` referencing a duplicate to the kept id, then delete the duplicate rows. Plain SQL via `op.get_bind()`, since this is data-dependent, not just schema.
2. `batch_alter_table("vendors")`: rename `owner_id` → `created_by_id`, make it nullable, drop `uq_vendor_owner_name`, add `uq_vendor_name` on `name`.
3. Create the `shares` table with its indexes and unique constraint.

Downgrade reverses the constraint/column changes and drops `shares`. It does not (and cannot) restore the pre-dedupe ownership boundaries on vendors that were merged — that data is gone once merged, same as any other lossy downgrade in this codebase.

## Inventory: merged, read-only, tagged

`_own_items()` (the query behind the Inventory list) becomes `_visible_items()`: the caller's own items, plus items owned by anyone who has granted them `inventory` sharing. Each item carries whether it's the viewer's own (controls whether Edit/Delete are shown) and, for shared items, the owner's username (rendered as a small tag, e.g. "— shared by Wife").

Authorization split:
- **Mutating routes** (`update_item`, `delete_item`) keep using the existing strict `_own_item()` helper — unchanged, still 404 for anything not truly yours. Shared access never grants write access.
- **Read routes** (the list, and `get_coa`) use a new `_visible_item()` helper: owned OR shared-with-you. A shared item's COA is viewable (that's the point of inventory sharing — you can see what was logged), but the item can't be edited, and a shared COA can't be replaced.

The vendor picker on your own add/edit form is unaffected by this change beyond the global-name lookup above — you still just type a name; whether it resolves to a vendor you created or one someone else did is invisible to you either way.

## Protocols: "Shared with me" tab, read-only

`/protocols` gains a second tab next to today's Active/Saved view: **Shared with me**. A `_shared_protocol_query(uid)` (mirrors `_protocol_query`, same eager-loading) selects protocols owned by anyone who granted the viewer `personal_data` sharing. Rendered with the existing card view (`_view()` gains an optional `owner_name` passed through for the tag), but with no Pause/Resume/End/Repeat/Delete/Share controls — fully read-only. The tab is a same-page toggle (small vanilla JS, same pattern as the Admin row-menu `<details>`), not a separate route, so both datasets render server-side on one request.

This does not touch Calendar in this pass — shared protocols do not appear on the viewer's own calendar (per the earlier decision to keep shared data visually separate rather than merged for anything sensitive).

## Settings: the "Sharing" section

New section on `/settings`, in the nav between User and Display. A table: one row per other user on the install, two toggle-button columns — "Share my inventory" and "Share my personal data" — each cell a small one-button form; the button's own label and an `active`/`btn-primary` class reflect the current state ("Sharing" vs "Not shared") and a click flips it.

`POST /settings/sharing/{grantee_id}/{category}` toggles the `Share` row for `(current user as owner, grantee_id, category)`: creates it if absent, deletes it if present. 404s if `grantee_id` doesn't exist or equals the caller's own id (no self-grants), and 422s if `category` isn't `inventory` or `personal_data`. Redirects to `/settings#sharing`.

## Enforcement & edge cases

- Every read route re-checks the `Share` table server-side — never UI-only. A user who isn't a grantee hitting a shared item's or protocol's URL directly (guessing an id) gets the same 404 "not yours" treatment the app already gives for any unowned resource.
- Revoking is immediate and has no soft-delete; the next request simply stops matching the `_visible_*` query.
- Deleting a user cleans up both directions of `Share` (as owner and as grantee) and nulls out `created_by_id` on their vendors, but leaves the vendor rows, other users' `InventoryItem`/`Protocol` rows, and any `Share` grants those other users made to *each other* untouched.
- Admin gets no bypass anywhere in this feature — verified explicitly by a test where the admin account cannot see another user's private inventory/protocols without being granted access like anyone else.

## Testing plan

- **Model/migration:** `0009` upgrades cleanly from `0008`; a fixture with two different pre-existing owners who typed the same vendor name dedupes to one row and repoints the referencing `InventoryItem`; downgrade reverts the schema without crashing.
- **Vendor sharing:** two different users typing the same vendor name resolve to the same global `Vendor` row; deleting a user who created a vendor leaves the vendor (with `created_by_id` now `NULL`) usable by everyone else.
- **Grant/revoke round-trip:** toggling a Settings sharing button creates/deletes the `Share` row and flips the button's rendered state.
- **Inventory:** an item shared via `inventory` grant appears in the grantee's list tagged with the owner's name, is not editable/deletable by the grantee (route-level 404), its COA is viewable; a third, non-granted user never sees it; revoking removes it on the next load.
- **Protocols:** a protocol shared via `personal_data` grant appears only under "Shared with me" (never merged into the grantee's own Active/Saved view or Calendar), with no mutating controls rendered, and its action routes 404 for the grantee.
- **Privacy:** admin cannot see another user's inventory/protocols without an explicit grant to the admin account specifically.
- **Cascading delete:** deleting a user removes their `Share` rows in both directions and nulls `created_by_id` on their vendors, without touching unrelated users' data.

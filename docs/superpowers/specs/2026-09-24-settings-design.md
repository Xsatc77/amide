# Settings Page — Design

Date: 2026-09-24 · Status: approved by owner in chat

## 1. Intent

A `/settings` hub reached from the username menu (top right), replacing the current direct "Two-factor authentication" / "Backup & restore" menu links with one "Settings" link. One scrollable page, a sticky left jump-nav to its sections. **Sharing** (share inventory / share personal data with others on the same network) is explicitly deferred to its own future design — this page ships without it.

## 2. Sections

### User
- **Change username** — new value + current password to confirm. Same uniqueness/format rule as registration (case-insensitive).
- **Change password** — current password + new + confirm, reusing `app.auth.passwords` and its live checklist. On success, every *other* session for this user is ended (forces re-login elsewhere) — the current session stays signed in.
- **Two-factor authentication** — a status line (On/Off) with a button linking to the existing `/account/2fa` page. Not re-implemented here.
- **Timezone** — "System" (default; a browser-detected IANA zone used client-side, nothing stored) or "Manual" (pick an IANA zone, stored on `User.timezone`). **Display-only in this pass**: used to format the Admin section's "last login" times. It does **not** change "today" for Protocols or Calendar — that is a separate, larger follow-up (touches their core date math) and is called out to the owner as such, not silently included.
- **Email** — optional, format-validated, stored on `User.email`. Labeled "Used for sharing features (coming soon)" — no verification is sent; Amide has no email-sending capability.
- **Backup & restore** — a summary line with a button linking to the existing `/backup` page. Not re-implemented here.
- *(Share Inventory / Share Personal Data are not shown — deferred.)*

### Display
- **Colorway** — a grid of swatches: Light, Dark, Tequila Sunrise, Fireworks, Solarin, The Bricks, Retro, Greensleeves, High Contrast. Selecting one POSTs and reloads with the new theme applied. Stored on `User.colorway` (nullable; null = "Auto," today's `prefers-color-scheme` behavior, unchanged). Applied via a `data-theme="<value>"` attribute on `<html>`, set server-side from the signed-in user's stored value; CSS adds one `:root[data-theme="..."]` block per option overriding the existing `--bg/--surface/--text/--muted/--border/--accent/--accent-hover/--accent-soft/--accent-text/--danger/--danger-soft/--ribbon/--ribbon-text/--ribbon-soft/--ribbon-strong/--info/--info-soft` tokens. Palette-to-token mapping is a design judgment call (owner said to use whatever reads best); High Contrast uses pure black/white with no soft/tinted variants.

### Integrations
- **Apple Health**, **Hume** — static cards, "Coming soon" badge, no backend, no toggle. No API scope exists yet for either.

### Admin (only rendered when `request.state.user.is_admin`; every admin route re-checks server-side)
- **Users table**: username, admin flag, 2FA on/off, locked state, last login (owner's timezone if set). Reuses a new shared `app.users.user_rows()` helper so the CLI's `list` command and this table can't drift apart (the CLI is refactored to call it too).
- **Add new user** — username + password + confirm; creates a non-admin account directly (no "make admin" toggle in this pass — promoting a second admin isn't needed yet since only account #1 is ever auto-admin).
- Per row: **Reset password** (admin sets it directly; also clears any lockout and ends that user's other sessions), **Remove 2FA** (turns it off), **Delete user** (see below).
- **Delete user** is two-step: (1) a dialog stating this permanently deletes that user's inventory, protocols, and vendors too, with a link to `/backup` to export their data first; (2) a second dialog whose Delete button stays disabled until the exact username is typed, and sits visually offset from Cancel. Deleting **yourself** is refused server-side (the only admin today; losing it would be unrecoverable since there is no promote-to-admin flow). Deletion cascades: the target's `InventoryItem` rows (and their COA files via `uploads.delete_coa`), `Protocol` rows (items/steps/goals already cascade via the existing relationship), `Vendor` rows, then the `User` row (their `LoginSession` rows already cascade at the DB level).

## 3. Data (migration `0008`)

`users` gains: `email` (nullable, format-checked at the form layer, not a DB constraint), `timezone` (nullable string, an IANA zone name), `colorway` (nullable small enum: `light`, `dark`, `tequila_sunrise`, `fireworks`, `solarin`, `bricks`, `retro`, `greensleeves`, `high_contrast`).

## 4. Routes

`GET /settings` (the whole page) · `POST /settings/username` · `POST /settings/password` · `POST /settings/timezone` · `POST /settings/email` · `POST /settings/display` · `POST /settings/admin/users/new` · `POST /settings/admin/users/{id}/reset-password` · `POST /settings/admin/users/{id}/remove-2fa` · `POST /settings/admin/users/{id}/delete`. Every `/settings/admin/*` route 404s (not 403 — consistent with how owned-resource routes already treat "not yours") for a non-admin caller.

## 5. Testing

Unit: username/password change validation and session-invalidation-on-password-change; timezone/email/colorway save and round-trip; migration adds columns, keeps existing rows. Integration: non-admin never sees the Admin section or can reach its routes (404); admin table shows all users; add/reset-password/remove-2fa/delete work; delete cascades (inventory + COA files, protocols, vendors) and is blocked for self; delete requires the typed username to match; colorway `data-theme` attribute renders and each palette's CSS block exists; CLI `list` still matches the web table (shared helper). Privacy: nothing in Settings ever exposes another non-admin user's data.

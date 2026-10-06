# Price List Ingest (Part A: the engine inside Amide) — Design

Status: design approved in conversation 2026-10-06 (with the two dashboard alerts added); written spec pending owner review.

## Problem

New vendor price lists are posted in Telegram groups. Today each one has to be downloaded by hand, renamed to a filename
convention, attached to a vendor and imported. The owner wants a program on their computer to watch those groups and have
the app take each list in automatically: the vendor, warehouse and date are worked out from the group, the file and its
context, with no filename convention.

## Scope: two parts, this spec is part A

- **A. The ingestion engine inside Amide (this spec):** a token-protected upload address, readers for PDF, photos,
  spreadsheets and typed text, context inference (vendor, warehouse, date), a confidence rule (auto-import or review),
  the Price list inbox, undo, and two dashboard alerts.
- **B. The watcher program (own spec, later):** logs in as the owner's own Telegram account, watches chosen groups,
  backfills recent days, sends what it finds to part A, reports a group going away. Part A defines everything B may call
  (the contract below). B's login/session file and API credentials never enter the repository.

## Decisions made with the owner

| Question | Decision |
|---|---|
| Telegram account | The owner's own account (read-only watching of chosen groups). Kept out of the repo. |
| Hand-off | A secure upload address with a token the administrator creates in Settings. |
| Going live | Automatic when confident, otherwise the inbox for approval; every automatic import can be undone. |
| Formats in version 1 | PDF (text and scanned), photos (JPG/PNG, one or several), spreadsheets (xlsx), prices typed in a message. |
| Dashboard alerts | "<VENDOR> released new price list." and "<VENDOR> Telegram group is no longer active." (see Alerts). |

## Out of scope for A

The watcher itself (part B); Word documents and other formats; reading prices from voice, video or stickers; vendors that
only quote prices by direct message; editing a parsed list row by row in the inbox (reject and fix at the source, or
correct the vendor, warehouse and date); notifications outside the dashboard.

## Data model (migration 0039)

- **`ingest_tokens`**: `id`, `owner_id` (FK users, CASCADE; the administrator who made it), `label` (<=60), `prefix` (first
  6 characters of the secret, to recognize it), `token_hash` (SHA-256 of the secret, unique), `created_at`, `last_used_at`,
  `revoked_at`. The secret is shown once at creation and never stored.
- **`ingest_sources`**: `id`, `platform` (`telegram`), `chat_id` (text), `title` (<=200), `vendor_id` (FK vendors, SET NULL,
  nullable until mapped), `default_warehouse` (`us` | `china` | NULL), `enabled` (default false), `state` (`active` |
  `gone`, default active), `state_reason` (<=200), `state_changed_at`, `alert_acknowledged_at`, `created_at`. Unique on
  `(platform, chat_id)`.
- **`ingest_items`**: `id`, `source_id` (FK, CASCADE), `message_id` (text), `album_id` (text, nullable), `received_at`
  (the message's own time), `filename` (original, display only, <=200), `kind` (`pdf` | `image` | `xlsx` | `text`),
  `file_hash` (SHA-256), `stored_file` (random name, nullable), `caption` (text, <=8000), `status` (`received` |
  `imported` | `needs_review` | `ignored` | `duplicate` | `failed` | `undone` | `rejected`), `reason` (<=300), `vendor_id`,
  `warehouse`, `list_date`, `price_list_id` (FK price_lists, SET NULL), `rows_found`, `rows_matched`, `created_at`,
  `decided_at`, `decided_by` (FK users, SET NULL). Unique on `(source_id, message_id, file_hash)`.
- **`dashboard_dismissals`**: `id`, `user_id` (FK, CASCADE), `alert_key` (<=120), `created_at`; unique on `(user_id, alert_key)`.
- Files live in `config.INGEST_DIR = UPLOAD_DIR / "ingest"` under random names; the original filename is never used as a path.

## The contract the watcher uses (all JSON, `Authorization: Bearer <token>`)

- `PUT /api/ingest/sources/{chat_id}` body `{title}`: registers a group the watcher can see (created disabled and
  unmapped) or refreshes its title.
- `GET /api/ingest/sources`: the groups to watch (`enabled` and mapped): `[{chat_id, title}]`.
- `POST /api/ingest/sources/{chat_id}/state` body `{state: "active"|"gone", reason}`: the watcher reports a group that was
  deleted, made private, or that the account was removed from, and reports it active again if it returns. A quiet group is
  **not** "gone".
- `POST /api/ingest/messages` (multipart): `chat_id`, `message_id`, `album_id` (optional), `date` (ISO 8601), `text`
  (optional), `files` (0 to 10). Returns one status per file, or one for the text. Idempotent: a message or file already
  seen answers `duplicate` and changes nothing.
- A bad, missing or revoked token answers 401 with no detail; more than 60 requests a minute for one token answers 429.
  Limits: 25 MB per file, 10 files and 8,000 characters of text per message. The session login gate does not apply to
  these paths; they have their own token check, and the cross-site check passes for non-browser clients as it does today.

## Reading a message (`app/ingest/`)

1. **Kind by content, never by name:** PDF (`%PDF`), PNG, JPEG, XLSX (a zip holding `xl/workbook.xml`), otherwise `ignored`
   (the file is not kept). Typed text is its own kind.
2. **Readers produce the app's existing `PriceListData`** (rows with code, name, spec, price, flags, plus notes and a
   warehouse hint):
   - PDF: the existing reader (text, tables, per-kit lists, scans through OCR).
   - Photos: OCR of each image, rebuilt into a table by position (the existing `ocr.table_from_words`); several photos are
     read as the pages of one list. Pixel and count caps apply (50 megapixels each, at most 20 images per list).
   - Spreadsheets: each sheet becomes a table for the existing role inference (`rows_from_table`), merged names filled by the
     existing rule. Read-only mode, at most 10 sheets, 5,000 rows and 40 columns per sheet.
   - Typed text: the existing line reader (`rows_from_lines`), on the message text and caption.
3. **One list from several parts:** photos of one album (`album_id`) or, with no album, photos from the same group within
   five minutes, are held until a **settle delay** of 60 seconds after the last one arrived, then read together. A
   background task in the app (every 20 seconds) processes groups that have settled; tests call the same function with
   an injected clock.
4. **Is it a price list?** Yes when at least 3 rows were read and at least 60 percent of them have a price, or a price-word
   (price, pricelist, quote, catalog) appears in the filename or caption and at least 1 row was read. Otherwise `ignored`
   with the reason "not a price list" (the file is deleted; the item row stays for the record).

## Context inference

- **Vendor:** the group's mapping. An unmapped group (or one mapped but disabled) never imports automatically: the item
  goes to the inbox "choose the vendor".
- **Warehouse (first that applies):** the list's own text (the reader's warehouse hint) -> words in the caption or filename
  (usa, us, united states, china, chinese) -> the group's default warehouse -> China, **assumed** (the owner's rule), which
  is flagged and sends the item to the inbox unless the group has a default.
- **Date:** a date in the caption, filename or list text (YYYY-MM-DD, MM/DD/YYYY, "Oct 6", "10.06") if it is within 45
  days of the message date, otherwise the message's own date (in the server's local calendar).

## Decision: automatic or inbox

An item imports automatically only when **all** of these hold: the source is mapped to a vendor and enabled; at least 5
rows read with at least 80 percent priced; the warehouse was not merely assumed; the date is not older than the vendor's
current list for that warehouse; it is not a duplicate; and, when the vendor has a current list for that warehouse, the
median per-vial price ratio over the products in both lists is between 0.5 and 2.0 (a wildly different list is more likely
a misread). Anything else is `needs_review` with a plain reason. **Duplicates:** the same file hash for a group, or a list
whose vendor, warehouse, date and rows equal one already stored, is `duplicate` and imported again never.

Importing uses the existing `import_for_vendor(session, vendor, data, warehouse, list_date)` (so the same vendor,
warehouse and date replaces the earlier import). An automatic import sets `status = imported`, `decided_by` NULL and
raises the new-list alert. **Undo** deletes that price list (older lists remain, so the vendor's current list reverts)
and sets `undone`.

## The Price list inbox (`/settings/ingest`, administrator only; everyone else gets 404)

- **Tokens card:** create (label), copy the secret once, list with prefix and last used, revoke.
- **Sources table:** each group seen (title, vendor select with "create a vendor named like the group", default warehouse,
  enabled toggle, state, last item time).
- **Items:** newest first with status filters. An item shows what was read (counts and the first 20 rows), the reason it is
  in review, and editable **vendor, warehouse and date**; buttons **Approve** (imports with the edited values), **Reject**,
  **Undo** (after an import), and **Download original** (administrator only, served with `no-store`).
- Approving an item moves later items from the same unmapped group out of review only if the group is now mapped and the
  rules pass (they are re-evaluated; nothing is imported silently without passing the rules).

## Dashboard alerts (in the existing Alerts panel)

- **"<VENDOR> released new price list."** One per ingest-created list (automatic or approved), shown to every signed-in
  person (price data is shared), linking to that vendor's page. Each person can **Dismiss** theirs (a
  `dashboard_dismissals` row); it also expires on its own 7 days after the import. A manual upload on a vendor's page does
  not raise it.
- **"<VENDOR> Telegram group is no longer active."** Shown to the **administrator only**, for each source whose `state` is
  `gone` and whose `alert_acknowledged_at` is older than `state_changed_at`. **Dismiss** acknowledges it. It clears when the
  watcher reports the group active again, and returns if the group goes away again later. The vendor name is the mapped
  vendor's, else the group's title.

## Security and privacy

- The administrator is the only person who can create tokens, see the inbox or sources, or download an original. A token
  carries ingest rights only (it cannot read any other data).
- Tokens are SHA-256 hashed, compared in constant time, and never logged; the secret is never shown again.
- Files are validated by their bytes, size and count caps, stored under random names outside any static path, and never
  executed. Spreadsheets are opened read-only with row, column and sheet caps; images have pixel caps; PDF and OCR reuse the
  existing limits.
- The group-to-vendor mapping, the files and the tokens live only in the local database and data folder; no vendor name is
  ever written to a tracked file, and tests use invented vendors.
- Message text is stored as plain text and always escaped on display.

## Backup

A new installation-level section **Price list inbox** (sources, items and their files; not shareable). Tokens and
dismissals are not backed up (secrets and per-person noise): they join the registry's explicit "not backed up" list, which
keeps the guard test honest.

## Testing

Unit tests per reader with synthetic inputs (a generated xlsx, rendered images of invented tables, text lines, an invented
PDF); detection by content; inference rules (each precedence step, the 45-day date window); the decision rule at every
boundary; album settling with an injected clock; dedupe; undo; the API (token creation shown once and hashed, 401 on bad,
revoked and missing tokens, 429, size and count caps, idempotent replays, state reports); the inbox pages and the
administrator-only rule; both alerts (who sees them, dismissal, expiry, return after a second shutdown, clearing); backup
round trip; and that a failure in a reader never raises a server error (the item becomes `failed` with a reason). The
vendor-name scan, proven on a planted name first, stays clean.

## Review Focus

1. A forged or replayed request: a token for another purpose, a reused message id with a different file, a huge or zero-byte
   file, a zip that is not a spreadsheet, a PDF that crashes the reader, an image bomb.
2. A group mapped to the wrong vendor and a list that parses to nonsense: the rules must send it to the inbox, not the data.
3. The same list posted twice, edited later, or split over several photos with a slow last one.
4. Two lists for different warehouses of one vendor on one day.
5. A group that goes away while its lists are still current, and one that comes back.
6. The background task running while a request is writing the same item (no double import, no lost update).

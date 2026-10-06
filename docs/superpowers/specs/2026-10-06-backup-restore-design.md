# Backup and restore: design

Date: 2026-10-06. Status: awaiting review.

## Goal

From Settings, a person can back up everything of theirs, or chosen sections, and later restore everything or load
individual sections. The administrator can also back up and restore the whole installation. Today's Backup page exports
only the signed-in person's inventory and protocols as plain JSON and imports additively; nothing else is covered.

## What the owner decided

- **Two levels.** Every person backs up and restores their own data. The administrator also has a whole-installation
  backup and restore.
- **Full backup and selective backup;** full restore and selective section load ("Load").
- **Section load:** the person chooses **Replace** or **Add** per load (assumed from the recommended option; the
  owner's answer to that question did not arrive and is confirmed in review).
- **Full restore** is careful: an automatic safety backup first, a typed confirmation, all-or-nothing.
- **Backup files are always encrypted** with a passphrase.
- Vendors and price lists are never in the repository; backups contain them, so `*.amidebackup` is git-ignored and no
  test uses real vendor data (invented names only).

## Out of scope

Scheduled or automatic backups; storing backups on the server (apart from the pre-restore safety copy); cloud
destinations; merging two installations' accounts; restoring a backup from a newer app version.

## Sections

A section is a named group of tables (and files) that is exported and loaded together. Per-person sections are filtered
to that person's rows; the whole-installation backup includes every person's.

| Key | Level | Contents | Files |
|---|---|---|---|
| `profile` | person | body profile and preferences on the user row (sex, birth date, height, activity level, goal, diet preset, custom macro percentages, water goal, life stage, timezone, colorway, thresholds). Never the password hash, 2FA secret or email. | |
| `inventory` | person | inventory items, orders and order lines, sales, active vials | COAs |
| `protocols` | person | protocols, goals, items, titration steps, cycle-off weeks, dose logs | |
| `workouts` | person | plans, days, exercises, logs and exercise logs, fitness test results | workout PDFs |
| `measurements` | person | body measurements, water logs | |
| `journal` | person | entries, side effects, quick notes | |
| `labs` | person | lab panels and results | lab reports |
| `accounts` | installation | every user (including password hashes and 2FA secrets) and sharing grants. Sessions are never backed up. | |
| `vendors` | installation | vendors, contacts, payment methods and types, wallets, favorites | wallet QR images, vendor price-list files |
| `price_lists` | installation | price lists, items, alert ignores | |
| `library` | installation | peptides (all sources), goal membership, cycles, dosing tiers, monitoring tests, stack relations | library card images and `cards.json` |

The whole-installation backup is every section for every person plus the installation sections. A person's backup is
the person sections for that person.

## The file

A `.amidebackup` file: magic bytes `AMIDEBK1`, a 16-byte scrypt salt, a 12-byte nonce, then the AES-256-GCM
ciphertext (with its tag) of a zip archive. Key = scrypt(passphrase, salt). The zip holds `manifest.json`
(format version, created-at, app version, Alembic revision, the creating account, level `person` or `installation`,
the section list with row counts, and a SHA-256 per file entry), `sections/<key>.json`, and `files/<section>/<name>`.
A wrong passphrase, a truncated or modified file and an unknown magic all fail at decryption, before any parsing. A
passphrase is required (minimum 8 characters, entered twice on backup). `cryptography` (already installed through the
PDF libraries) is added to `requirements.txt` as its own pinned dependency. The whole archive is built in memory; the
page states a size limit (the existing upload limit setting) and refuses larger sets with a clear message.

Section JSON is `{"table": [row objects keyed by column name], ...}` with dates and enums in their stored forms and
every row keeping its original primary key (needed to link rows within the file).

## Backing up (Settings, Backup & restore)

The page is reworked into **Back up** and **Restore** tabs (the Settings card still links to it). On Back up: a checklist
of sections (all ticked by default), a passphrase and confirmation, and **Download backup**. The administrator sees a
second block, **Whole installation**, with its own checklist. Download is a streamed response with a dated filename.
The old JSON export and the inventory CSV stay (under "Older formats").

## Loading and restoring

On Restore the person uploads a file and its passphrase. The server decrypts and verifies it, then shows a **preview**:
creation date, who made it, level, app and database version, and each section with its row counts. Nothing has changed
yet. The decrypted archive is held server-side for ten minutes under a random token (in a private temp file, deleted
after use or on expiry) so the next step does not re-upload; nothing is kept in browser storage.

**Version rule.** A file whose Alembic revision is newer than this installation's is refused. An older revision loads:
rows are inserted by column name, so columns added since take their defaults.

**Load sections (any person, their own data).** Tick sections and choose **Replace** or **Add** for each. Only person
sections of a `person` file, or of an `installation` file for the signed-in person's own rows (matched by username),
can be loaded this way; installation sections need the administrator and use Restore everything.
- **Add:** every row in the section is inserted with a new primary key; foreign keys inside the loaded sections are
  remapped. Existing rows are untouched, so duplicates are possible and the preview says so.
- **Replace:** the person's rows of that section are deleted first (shown as counts in the confirmation), then the
  backup is inserted as for Add. Deleting follows the schema's cascades; links from other sections that point into the
  replaced rows (a protocol item's inventory item, a dose log's vial) are set null by the schema and then re-linked by
  the second rule below when those sections are in the same load.
- **Links to rows outside the load** are resolved by natural key: a peptide by name (a missing one is created as a
  custom peptide, the existing protocol-import behaviour), a vendor by name (else null), an inventory item by name, vial
  size and unit (else null), the owner is always the signed-in person. Anything that could not be resolved is counted in
  a **result report** shown after the load.
- The whole load runs in one transaction; any error rolls it all back and reports which section failed. Files are
  copied after the commit with fresh names, and removed again if the commit fails.

**Restore everything (administrator only).** Needs an `installation` file. Steps: take an automatic safety backup of the
current installation into `data/backups/` (the newest three are kept, the passphrase is the one just entered), show a
confirmation that names the data being replaced and requires typing `RESTORE`, then in one transaction delete every row
of every restorable table and insert the file's rows with their original primary keys, then swap the upload and library
directories (old ones moved aside first, removed after the commit). On any failure the transaction rolls back and the
directories stay as they were. Success signs everyone out (sessions are not in the file) and shows the sign-in page.

## Authorization

Every backup and load route requires a signed-in person; installation-level routes require the administrator and answer
404 to everyone else (the project's convention). A person's backup contains only their own rows and never `accounts`.
Loading a file made by another account is allowed for the person's own sections only when they know the passphrase,
and only ever writes the signed-in person's rows. The preview token is bound to the account that created it.

## Components

- `app/backup/sections.py`: the registry (the table above as data: tables, owner column, files, dependencies, natural-key
  resolvers) and its integrity checks.
- `app/backup/container.py`: build, encrypt, decrypt and verify the archive (pure bytes in, bytes out).
- `app/backup/export.py`: rows to section JSON, with the person filter, and file collection.
- `app/backup/load.py`: preview, Add and Replace with remapping and the result report.
- `app/backup/restore.py`: safety backup, wipe, insert and directory swap.
- `app/routers/backup.py`: routes and the page (existing JSON/CSV endpoints kept); templates `backup/backup.html`
  reworked, `backup/preview.html` new; `app/static/js/backup.js` for the passphrase and confirmation controls.
- No new tables. A guard test fails when a table is neither in a section nor on an explicit "not backed up" list
  (sessions, Alembic version), so a future table cannot be forgotten.

## Testing

Round trip per section (export, wipe, load, compare); Replace versus Add; remapping and the result report for unresolved
links; whole-installation round trip including files; wrong passphrase, truncated and tampered files, and a newer
revision; safety backup created and used when a restore fails halfway; rollback leaves data identical; permissions
(person cannot reach installation sections or others' rows; non-admin gets 404); the section-coverage guard; an
older-revision file loading with defaults; the size limit; and the legal rule (no real vendor data in tests).
Browser check of both tabs and the preview at desktop and phone width.

## Risks

- **Replace deletes data.** Mitigated by the preview counts, an explicit second step, and a pre-load safety copy of the
  person's affected sections kept for the session.
- **A lost passphrase makes a backup unreadable.** Stated plainly on the page; there is no recovery.
- **Foreign-key ordering on restore:** tables are inserted in dependency order from the registry, and the guard test
  checks the registry covers every foreign key.
- **Memory use for very large file sets:** bounded by the stated size limit.

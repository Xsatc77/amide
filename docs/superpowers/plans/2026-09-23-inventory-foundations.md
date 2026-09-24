# Phase 1 — Inventory Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Medium-aware inventory form (units, required fields, expiration/storage), a Vendors table with a pick-or-create UX, search/sort/filter on the inventory list, and JSON/CSV backup with additive-only restore.

**Architecture:** `app/inventory/rules.py` (pure, required-fields-per-medium). New `Vendor` model + resolve-or-create helper in `app/inventory/vendors.py` (pure-ish, DB-fixture tested like the peptide loader). Migration `0007`. Existing `app/routers/inventory.py` extended, not rewritten. New `app/routers/backup.py`.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Jinja2, vanilla JS, pytest, csv (stdlib).

**Spec:** `docs/superpowers/specs/2026-09-23-inventory-foundations-design.md`

## Global Constraints
- `vial_size_mg` keeps its column name; meaning is now paired with `vial_size_unit`.
- Required-fields rule lives only in `app/inventory/rules.py`; both server validation and the form's JS read it (embedded as JSON, same pattern as the protocol builder).
- Import is strictly additive: no update, no delete, ever.
- Built in worktree `C:\tmp\amide-inv` (branch `feature/inventory-foundations`).

## Review Focus
1. Selecting a medium with its required field left blank is rejected server-side even if JS is disabled/bypassed. (`test_required_fields_enforced_server_side`)
2. Typing an existing vendor's name in a different case reuses that vendor, never creates a duplicate. (`test_vendor_resolve_reuses_case_insensitively`)
3. Importing a backup never modifies or deletes another user's (or the importing user's own pre-existing) rows — only adds new ones. (`test_import_is_additive_only`)
4. A migrated database's existing inventory rows read back with `vial_size_unit == mg` and every other new column `None`/default, not broken. (`test_0007_backfills_existing_rows`)
5. Search/filter/sort never leaks another user's inventory into the page (client-side JS only filters what the server already scoped to the owner). (`test_inventory_list_still_owner_scoped_with_new_fields`)

### Task 1: Schema + rules + vendor resolution
Files: `app/models.py`, `migrations/versions/0007_inventory_foundations.py`, `app/inventory/{__init__,rules,vendors}.py`, tests `tests/test_inventory_rules.py`, extend `tests/test_migrations.py`.
- [ ] Tests: `required_fields_for(medium)` for every medium + `None`; `resolve_vendor` reuse/create/blank-clears/case-insensitive, scoped per owner; migration keeps existing inventory rows, backfills unit, adds all new columns/table, downgrade drops them.
- [ ] FAIL → implement → PASS → commit.

### Task 2: Medium-aware form
Files: `app/routers/inventory.py`, `app/templates/inventory/list.html`, `app/static/js/inventory.js`, CSS; tests extend `tests/test_inventory.py`.
- [ ] Tests: each medium's required-field validation (missing → 422 with the right message; complete → saves); vendor field creates/reuses; expiration/storage save and round-trip on edit; JSON API includes the new fields; existing tests still pass.
- [ ] FAIL → implement → PASS; browser check (medium switch shows/hides + relabels fields) → commit.

### Task 3: Search, sort, filter
Files: `app/templates/inventory/list.html`, `app/static/js/inventory.js`, CSS; tests extend `tests/test_inventory.py`.
- [ ] Tests: rows carry the right `data-search`/`data-sort-*` attributes; filter chips for All + each medium present; owner-scoping unaffected by the new markup.
- [ ] FAIL → implement → PASS; browser check → commit.

### Task 4: Backup & restore
Files: `app/routers/backup.py`, `app/templates/backup/backup.html`, `app/main.py`, `base.html` (account-menu link); tests `tests/test_backup.py`.
- [ ] Tests: JSON export shape and content; CSV headers/rows; import creates owned rows from a valid export, skips unknown goals with a warning, never touches another user's or pre-existing rows (additive-only); round trip (export → import into a second account → matches).
- [ ] FAIL → implement → PASS; browser check → commit.

### Task 5: Docs + ship
- [ ] README/ROADMAP (mark Phase 1 done except Settings/sharing); full suite; merge; push.

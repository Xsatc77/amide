# Peptide Library & Card Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import the owner's 100 peptide cards into a searchable Library with card text + images and owner-editable dose/goal fields.

**Architecture:** Migration `0004` adds card columns to `peptides`. `app/library/loader.py` loads a `cards.json` into the DB (tested, dependency-free). `tools/import_cards.py` (PyMuPDF, tools-only) turns the PDF into `cards.json` + WebP images, then calls the loader. `app/routers/library.py` serves list/detail/edit/card-image pages.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Jinja2, vanilla JS, pytest; PyMuPDF only in `tools/`.

**Spec:** `docs/superpowers/specs/2026-09-22-peptide-library-design.md`

## Global Constraints

- No new runtime dependency in `requirements.txt`; PyMuPDF only in `tools/requirements.txt`.
- Card content and images only under the data dir (`data/library/`), never in the repo or tests' fixtures beyond tiny synthetic samples.
- Imports never modify owner fields (`aliases`, `dose_*`, `typical_frequency`, `notes`) or `goal_peptides`.
- Card image route must only serve files named like `NNN.webp` from the cards dir.

## Review Focus

1. Re-running the import after the owner edited doses/notes → owner edits survive. (Task 1 `test_load_keeps_owner_fields`)
2. Card whose name disagrees with the library peptide at that number → skipped and reported, nothing overwritten. (Task 1 `test_load_mismatch_skipped`)
3. Requesting `/library/{id}/card` for a peptide with no image, or with a tampered filename → 404, never a file outside the cards dir. (Task 3 `test_card_image_route`)
4. Editing goals: untick a goal → peptide leaves that stack; tick → appended at the end, other stack order unchanged. (Task 3 `test_edit_goal_membership`)
5. Dose range given out of order (high < low) → validation error, nothing saved. (Task 3 `test_edit_validation`)

---

### Task 1: Schema + loader + reload command

**Files:** Modify `app/models.py`, `app/config.py` (`LIBRARY_DIR`, `CARDS_DIR`, `CARDS_JSON`); Create `migrations/versions/0004_library_cards.py`, `app/library/__init__.py`, `app/library/loader.py`, `app/library_load.py`; Test `tests/test_library_loader.py`, extend `tests/test_migrations.py`.

**Produces:** `Peptide.card_class/category/evidence_level/status/card_details/card_image`; `LoadReport(updated: list[str], created: list[str], mismatched: list[str])`; `load_cards(session, cards: list[dict]) -> LoadReport`; `python -m app.library_load [path]`.

- [ ] Write tests: update existing by number (card columns set, `card_details` holds the other sections, image filename stored); create when number unknown; mismatch skipped + reported; owner fields untouched (set dose/notes/aliases first, load, assert unchanged); idempotent (load twice → same, report on 2nd run lists updated); migration upgrade 0003→head keeps rows and adds columns; downgrade to 0003 drops them.
- [ ] Run → FAIL. Implement. Run → PASS. Commit `feat: library card columns and loader`.

### Task 2: PDF extractor tool

**Files:** Create `tools/requirements.txt` (`pymupdf`), `tools/import_cards.py`, `tools/README.md`; Test `tests/test_import_cards_helpers.py` (pure helpers only; skipped if PyMuPDF missing is **not** needed because helpers take plain tuples).

**Produces:** helpers `group_lines(words, y_tol=3) -> list[list[word]]`, `join_lines(lines) -> str`, `is_spaced_heading(text) -> bool`, `split_bullets(lines) -> list[str]`; CLI `python tools/import_cards.py PDF [--data-dir DIR] [--no-load]`.

- [ ] Write helper tests: words on the same baseline (±3pt) group into one line ordered by x; bold fragments split across spans rejoin ("Modulates", "angiogenesis and fibroblast migration", ".") → "Modulates angiogenesis and fibroblast migration."; letter-spaced headings detected ("K E Y A P P L I C A T I O N S"); bullet splitting starts a new bullet on a capitalised line after a line ending in "." .
- [ ] Run → FAIL. Implement helpers + region map + CLI. Run → PASS.
- [ ] Run the tool into a scratch data dir; compare ≥ 10 cards (1, 2, 4, 26, 37, 50, 66, 88, 99, 100) against their rendered images; fix region bounds until text matches. Record any card-specific residue as a ledger note.
- [ ] Commit `feat: card import tool`.

### Task 3: Library pages, edit, card image, builder link, API

**Files:** Create `app/routers/library.py`, `app/library/forms.py`, `app/templates/library/list.html`, `detail.html`, `edit.html`, `app/static/js/library.js`; Modify `app/main.py`, `base.html` (nav), `app/routers/protocols.py` (API fields), `app/static/js/protocol-builder.js` (View card link), `app/static/css/app.css`; Test `tests/test_library.py`.

- [ ] Write tests: list page has tiles for all peptides + search data attributes + goal chips; detail shows card sections and card # and "Used in protocols" link; `test_card_image_route` (serves webp with correct type; 404 when none; 404 when `card_image` is `../x`); `test_edit_validation` (dose ≤ 0, high < low → 422, nothing saved); `test_edit_saves_owner_fields`; `test_edit_goal_membership`; builder data includes peptide ids so the JS link works (check `View card` template string exists in JS is not testable → covered manually); `/api/peptides` has new fields.
- [ ] Run → FAIL. Implement. Run → PASS. Manual browser check desktop + phone. Commit `feat: peptide library pages`.

### Task 4: Import the owner's cards, docs, ship

- [ ] Run `tools/import_cards.py` against the owner's PDF with `--data-dir` = the owner's real `data/` (after backing up `amide.db` to `amide.db.bak-<date>`); read the report (expect 100 updated, 0 mismatched).
- [ ] Update README (status, `tools/` usage, reload command) and ROADMAP; full suite PASS; commit; push.

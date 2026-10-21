# Apple Health (Health Auto Export) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Receive everything Health Auto Export posts, keep it raw and as a clean per-metric history, let the owner choose what to show through a first-data popup, and chart it on a new Apple tab.

**Architecture:** A token-protected `POST /api/ingest/health` streams the body to disk, stores it gzip-compressed (deduplicated by hash), and parses it into `health_readings` (numeric series) and `health_events` (workouts, symptoms, state of mind, ECG, heart rate notifications, cycle tracking, medications). Per-owner `health_metric_choices` rows drive a popup and the Apple tab. Tokens reuse `ingest_tokens` with a new `scope` column. Everything lives in a new `app/health/` package plus thin routers and templates.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Jinja, vanilla JS, SQLite, pytest (existing fixtures `client`, `db`, `me` in `tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-10-20-apple-health-integration-design.md`

## Global Constraints

- Next migration number is `0077` (latest is `0076`); follow the style of `migrations/versions/0076_photo_reminders_sent.py`.
- Never put a vendor name, real price or the owner's domain in the repo; use invented examples (Acme, Zephyr) and the host the page is viewed from. After each task run `.venv/Scripts/python.exe -I .superpowers/scan_text.py` (list the changed text files in it) and expect no HIT.
- The guard test in `app/backup/sections.py` fails when a table is neither in a section nor in `NOT_BACKED_UP`; every new table must be placed.
- A payload that is huge, malformed or unknown must never crash the server, never leave a half-imported export, and is stored raw and flagged, never silently dropped.
- Default body cap 512 MB (`AMIDE_HEALTH_MAX_MB`), default 30 requests per minute per token, raw exports kept 90 days (spec, call 7 and Storing).
- Facts marked "Not established" in the spec (top-level wrapper, shapes of symptoms, ECG, heart rate notifications, state of mind, cycle tracking, medications, full metric-name list, batch splitting) must be handled tolerantly and marked `# Not established: ...` in code; the real sample from the owner's Manual Export wins over the docs (Task 10).
- Commit style: lowercase `feat:`/`fix:`/`docs:` and end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. "Commit" also means push; watch CI after each push.

## Review Focus

1. A body over the cap, or a chunked upload with no length: answer 413 without reading it all, keep nothing. (Task 3)
2. The same export posted twice, or an overlapping export: no duplicate rows, no error. (Tasks 3, 4)
3. A reading with `qty` null, a string, a missing date, or an unknown metric name: skip or keep that reading only, import the rest, flag the export. (Task 4)
4. Two users: one user's token or choices never reads or writes the other's data; a revoked token and a watcher token are refused on the health address. (Tasks 2, 3, 7)
5. Dates with a UTC offset around midnight: a reading at 23:30 -0800 lands on the right local day for the owner's time zone. (Task 7)

## File Structure

- Create `app/health/__init__.py`, `groups.py` (metric to section map and recommended set), `receive.py` (store a request body), `parse.py` (payload to readings and events), `choices.py` (popup and switches), `series.py` (daily series for charts), `history.py` (MedGemma-ready read function and raw purge).
- Create `app/routers/health_api.py` (the POST address), `app/routers/apple_health.py` (Settings card actions and the Apple tab data).
- Create `app/templates/health/_settings_card.html`, `_choices_dialog.html`, `apple_tab.html`; modify `app/templates/settings/settings.html`, `app/templates/measurements/index.html`, `app/templates/base.html` (popup include).
- Modify `app/models.py`, `app/config.py`, `app/auth/gate.py` (no change expected; `/api/ingest/` already open), `app/ingest/tokens.py`, `app/backup/sections.py`, `app/main.py`, `app/routers/measurements.py` (`TABS`), `tests/conftest.py`.
- Create `migrations/versions/0077_apple_health.py`.
- Tests under `tests/health/` (create the folder; mirror the style of `tests/test_journal_appetite.py`).

---

### Task 1: Tables, models, backup placement

**Files:** Create `migrations/versions/0077_apple_health.py`; modify `app/models.py`, `app/backup/sections.py`, `app/config.py`, `tests/conftest.py`; test `tests/health/test_models.py`.

**Interfaces:**
- Produces models in `app/models.py`: `HealthRaw(id, owner_id, received_at, sha256, size_bytes, file_name, status, note, automation_name, readings_count)` with `UniqueConstraint(owner_id, sha256)` and `status in ('stored','parsed','flagged')`; `HealthReading(id, owner_id, metric, start_at, end_at, offset_minutes, grain, value, unit, source, extra)` unique on `(owner_id, metric, start_at, grain, source)`, `grain in ('second','minute','hour','day','point')`, `value` nullable Float, `extra` JSON; `HealthEvent(id, owner_id, kind, external_id, start_at, end_at, name, payload)` unique on `(owner_id, kind, external_id)`; `HealthMetricChoice(id, owner_id, metric, enabled, decided_at)` unique on `(owner_id, metric)`; `IngestToken.scope` String(12) default `'watcher'`. All `owner_id` foreign keys cascade on user delete. Datetimes are naive UTC (`naive_utcnow` style).
- Produces config: `HEALTH_RAW_DIR = UPLOAD_DIR / "health_raw"`, `HEALTH_MAX_BYTES` (from `AMIDE_HEALTH_MAX_MB`, default 512), `HEALTH_RATE_PER_MINUTE = 30`, `HEALTH_RAW_KEEP_DAYS = 90`.

- [ ] **Step 1:** Write `test_models.py`: `test_upgrade_creates_the_health_tables_and_scope_column` (run Alembic to head on a temp SQLite as `tests/test_journal_appetite.py::test_migration_0063_...` does; assert the four tables and `ingest_tokens.scope` exist, existing token rows get `'watcher'`), `test_reading_key_is_unique` (inserting the same owner/metric/start/grain/source twice raises `IntegrityError`), and the existing backup guard test file's name run as-is.
- [ ] **Step 2:** Run `pytest tests/health/test_models.py -v`; expect FAIL (tables missing).
- [ ] **Step 3:** Add the models, the migration (downgrade drops them), config values, `HealthRaw` in `NOT_BACKED_UP` (table name `health_raw`), and `health_readings`, `health_events`, `health_metric_choices` as a new person section `Section("apple_health", "Apple Health", PERSON, (Tbl("health_readings", "owner_id = :uid"), Tbl("health_events", "owner_id = :uid"), Tbl("health_metric_choices", "owner_id = :uid")), help="...")`. Add the four `health_*` deletes to `tests/conftest.py`'s cleanup.
- [ ] **Step 4:** Run `pytest tests/health/test_models.py tests -k "backup" -q`; expect PASS (the backup guard passes).
- [ ] **Step 5:** Commit `feat: tables for Apple Health data`.

### Task 2: Health tokens (scope) and Settings token actions

**Files:** Modify `app/ingest/tokens.py`; create `app/routers/apple_health.py` (token routes only for now); modify `app/main.py`; test `tests/health/test_tokens.py`.

**Interfaces:**
- Consumes: `IngestToken.scope` (Task 1).
- Produces in `tokens.py`: `HEALTH_PREFIX = "amide_hlt_"`; `create_token(session, owner_id, label, scope="watcher") -> tuple[IngestToken, str]` (health secrets start with `HEALTH_PREFIX`); `find_token(session, header, scope="watcher") -> IngestToken | None` (watcher scope stays admin-only and `amide_ing_` only; health scope accepts any active owner, requires `HEALTH_PREFIX` and `token.scope == "health"`). Routes: `POST /settings/apple-health/token` (creates one, redirects with the secret shown once through a one-time session flash, same mechanism the price-list inbox uses for its token) and `POST /settings/apple-health/token/{id}/revoke` (owner only).

- [ ] **Step 1:** Tests: `test_health_token_works_for_a_non_admin_and_is_shown_once`, `test_watcher_token_is_refused_on_health_scope`, `test_health_token_is_refused_on_watcher_scope`, `test_revoked_token_is_refused`, `test_one_person_cannot_revoke_anothers_token`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement as signatures above; reuse `hash_secret`, `limiter` untouched.
- [ ] **Step 4:** Run `pytest tests/health/test_tokens.py tests -k "ingest" -q`; expect PASS (price-list watcher tests unchanged).
- [ ] **Step 5:** Commit `feat: Apple Health tokens, separate from the price-list watcher's`.

### Task 3: The receiver and raw storage

**Files:** Create `app/health/__init__.py`, `app/health/receive.py`, `app/routers/health_api.py`; modify `app/main.py`; test `tests/health/test_receive.py`.

**Interfaces:**
- Consumes: `tokens.find_token(..., scope="health")`, `HealthRaw`, config values.
- Produces: `async def store_body(request, owner_id, session) -> HealthRaw` in `receive.py` (streams `request.stream()` into a temp file in `HEALTH_RAW_DIR`, aborting with `HTTPException(413)` as soon as bytes exceed `HEALTH_MAX_BYTES`, hashing as it goes; if a `HealthRaw` with the same owner and sha256 exists returns it with `duplicate` noted and deletes the temp file; else gzips to `<sha256>.json.gz`, inserts a row with `status='stored'`, and records `automation_name` from the `automation-name` header). Route `POST /api/ingest/health` in `health_api.py`: `require_health_token` dependency (401 bad token, 429 over `HEALTH_RATE_PER_MINUTE` using its own `RateLimiter`), stores the body, calls `parse.import_raw(session, raw)` (Task 4; until then a stub returning 0), answers `200 {"received": n_bytes, "readings": n, "duplicate": bool}`.

- [ ] **Step 1:** Tests: `test_no_token_is_401`, `test_valid_post_stores_a_gzipped_copy_and_answers_200`, `test_same_body_twice_is_a_duplicate_and_adds_no_row`, `test_body_over_the_cap_is_413_and_stores_nothing` (monkeypatch `config.HEALTH_MAX_BYTES` to 1000), `test_chunked_body_without_length_is_capped_too`, `test_a_body_that_is_not_json_is_stored_and_flagged_not_rejected` (status `flagged`, still 200), `test_rate_limit_is_429`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement per Interfaces; delete the temp file on any exception.
- [ ] **Step 4:** Run `pytest tests/health/test_receive.py -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: receive Health Auto Export posts and keep each one raw`.

### Task 4: Parser to readings and events

**Files:** Create `app/health/parse.py`, `app/health/groups.py`; test `tests/health/test_parse.py`.

**Interfaces:**
- Consumes: `HealthRaw`, models.
- Produces: `parse_date(text: str) -> tuple[datetime, int]` (UTC naive datetime and offset minutes from `"yyyy-MM-dd HH:mm:ss Z"`; also accepts a bare `"yyyy-MM-dd"` as local midnight, offset 0); `GRAIN_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}`; `import_raw(session, raw: HealthRaw) -> int` (reads the gz file, accepts the payload either as `{"metrics": [...], "workouts": [...], ...}` or wrapped in a `"data"` key, upserts readings and events, sets `readings_count` and `status` to `parsed`, or `flagged` with a `note` when anything was skipped or the JSON was unusable; never raises on bad content; returns the number of readings written); `metric_group(name: str) -> str` and `RECOMMENDED: frozenset[str]` and `SECTIONS: tuple[str, ...] = ("Activity","Body","Nutrition and water","Sleep","Heart and vitals","Mind and other")` in `groups.py` (the spec's default on-list: `step_count, walking_running_distance, flights_climbed, weight_&_body_mass, body_fat_percentage, lean_body_mass, body_mass_index, dietary_water, dietary_energy, protein, carbohydrates, total_fat, fiber`, plus symptoms and state of mind as event kinds; unknown names go under "Mind and other").
- Rules: a numeric metric `{qty, date, source}` becomes one reading with `grain` taken from the `automation-aggregation` header when present, else `"point"`; `end_at = start_at + grain seconds` (equal to start for `"point"`); blood pressure stores `value=systolic` with `extra={"systolic":..,"diastolic":..}`; heart rate stores `value=Avg` with `extra={"min":..,"max":..}`; sleep analysis stores `value=totalSleep` hours with the stage fields in `extra`; reading upserts on the unique key (update value). `workouts` become `kind="workout"` events with `external_id=id`; `symptoms`, `ecg`, `heart_rate_notifications`, `state_of_mind`, `cycle_tracking`, `medications` become events of kinds `symptom, ecg, heart_rate_notification, state_of_mind, cycle, medication`, `external_id` the item's `id` if it has one else the SHA-1 of its sorted JSON. Route point arrays and ECG waveforms stay inside `payload` untouched.

- [ ] **Step 1:** Tests with invented payloads built from the documented shapes: `test_parse_date_with_offset_and_bare_day`, `test_steps_become_readings_with_the_header_grain`, `test_reposting_updates_not_duplicates`, `test_blood_pressure_heart_rate_and_sleep_shapes`, `test_null_string_and_missing_date_readings_are_skipped_and_flagged_while_the_rest_import`, `test_unknown_metric_is_kept_and_grouped_under_mind_and_other`, `test_data_wrapped_and_unwrapped_payloads_both_work`, `test_workout_symptom_state_of_mind_and_unknown_event_kinds_are_stored_with_full_payload`, `test_garbage_json_is_flagged_not_raised`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; wire `import_raw` into the receiver (replace the Task 3 stub); each export parses inside one transaction (all or nothing for its readings).
- [ ] **Step 4:** Run `pytest tests/health -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: turn Health Auto Export payloads into a clean history`.

### Task 5: Metric choices and the first-data popup

**Files:** Create `app/health/choices.py`, `app/templates/health/_choices_dialog.html`; modify `app/routers/apple_health.py`, `app/templates/base.html`; test `tests/health/test_choices.py`.

**Interfaces:**
- Consumes: `groups.SECTIONS`, `groups.RECOMMENDED`, `groups.metric_group`, models.
- Produces: `received_metrics(session, owner_id) -> list[dict]` (`{"metric","label","unit","section","recommended"}` for numeric metrics plus event kinds `symptom` and `state_of_mind`, only those actually received, grouped by section order); `undecided(session, owner_id) -> list[str]` (received metrics with no choice row); `needs_popup(session, owner_id) -> bool`; `save_choices(session, owner_id, enabled: set[str], offered: set[str]) -> None` (writes a row for every offered metric, `enabled` True only for those in `enabled`); `is_enabled(session, owner_id, metric) -> bool`. Route `POST /settings/apple-health/choices` (form fields `metric` repeated, `offered` repeated; redirects back). The dialog is the approved mockup: a switch per section and per metric, "Recommended" tag, "Turn all off", "Use recommended", "Save choices", "N of M on" count, and opens automatically on the next page load when `needs_popup` is true; also reopenable from the Settings card.

- [ ] **Step 1:** Tests: `test_nothing_received_means_no_popup`, `test_popup_lists_only_received_metrics_with_recommended_marked`, `test_saving_records_on_and_off_and_ends_the_popup`, `test_a_new_metric_later_brings_the_popup_back_for_only_that_one`, `test_choices_are_per_person`, `test_dialog_html_has_section_and_metric_switches` (render check of control names and the "Recommended" tag).
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; dialog JS is vanilla in the page's existing script style (a section switch toggles its metrics; the count updates), `<dialog>` like `_calibrate_dialog.html`.
- [ ] **Step 4:** Run `pytest tests/health/test_choices.py -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: choose which Apple Health metrics to show`.

### Task 6: The Settings, Integrations card

**Files:** Create `app/templates/health/_settings_card.html`; modify `app/templates/settings/settings.html` (replace the "Apple Health, Coming soon" card), `app/routers/settings.py` (pass card data), `app/routers/apple_health.py`; test `tests/health/test_settings_card.py`.

**Interfaces:**
- Consumes: Tasks 2 to 5.
- Produces `card_data(session, owner_id, host_url: str) -> dict` with `tokens` (active health tokens: label, prefix, last_used_at, id), `new_secret` (shown once or None), `ingest_url` (`<host_url>/api/ingest/health`), `header_line` (`Authorization: Bearer <secret>` only when `new_secret`), `last_received_at`, `reading_count`, `first_data_at`, `recent` (last 10 `HealthRaw`: time, size, status, note), `event_counts` (per kind, including the stored-only kinds). The card shows: Create token, Copy buttons for address and header line, Revoke, the numbered setup steps from the spec's Setup page paragraph (JSON, Export Version 2, Since Last Sync, Summarize Data on with hours grouping, Batch Requests on, hourly cadence, Manual Export to test; first export Previous 7 Days or a wider manual one), the reverse-proxy note (raise the body-size limit to match `HEALTH_MAX_BYTES`, exempt `/api/ingest/` from any extra login; no product named), the status line ("Waiting for first data" until something arrives), recent receipts, and "Change what is shown".

- [ ] **Step 1:** Tests: `test_card_shows_waiting_then_last_received`, `test_new_token_secret_is_shown_once_and_the_address_uses_the_request_host`, `test_event_counts_listed_for_stored_only_kinds`, `test_card_never_contains_a_domain_other_than_the_request_host` (render with a test host and assert the page text has no other hostname).
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `pytest tests/health -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: the Apple Health setup card in Settings`.

### Task 7: Daily series and the Apple tab

**Files:** Create `app/health/series.py`, `app/templates/health/apple_tab.html`; modify `app/routers/measurements.py` (`TABS` adds `"apple"`; render hook), `app/templates/measurements/index.html` (tab link shown only when the person has health data or a health token; include the new template); test `tests/health/test_series.py`, `tests/health/test_apple_tab.py`.

**Interfaces:**
- Produces `CUMULATIVE = frozenset({"step_count","walking_running_distance","flights_climbed","dietary_water","dietary_energy","protein","carbohydrates","total_fat","fiber","active_energy"})`; `daily_series(session, owner_id, metric, start: date, end: date, zone: str) -> list[tuple[date, float]]` (groups readings by the local day in `zone`; per day uses only the finest grain stored for that metric that day; cumulative metrics sum, the rest take the day's last value; days with no data are omitted); `apple_tab_data(session, owner_id, range_param, zone) -> dict` (enabled metrics with `series`, today and 7-day figures, workouts list newest first with name, start, duration, distance, energy, and symptom and state-of-mind dated lists). Charts use the existing `line_chart` macro from `measurements/_silhouette_macros.html`. Apple data is never added to the Overview Metric list.

- [ ] **Step 1:** Tests: `test_cumulative_metric_sums_per_local_day`, `test_weight_takes_the_last_value_of_the_day`, `test_two_grains_are_never_added_together` (hour and day readings for one day: only hour used), `test_a_2330_minus_0800_reading_lands_on_the_owner_local_day`, `test_only_enabled_metrics_are_charted`, `test_tab_hidden_without_data_or_token_and_shown_with_either`, `test_tab_lists_workouts_symptoms_and_state_of_mind`, `test_other_persons_data_is_not_shown`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; `?tab=apple` with the same Range dropdown as the other tabs.
- [ ] **Step 4:** Run `pytest tests/health -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: the Apple tab on the Measurements page`.

### Task 8: History read function and raw retention

**Files:** Create `app/health/history.py`; modify the app's startup (`app/main.py` lifespan) to run the purge daily; test `tests/health/test_history.py`.

**Interfaces:**
- Produces `history(session, owner_id, start: datetime, end: datetime, metrics: list[str] | None = None) -> list[dict]` (each `{"metric","start","end","value","unit","source","extra"}`, UTC ISO strings, ordered by start) and `purge_raw(session, today: datetime, keep_days: int = config.HEALTH_RAW_KEEP_DAYS) -> int` (deletes raw files and rows older than the cutoff; readings and events untouched; returns the count).

- [ ] **Step 1:** Tests: `test_history_filters_by_range_and_metrics_and_owner`, `test_purge_removes_old_raw_files_and_rows_but_keeps_readings`, `test_purge_survives_a_missing_file`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run `pytest tests/health -q`; PASS.
- [ ] **Step 5:** Commit `feat: a read function for the Apple history and 90-day raw retention`.

### Task 9: Guides, changelog, screenshots, full check

**Files:** Modify `docs/GUIDE.md`, `docs/USER_GUIDE.md`, `CHANGELOG.md`, `.superpowers/guide_seed_extra.py` and `guide_shots.py` (invented health data and 3 new shots: the Settings card, the popup, the Apple tab); create images `docs/guide/img/40-apple-card.png`, `41-apple-popup.png`, `42-apple-tab.png`.

- [ ] **Step 1:** Add a GUIDE section "Apple Health" (what it is, setup steps matching the card, the popup, the tab, the stored-only kinds, privacy: the phone posts straight to your own server) and a USER_GUIDE bullet; CHANGELOG bullet.
- [ ] **Step 2:** Seed invented readings through the real receiver, retake the three shots, and look at each image.
- [ ] **Step 3:** Run the full suite, expect all pass; run the scan on the changed text files, expect no HIT. Commit `docs: Apple Health guide and screenshots`, push, watch CI to the end and report.

### Task 10: Real sample and first live data (with the owner)

**Files:** possibly `app/health/parse.py` and its tests.

- [ ] **Step 1:** After deploy the owner creates the token, sets up one REST automation per the card, and taps **Manual Export**. Read the raw file in `data/uploads/health_raw/` from the owner's server (the owner supplies it or the status page's note) and compare with `parse.py` assumptions.
- [ ] **Step 2:** Where reality differs from the documented shapes, add a failing test built from the (anonymised) real shape, fix the parser, replace each `# Not established` comment with what was confirmed, and re-import any flagged raw exports.
- [ ] **Step 3:** When the first data has arrived, create the scheduled Claude task for 7 days later to compare the owner's logged workouts with the Apple workouts and report the differences (not Amide code; spec call 1). Commit and push, watch CI, report.

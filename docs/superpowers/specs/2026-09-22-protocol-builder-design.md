# Protocol List & Builder — Design

Date: 2026-09-22 · Status: awaiting owner review

## 1. Intent

Let the owner plan and keep peptide protocols: start from one or more **goals**, get a suggested **stack** of peptides, set their own dose and schedule for each, optionally add **titration** steps, and see every currently running protocol at a glance on **Active** cards. Protocols are kept until deleted so past ones can be repeated.

Inspiration: pepbuilder.com/builder (goal cards → suggested stack → protocol). Pricing/ordering is out of scope. Amide does **not** copy PepBuilder's stacks or library data; stacks here are authored for Amide and dose data is entered by the owner.

### Decisions made with the owner

| Topic | Decision |
| --- | --- |
| Builder flow | Selecting goals pre-selects a suggested stack (merged across goals); the owner can untick or add peptides |
| Dose data | Starts blank; the owner enters doses per protocol. Library Low/Mid/High fields exist but are empty |
| "Active" | Derived from dates plus manual Pause/End (see §4) |
| Multiple active | Yes, any number |
| Titration | A per-protocol checkbox; when on, each peptide gets its own steps. Templates come later |
| Persistence | Every protocol built is stored in the protocol database until the owner deletes it. Ended protocols are stored records (not running) that can be viewed or repeated |
| Share | Button shown but disabled ("Coming soon"); email sharing is a later feature |
| Library source | Owner's 100-card PDF. Names seed the library now; full card import is a later phase and stays in `data/` (not the public repo) |

## 2. Scope

**In:** peptide library table (seeded), goal stacks (seeded), protocols with peptides, schedules, titration steps, inventory links; Protocols page; builder create/edit/repeat; pause/resume/end/delete; disabled Share button; JSON read API for protocols; migration; tests.

**Out (later phases):** importing card details/images from the PDF; Library screens for editing peptides/Low-Mid-High doses; titration templates; dose logging / Today view; email sharing; reminders.

## 3. Data model (migration `0003`)

### Goals — fixed list in code (`app/goals.py`)

| slug | Label | Short description |
| --- | --- | --- |
| `fat-loss` | Fat Loss / Metabolic | Metabolism and body composition |
| `muscle-recovery` | Muscle & Recovery | Tissue repair and recovery |
| `gh-performance` | Growth Hormone / Performance | GH secretagogues and performance |
| `longevity` | Longevity & Cellular Health | Mitochondrial and cellular health |
| `skin-beauty` | Skin & Beauty | Skin, hair and appearance |
| `wellness` | Wellness / General Health | Immune, mood and general support |
| `glp1-weight` | GLP-1 / Weight Management | Incretin-based weight management |
| `sleep-recovery` | Sleep & Recovery | Rest and restoration |

### `peptides` (library)

| column | type | notes |
| --- | --- | --- |
| id | int PK | |
| name | str(120), unique (case-insensitive) | |
| aliases | str(300) null | comma-separated |
| card_number | int null | 1–100 from the owner's PDF; null for extras/custom |
| dose_low / dose_mid / dose_high | float null | empty for now |
| dose_unit | enum mg / mcg / IU, null | |
| typical_frequency | str(100) null | free text for now |
| notes | text null | |
| source | enum `card`, `starter`, `custom` | where the entry came from |

### `goal_peptides` (goal stacks)

| column | type | notes |
| --- | --- | --- |
| goal | str(40) | goal slug |
| peptide_id | FK peptides, cascade delete | |
| position | int | order within the stack |
| PK | (goal, peptide_id) | |

### `protocols`

| column | type | notes |
| --- | --- | --- |
| id | int PK | |
| name | str(200) | required |
| start_date | date | required, default today |
| end_date | date null | planned end; null = ongoing |
| ended_on | date null | set when the owner presses **End** |
| paused | bool | default false |
| titration_enabled | bool | default false |
| notes | text null | |
| created_at / updated_at | datetime | |

### `protocol_goals`

(protocol_id FK cascade, goal slug) — PK both. At least one goal per protocol.

### `protocol_items` (a peptide within a protocol)

| column | type | notes |
| --- | --- | --- |
| id | int PK | |
| protocol_id | FK protocols, cascade delete | |
| peptide_id | FK peptides, restrict | |
| position | int | display order |
| dose | float null, > 0 | blank allowed ("Dose not set") |
| dose_unit | enum mg / mcg / IU | default mg |
| frequency | enum: `daily`, `eod` (every other day), `every_n_days`, `weekdays`, `weekly`, `as_needed` | default `daily` |
| every_n_days | int null | required ≥ 2 when frequency = `every_n_days` |
| weekdays | str(7) null | chosen days as letters from `MTWRFSU` (R = Thu, U = Sun), e.g. `MWF`; required (≥ 1 day) when frequency = `weekdays` |
| time_of_day | enum `am`, `pm`, `bedtime`, `any` | default `any` |
| route | enum `subq`, `im`, `oral`, `nasal`, `topical`, `other` | default `subq` |
| inventory_item_id | FK inventory_items null, **ON DELETE SET NULL** | optional link |
| notes | str(300) null | |

### `titration_steps`

| column | type | notes |
| --- | --- | --- |
| id | int PK | |
| protocol_item_id | FK protocol_items, cascade delete | |
| start_week | int ≥ 1 | |
| end_week | int null ≥ start_week | null = "onward" (only allowed on the last step) |
| dose | float > 0 | same unit as the item |

Validation: steps sorted by start_week, must not overlap; only the last step may have an open end. Steps are kept (not deleted) when the Titration box is unticked, but are ignored and hidden until it is ticked again.

## 4. Status rules (computed, never stored)

Evaluated against today's date on the server, in this priority order:

1. **Ended**: `ended_on` is set, or `end_date` < today
2. **Paused**: `paused` is true
3. **Scheduled**: `start_date` > today
4. **Active**: otherwise

Actions:
- **Pause** → paused = true. **Resume** → paused = false.
- **End** → ended_on = today (the protocol shows as Ended right away). Available on Active, Paused, Scheduled.
- **Repeat** (available on every protocol) → opens the builder pre-filled as a **new, unsaved** protocol: same goals, peptides, doses, schedules, titration; name "<name> (repeat)"; start = today; end = today + original length if the original had an end date.
- **Delete** → confirm dialog, then removes the protocol and its items/steps/goals.
- **Share** → rendered disabled with tooltip "Email sharing coming soon". No backend.

"Current titration step" for a card = the step whose week range contains `floor((today − start_date) / 7) + 1`.

## 5. Screens

### `/protocols`

1. **Active protocols** — responsive card grid (1 column on phones). Each card:
   - Yellow diagonal **ACTIVE** ribbon across the top-left corner
   - Name; goal tags
   - One line per peptide: `BPC-157 — 250 mcg · daily · AM · SubQ` (or `Dose not set` in muted text); if titration is on and a step applies, show `Week 5 · step 2: 5 mg`
   - `Day 12 · started Sep 10, 2026` and `Ends Dec 3, 2026` or `Ongoing`
   - Buttons: Edit, Pause, End, Share (disabled)
   - Empty state: "No active protocols. Pick a goal below to build one."
2. **Start a new protocol** — the 8 goal cards (label + short description), multi-select with a check mark; **Build protocol** button enabled when ≥ 1 selected → `GET /protocols/new?goal=a&goal=b`.
3. **Saved protocols** — the protocol database: every protocol that isn't currently Active (Scheduled, Paused, Ended), newest first, as a compact table: name, status tag, goals, dates; actions Resume (paused), Edit, Repeat, Delete. Filter chips: All / Scheduled / Paused / Ended. Nothing leaves this list except by Delete.

### `/protocols/new` and `/protocols/{id}/edit` (builder, one page)

1. **Goals** — the 8 goals as toggle chips, pre-set from the query string / saved protocol. Changing goals updates the suggestions client-side (no reload).
2. **Peptides** — suggested stack for the selected goals: union of each goal's stack in goal order then position, duplicates removed. Suggested peptides are **pre-ticked** on a new protocol; peptides already in a saved protocol stay ticked. Below: **Add a peptide** — searchable input over the whole library; typing a name not in the library offers "Add '<name>' to library" (creates a `custom` peptide on save).
3. **Details** — one row per ticked peptide: dose + unit, frequency (+ every-N / weekday picker when relevant), time of day, route, inventory item (dropdown of all inventory items), notes. Drag-free ordering: order follows the tick order.
4. **Protocol** — name (auto-suggested from goal labels joined by " + ", editable), start date, end date **or** "length in weeks" (entering weeks fills the end date), notes, **Titration** checkbox. When ticked, each peptide row expands a steps table (Add step / remove) with start week, end week (optional on last), dose.
5. **Save** → validates server-side; on errors the page re-renders with messages next to fields (same pattern as Inventory). On success → `/protocols`.
6. Edit mode adds a header with status tag and Pause/Resume, End, Repeat, Delete, Share (disabled).

The data needed by the page's JavaScript (goal stacks, library names, inventory items) is embedded in the page as JSON; no extra API calls.

### Navigation

"Protocols" in the top bar becomes a live link. `/` keeps redirecting to `/inventory` (the dashboard comes later).

## 6. Validation (server-side)

- name required (≤ 200); ≥ 1 goal; ≥ 1 peptide
- start_date required & valid; end_date ≥ start_date; weeks ≥ 1 integer
- dose blank or > 0; unit/frequency/time/route from their lists
- every_n_days ≥ 2 when needed; ≥ 1 weekday when needed
- inventory item must exist if given
- titration steps as in §3
- new custom peptide names trimmed, ≤ 120 chars, case-insensitive de-dupe against library

## 7. Seed data (in migration `0003`, committed to the repo)

- **100 card peptides** — names and card numbers from the owner's PDF (names only), `source = card`.
- **Starter extras** not on the cards, `source = starter`: KPV, NAD+, Glutathione, LL-37, Pinealon.
- **Starter goal stacks** (authored for Amide, editable later in the Library phase):

| Goal | Stack (in order) |
| --- | --- |
| Fat Loss / Metabolic | Retatrutide, Tirzepatide, Tesamorelin, AOD-9604, MOTS-c |
| Muscle & Recovery | BPC-157, TB-500, CJC-1295 + Ipamorelin, IGF-1 LR3, PEG-MGF |
| Growth Hormone / Performance | CJC-1295 + Ipamorelin, Tesamorelin, Sermorelin, Ipamorelin, GHRP-2 |
| Longevity & Cellular Health | Epitalon, MOTS-c, Elamipretide (SS-31), Humanin, NAD+ |
| Skin & Beauty | GHK-Cu, BPC-157, KPV, Melanotan I (Afamelanotide) |
| Wellness / General Health | Thymosin alpha-1, BPC-157, Selank, Semax, Glutathione |
| GLP-1 / Weight Management | Semaglutide, Tirzepatide, Retatrutide, Cagrilintide, Liraglutide |
| Sleep & Recovery | DSIP, CJC-1295 + Ipamorelin, Epitalon, Pinealon |

The seed is inserted only if the `peptides` table is empty, so it never overwrites the owner's later edits.

## 8. JSON API (read-only)

- `GET /api/protocols` — list with computed `status`, goals, items (peptide name, dose, unit, frequency…, inventory_item_id), titration steps when enabled.
- `GET /api/protocols/{id}`
- `GET /api/peptides` — library list (used later by dose logging / calculator).

## 9. Code layout

- `app/goals.py` — goal definitions
- `app/models.py` — new models (file stays manageable; split into `app/models/` package only if it passes ~300 lines)
- `app/protocols/status.py` — pure functions: status, current week, current titration step (easily unit-tested)
- `app/protocols/forms.py` — parse + validate builder form into values/errors
- `app/routers/protocols.py` — pages, actions, API
- `app/templates/protocols/list.html`, `builder.html`; `app/static/js/protocol-builder.js`, `protocols.js`
- `migrations/versions/0003_protocols.py` (schema + seed)

## 10. Testing

- Unit: status rules (each state, priority order, End today, end_date boundary), current week/step, stack merge + de-dupe ordering, form validation (each rule above), titration overlap/open-end rules.
- Integration (TestClient): create from two goals; edit; pause/resume; end; repeat pre-fills and does not save; delete cascades; custom peptide creation and de-dupe; inventory link survives and is nulled when the inventory item is deleted; Share button present and disabled; API shapes.
- Migration: upgrade from `0002` with existing inventory data; seed only when empty; downgrade.
- Manual: desktop + phone layouts of cards (ribbon), goal cards, builder.

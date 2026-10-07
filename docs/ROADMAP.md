# Amide Roadmap

Where Amide is today, and the path from here to the full feature list in the README.

Each phase is a usable release on its own. Phases are ordered by **data dependency**: later features read records created by earlier ones, so we build the chain from the bottom up — *stock → vials → protocols → doses → everything that analyzes doses*.

---

## Where we are: v0.7 — Inventory + Protocols + Library + Daily Dosing + Dashboard + Vendor Management + Body & Health Tracking (Weight & Measurements + Journal + Labs) ✅

**Vendor Management (v0.6, shipped ahead of schedule)**
- A full `/vendors` page: structured contact/payment methods (built-in types get clickable links,
  custom types are user-addable and reusable), recommend/don't-recommend, per-user favorites,
  sortable list, a price-list attachment with a staleness prompt on reorder, and Purchase History
  scoped the same way every other page in this app is (your own data, plus anyone who's shared
  Inventory with you)
- The New Order flow now asks "new vendor or existing?" up front, and prefills each line's price
  from your own last order with that vendor for the same item — derived from existing order
  history, no separate price list to maintain

**Daily Dosing (v0.4)**
- **Today view** (`/today`): every dose due today, Log (draws the oldest-open Active Vial, deducts the volume, records time) or Skip, with a body-silhouette **injection-site picker** that highlights the last-used site and pulses the recommended mirrored side
- **Missed/late handling:** a day only counts as missed once it's fully passed; a "catch up" list of recent missed doses lives on the Protocol page and logs them as Late
- **Adherence history:** color-coded dots on the Calendar (month/week/day) and a full dose-history table on the Protocol page
- **Peptide pens:** no separate entity — an Active Vial gains a `dispensing_method` flag, set at reconstitution or converted later, with dose-logging wording adjusted accordingly

**Library (v0.3)**
- All 100 peptide cards imported (text + card image) into a searchable Library with goal / "added by me" filters
- Per peptide: your Low/Mid/High dose, frequency, aliases, notes, and goal-stack membership (editable)
- Protocol builder: type-ahead search over the whole library; "View card" per peptide
- `tools/import_cards.py` to re-import an updated PDF; `python -m app.library_load` to reload without it


**Protocols (v0.2)**
- Protocols page: **Active** protocol cards (yellow ribbon), 8 selectable **goal cards**, and **Saved protocols** (scheduled / paused / ended — kept until deleted, with Repeat)
- Goal-driven **builder**: goals → suggested peptide stack (merged across goals) → your dose, unit (mg / mcg / IU), frequency, time of day, route and optional inventory link per peptide
- **Titration** checkbox with weekly steps per peptide; cards show the current step
- Pause / Resume / End / Repeat / Delete; Share button in place (email sharing later)
- Peptide library seeded with the 100 peptide-card names + 5 starters; goal stacks seeded (doses blank for now)
- Read-only JSON API: `/api/protocols`, `/api/peptides`

**Inventory (v0.1)**

- Inventory table: name, count, vial size (mg), medium, lot/batch #, cost, vendor, order / shipped / arrival dates, COA upload (photo/PDF) with lab-measured vial size and purity, notes
- Flags items whose lab-measured amount is more than 10% below the labeled vial size
- **+ Add item** form, edit, delete, COA viewer
- Read-only JSON API (`/api/inventory`) for upcoming features to build on
- SQLite database with migrations, Docker deployment, automated tests

---

## Launch plan: going public Monday 2026-10-12 (written 2026-10-07)

Every open item in this file now carries a group letter in brackets, [A] to [J]. Each group is one sitting of work: one spec, one plan, one commit run. Finished items are marked ✅ throughout.

**Shipped since the last update (2026-10-06 to 2026-10-07):** price-list ingest (engine, Telegram watcher, forum topics, skip words, inbox filters), Orders tab with tracking, image and spreadsheet price lists, reconstitution supplies and BAC priorities, IU vials in the calculator and the teaching tool, Compliance bars on the Dashboard, Shop this protocol (with BAC water, text and email sharing), stock sheets no longer mistaken for price lists, imported lists shown on the vendor page, a larger Dashboard body diagram.

| Group | What it covers | Open items | Needs you |
| --- | --- | --- | --- |
| **A. Launch readiness** | The things a stranger hits first | ✅ Password minimum 8; ✅ HTTPS / reverse-proxy guide; ✅ CI workflow; ✅ changelog; ✅ README status rewrite; ✅ accessibility basics (skip link, landmarks, focus rings, reduced motion). **Still open:** try the Docker build (OCR libraries; needs Docker); cut the v1.0 tag; scheduled automatic backup; a user guide; a fuller accessibility pass (every form control, colour contrast); Terms and Disclaimer read-through | Final wording of any legal text; the tag |
| **B. Inventory and labels** ✅ | Stock handling | All built 2026-10-07: Local Seller flag (no late-shipment alert, left out of averages); "Use first" order; average time to arrive on the vendor card; automatic vial labels on check-in plus the "put these dates on your label" dialog | Check the label size suits your printer |
| **C. Protocol builder** ✅ | How a protocol is described | All built 2026-10-07: expanded Time of Day; titration ramp helper; printable protocol view; Vitamins/Supplements and Prescriptions cards; "runs out on" predictions; current titration step on the Dashboard. "X times per day / per week" is covered by Specific days and one item per time of day. Post-launch: conflict-checking between medicines and peptides | Check the nine time-of-day names read right |
| **D. Calendar and reminders** ✅ | Getting doses done | Built 2026-10-07: month cards (already shipped), log a dose from the calendar, the iCal subscription, ntfy reminders, installable app. **Post-launch:** browser push and email reminders | Whether ntfy is the channel you want |
| **E. Library and learning** | The reference side | ✅ Duplicate cards fix (re-run the sheet loader to merge); ✅ calculator link on each dosing tier; ✅ Peptide Learning; ✅ reorder a goal stack. **Still open, needs you:** 35 cards still in the old style, premade protocols and pre-planned stacks, the five unmatched price-list names, bare cards to fill in, KGLOW, footer wording | The premade protocols, the 35 sheets, the footer wording |
| **F. Body, labs and journal** ✅ | Health records | Built 2026-10-07: labs with every marker inline (and <5, >100, 0, negatives), historical backfill charts at once, mood/energy/sleep trends, edit or back-date a journal entry. **Post-launch:** extensible side-effect list, metric units | None |
| **G. Workouts and food** | Exercise and nutrition follow-ups | Delete or end a workout plan; show logged weight and reps; the plan editor and Schedule as one form; TDEE history; xlsx export of the workout log; free-form workouts; Food part B (live USDA search, needs a free key) and part C (meal plans, diet library, shot-day eating guidance) | A USDA API key for part B |
| **H. Vendors and spending** | Money | Cost analytics (per mg, per dose, monthly spend per peptide); the URL price-list date bug; a warehouse stock view (what a warehouse has on hand; not prices); shop plan: count BAC bottles by the 28-day rule | Whether the stock view is wanted |
| **I. Integrations and AI** | Large, outside-service work | Apple Health / Health Connect / Withings / Renpho / Hume; personal API tokens; charts correlating a metric against doses; MedGemma lab interpretation | A decision: see below |
| **J. Polish** | Cosmetic | Workouts button spacing; caution-tape rounded corners; calculator syringe art; the general color and accent pass | None |

**Recommended order, working back from Monday:** Thursday A and B; Friday C; Saturday D and the quick parts of E and F; Sunday G, H, J and the owner-supplied content; Monday morning a last full test run, the Docker build, the tag, and the announcement.

**What cannot honestly be finished by Monday, and what I recommend:** group I (the health-tracker integrations and MedGemma depend on outside services, outside accounts and privacy decisions, so each is its own project), the premade protocols and the 35 card sheets (they are content only you can supply), and food part C (a library of meal plans). Leaving those on the roadmap, labeled as open for requests, is the honest launch state. Everything else is buildable in the days available if each group gets a short spec first.

---

## The target data model

This is where the chain is headed. `InventoryItem`, `Peptide`, `Protocol` (with items) and `TitrationStep` exist today.

```mermaid
erDiagram
    Vendor ||--o{ Order : "sells via"
    Order ||--o{ InventoryItem : "received as"
    Vendor ||--o{ InventoryItem : supplies
    Peptide ||--o{ InventoryItem : "is a"
    InventoryItem ||--o{ ActiveVial : "reconstituted/opened into"
    Peptide ||--o{ Protocol : "used in"
    Protocol ||--o{ TitrationStep : "ramps through"
    Protocol ||--o{ DoseLog : schedules
    ActiveVial ||--o{ DoseLog : "drawn from"
    DoseLog }o--o| JournalEntry : "noted in"
```

Key idea: **InventoryItem** is *sealed stock on the shelf* (count = 5 vials). When you reconstitute or open one, it becomes an **ActiveVial** with its own concentration, open date, and remaining amount. Doses are drawn from active vials, which is how Amide will know how much is left and when to reorder.

---

## Phase 1 — v0.2: Inventory foundations

Firm up the base before anything depends on it.

- ✅ *Medium-aware form shipped in v0.7*: amount + unit (mg/mcg/IU), volume for Liquid, units-per-package for Autoinjector/Pill, required fields enforced per medium.
- ✅ *More stock details shipped in v0.7*: expiration date, storage (fridge / freezer / room temp).
- ✅ *Vendors as their own table shipped in v0.7*, as a pick-or-create field on the inventory form (like the peptide picker). A full standalone Vendors management page — edit a vendor's website/contact info, see everything ordered from them — is still Phase 5's *Personal Distributor Contacts*.
- ✅ *Search, sort, filter on the inventory list shipped in v0.7.*
- ✅ *Accounts, legal notice, private data per user and 2FA shipped in v0.4.*
- ✅ *Backup/export (JSON + CSV, additive-only import) shipped in v0.7.*
- ✅ *Settings page shipped in v0.8*: username/password/2FA, email, timezone, colorway, and an admin Manage Users panel, reached from the account menu; Backup & restore now lives there too.
- ✅ *Sharing shipped in v0.9*: per-person, opt-in read-only sharing of Inventory and Personal Data (Protocols), managed from Settings.

## Phase 2 — v0.3: Reconstitution Calculator + Active Vials

- ✅ *Calculator shipped in v0.6*: concentration, draw volume, U-100 units, syringe-fill visual, presets, reverse target-units solver, Inventory/Protocol prefill.
- ✅ *Active Vials shipped in v1.0*: reconstitute from Inventory or the Calculator, tracked concentration/discard-by/doses, expiry-aware Active Vials section with the real vial icon, default discard-window setting.
- **Still open [J] — remaining icon art:** the Calculator's own syringe-fill visual still uses its original CSS-drawn look; the other cropped icons (pens, pill bottle, etc.) remain unused until later phases need them.

- **Calculator:** vial mg + bacteriostatic water mL + desired dose → concentration, **units to draw on a U-100 syringe**, and doses per vial. Syringe size picker (0.3 / 0.5 / 1 mL) with a visual fill line.
- Works standalone, *or* pre-filled by picking an inventory item.
- **"Reconstitute" action:** takes 1 from inventory count → creates an Active Vial with concentration, date mixed, and a discard-by date (configurable, e.g. 28 days).
- Active vial list: remaining mg / doses, days until discard.

## Phase 3 — v0.4: Daily Dosing *(the core loop)* ✅

- ✅ *Protocols and titration steps shipped in v0.2.* ✅ Cycles like 5 on / 2 off shipped 2026-09-30 (Cycle On/Off, under Protocols below). ✅ **Titration ramp helper** (built 2026-10-07): in the builder's titration section, "Fill the steps from a ramp" turns a start dose, an increase, weeks per step and a target into the step rows. It suggests no doses of its own.
- ✅ *Today view shipped in v0.4*: doses due today, tap to log → picks the oldest-open active vial, deducts the drawn amount, records time and **injection site** (with mirrored-side rotation suggestions via a body-silhouette picker); Skip action logged separately with no vial touched.
- ✅ *Skip / missed / late dose handling and adherence history shipped in v0.4*: missed/late computed lazily (never stored as "missed"), a catch-up affordance on the Protocol page for recent missed doses, and color-coded adherence dots on the Calendar (month/week/day).
- ✅ *Peptide pen tracking shipped in v0.4*: pens are the same Active Vials, flagged via a `dispensing_method` field (no separate pen entity); reconstitution asks whether to load into a pen, and any syringe vial can be converted later.

## Phase 4 — v0.5: Quick View Dashboard ✅

- ✅ *Calendar (month / week / day views of scheduled doses) shipped, now with adherence color-coding since Daily Dosing (v0.4).* ✅ Logging from the calendar (built 2026-10-07): a dose due today and not yet logged shows "Pick site & log dose" in the click-through dialog, which jumps to that dose's row on the Today page.
- ✅ *Dashboard shipped in v0.5* (spec: `docs/superpowers/specs/2026-09-26-dashboard-design.md`) — the new homepage: a Today's Schedule summary linking out to the real `/today` page, Alerts (low stock — per-item threshold, defaulting to a user-set number; vial/BAC/sealed-stock expiration, both "soon" and already-expired; shipment running long), a Cost snapshot (cost per vial/dose from existing order data), an Adherence snapshot (% on-time/late over the last 30 days, counting genuinely-missed doses against it), and a single-person **viewer switcher** for anyone who's shared data with you (never blended — one person's data at a time, gated per the existing Inventory/Personal-data share categories). This covers the "today's doses," "low stock," "vials nearing discard," and "adherence streak" bullets below.
  - **Deferred out of this build, for later:** per-widget show/hide toggles (shipped as one fixed layout first; toggles are a fast-follow once it's been used for a while); per-vendor historical shipment-time averaging (needs Phase 5's still-open Vendor management page to store it — v1 uses a flat, user-adjustable day-count default instead); the Dashboard's own Weight & Measurements and Journal placeholder cards became real (the Weight/Measurements one still just links out, but the Journal one is now the quick-capture box) once Phase 6 shipped those features; a Health-integration widget stays a **placeholder card only** until Phase 9.
  - ✅ *Resolved by the Compliance bars (2026-10-06): only completed days count, today counts once done.* (Old note: the Adherence snapshot slightly over-penalizes a brand-new protocol's very first due day — a not-yet-logged dose due *today* counts against the percentage instead of showing "no data yet" until the day is over (the rest of the app treats a dose as loggable-but-not-yet-missed all day). Cosmetic only, no data-isolation or correctness risk; worth excluding today from the count when convenient.)
- ~~Today's doses and what's already done~~ / ~~Low stock~~ / ~~Vials nearing their discard date~~ / ~~Adherence streak~~ — covered by the Dashboard above.
- ✅ "Runs out on…" predictions (built 2026-10-07): the Inventory table has a Runs out column (sealed vials plus what is left in open ones, against what active protocols use at their current titration step), and the Dashboard warns when stock will be gone within 14 days with nothing on order.
- ✅ Current titration step on the Dashboard (built 2026-10-07: "Step 2 of 3" beside each dose in Today's Schedule).
- ✅ Subscribe to calendar on device (built 2026-10-07): Settings makes a private iCal address; every dose due in the next three months appears at its time of day with a pop-up alert; a new address cancels the old one, and the feed can be turned off.
- ✅ **Reminders via [ntfy](https://ntfy.sh)** (built 2026-10-07; opt-in, off by default): a push message when a dose's time of day arrives and it is not logged, in the user's own time zone, once per dose per day. It is the one outside call Amide makes on its own (`AMIDE_NTFY_SERVER` picks the server). **Not built, post-launch:** browser push notifications and email reminders (email needs mail-server settings).
- ✅ **Installable phone app (PWA)** (built 2026-10-07): manifest, icons and a small service worker (it stores nothing; it only shows an offline note), so the browser offers Add to Home Screen. Needs HTTPS or localhost to install; see docs/DEPLOYING.md.

## Phase 5 — v0.6: Orders & Distributor Contacts ✅ (core scope)

- ✅ *Order tracking shipped, ahead of schedule, alongside the v0.7 Inventory work*: multi-item `Order`/`OrderItem` model — order/shipped/arrival dates, tracking site + number, vendor, per-line quantity/cost/lot/expiration/COA, shipping & tax allocated across lines for a true per-vial cost.
- ✅ *Receiving an order creates inventory automatically, shipped alongside the above*: filling in an order's arrival date is the "checked in" action; each line's `received_quantity` (editable down for anything short or damaged) is what counts toward `InventoryItem.available_count`, with COA carried over per line.
- ✅ *Vendors as their own table shipped in v0.7* (see Phase 1) — name, website, notes, pick-or-create from the Order/Inventory forms.
- ✅ *Personal Distributor Contacts (standalone Vendor management) shipped in v0.6* — a full `/vendors` page: structured, extensible contact methods (Email/WhatsApp/Telegram/Phone + user-addable custom types; built-ins render as clickable `mailto:`/`tel:`/`wa.me`/`t.me` links) and payment methods (Credit Card/Cash/Crypto/Alibaba + user-addable), a recommend/don't-recommend flag (the "rating field" from the older wishlist line, kept simple per an explicit decision), a per-user **Favorite** that pins to the top of the list, sortable alphabetically or by most-recent-*visible*-order-date, a reference price-list attachment (file — now including `.doc`/`.docx` — or a URL) with a "still current?" staleness prompt shown when starting a new order from that vendor, and a **Purchase History** on each vendor's page scoped like every other page in this app (your own orders, plus anyone who's shared their Inventory with you — never a global cross-user view). The New Order flow itself now asks "is this a new vendor?" up front: yes gives blank fields for a full profile entered inline; no gives a dropdown of every existing vendor and prefills each line's price from the last time you (or someone who's shared with you) ordered that same item from that same vendor — no separate price list to maintain, it's derived straight from order history.
  - **Known follow-up [H] (non-blocking, narrow):** editing a vendor's other fields (name, notes, etc.) through the edit form can incorrectly bump a URL-based price list's "last verified" date even when the URL itself wasn't touched, because the form pre-fills the field with its current value and the save path doesn't compare against what was there before. Doesn't affect file-based price lists or the order-flow's own staleness check; just means the edit-page date can occasionally look fresher than it really is. Small, well-understood fix (compare the posted value against what was already stored, only bump the date when it actually changed).
- **Cost analytics [H]:** cost per mg, cost per dose, monthly spend per peptide — not started (a different, peptide-scoped slice of this phase; out of scope for the Vendor page above).

## Phase 6 — v0.7: Body & Health Tracking

- ✅ *Weight & Measurements shipped* (spec: `docs/superpowers/specs/2026-09-28-weight-measurements-design.md`) — scale weight, blood pressure, and 7 tape-measure points (neck, biceps L/R, forearms L/R, waist, hips, quads L/R, calves L/R) logged per session, all fields optional; a body silhouette showing each measurement's current value and change since the last time that field was logged (bilateral fields shown as their average, missing sides never silently averaged with zero); a Macros/TDEE calculator (Mifflin-St Jeor + activity multiplier + goal offset, with a safe-floor clamp and visible adjusted-notice) reading a new Settings body-profile section (sex, birth date, height, activity level, goal, diet preset); a water-intake goal (half bodyweight in oz, editable) broken down into cups/bottles-per-hour pacing; BMI and US Navy-method body-fat % computed at read time; hand-drawn SVG trend charts with a 7-day-to-lifetime range selector; sharing via the existing Personal Data category.
  - **Follow-up shipped:** the body silhouette's outline is now a real content-trace of user-provided reference art (male/female front-view outlines), not a hand-drawn blob — each measurement point sits on its real anatomical location with a leader line to its value/delta label, replacing the old disconnected plain-text list.
  - **Deferred out of this build:** ✅ actual water-intake logging (shipped 2026-09-30, Phase 10); ✅ protein/fiber targets and logging (shipped with Food tracking, 2026-10-06); metric units (post-launch: it touches every screen that shows pounds, inches or ounces); ✅ mood/energy/sleep trend charts (see Journal below).
- ✅ *Journal shipped* (spec: `docs/superpowers/specs/2026-09-28-journal-design.md`) — daily entries (mood/energy/sleep, each 1-5; a 10-item side-effect checklist plus free-text "other"; a free-text notes field), one entry per day with same-day resubmission editing in place (never a duplicate); a Dashboard quick-capture box for timestamped notes through the day, auto-creating that day's entry and folding in underneath the main entry when later viewed or edited; a read-only, query-time view of that day's logged doses (no stored relationship) so you can see what you took alongside how you felt; sharing via the existing Personal Data category, same as Weight & Measurements.
  - ✅ Mood/energy/sleep trend charts (Journal tab, Trends) and back-dating or editing a past day's entry (an Edit link on each entry and a day picker; built 2026-10-07). **Still open, post-launch:** a user-extensible side-effect list (the free-text Other box covers it for now).
- ✅ *Labs & Medical Results shipped* (spec: `docs/superpowers/specs/2026-09-28-labs-design.md`) — the third and final Phase 6 sub-project. Bulk entry of blood-marker results per panel/draw: a 31-item curated marker dropdown (hormonal/metabolic/lipid/thyroid/liver-kidney/CBC/other) plus a user-addable "Other" marker, a repeatable-row form for entering several results in one sitting, each with its own user-entered reference range (ranges vary by lab, so none is built-in) and an "out of range" flag computed at display time only when both bounds are present; an optional PDF/image report attachment per panel, reusing the existing COA-upload convention (content-sniffed, not just filename-trusted); per-marker hand-drawn SVG trend charts (own panels only, never mixed with a sharing partner's) with the same 7-day-to-lifetime range selector as Weight & Measurements, plus a shaded reference-range band when every point in that chart has one; a read-only, query-time view of which protocol(s)/doses were active on each panel's own draw date (reusing Journal's `doses_for` helper); sharing via the existing Personal Data category. Completes the Labs tab, the last placeholder on the Weight & Measurements page — **Phase 6 is now fully shipped.**
  - ✅ (found already handled when checked, 2026-10-07: the field is flagged inline and a test pins it) Old note: an over-length `unit` value is correctly rejected server-side (422) but only shows the form's generic error banner rather than an inline per-field message, since the dialog's error-reopen JS wasn't wired up for that one specific field. Small, well-understood fix.

## Phase 7 — v0.8: Exercise

- ✅ *Exercise shipped* (spec: `docs/superpowers/specs/2026-09-29-exercise-phase7-design.md`) — Workout Plans built either manually or imported from a Muscle & Strength PDF (best-effort parsing that never rejects an upload, even an unreadable file — worst case, a 0-day plan you fill in by hand); a shared review/edit screen where days and exercises can be added, removed, and edited in place, reconciled by row id on save so an unrelated edit (fixing a typo, renaming the plan) never wipes a day's schedule or its logged history; weekday scheduling via checkboxes; exactly one Active plan at a time (activating a new one ends whichever was Active); completion logging per exercise (checked, weight, reps) from any day at any time, pre-filling from an existing log when re-opening an already-logged date; due workouts surfaced on Today (clearing once logged) and a plain marker on the Calendar month view; completed workouts folded into the Journal tab alongside that day's doses; a standalone Fitness Test (Max Push-ups/Sit-ups/Bodyweight Squats, Plank Hold) retakeable anytime with a per-exercise trend chart and an independent 28-day retest suggestion per exercise.
  - **Deferred out of this build:** a calorie-per-rep dataset (explicitly out of scope, noted in the spec as a future idea); a 7-day-to-lifetime range selector on the Fitness Test charts (they show the lifetime trend only); metric weight units beyond lb/kg toggle already built.
  - **Known follow-ups [G] (non-blocking):** no delete/end-plan route yet (a plan can be deactivated but not removed, and uploaded PDFs aren't cleaned up from disk); logged weight/reps are write-only today — no page displays them back yet, only completion counts; logging a workout from the plan editor's own "Log" link redirects to Today rather than back to the editor; the plan editor and its Schedule form are still two separate forms, so a day added in the editor doesn't appear under Schedule until the editor is saved first.

- ✅ **Workout calories, TDEE and progress charts (built 2026-10-06)** (spec: `docs/superpowers/specs/2026-10-06-workout-calories-tdee-design.md`; plan: `docs/superpowers/plans/2026-10-06-workout-calories-tdee.md`) — every logged exercise gets an estimated calorie burn from the owner's exercise workbook (230 exercises, aliases, style profiles and the 2024 Compendium of Physical Activities, shipped as `app/workouts/exercise_data.json`, regenerated by `tools/build_exercise_data.py`). The Journal tab has a **Log Workout** button; a logged workout shows in that day's Journal with its estimated kcal, and a day with a workout but no entry gets its own row. Plan exercises are matched to the database by fuzzy logic (confident matches are applied, weak ones are suggestions a person confirms on the plan editor); exercises can also be added on the day. The log form takes **exact** sets and reps (never a range) plus body weight (from the latest weigh-in, editable); walking, jogging and running take speed or incline and use the Compendium's own MET rows. **History survives plan changes:** logs keep a snapshot of everything their estimate used, and removing a plan day or exercise no longer deletes what was logged; the form offers last time's numbers when the same exercise appears in a later plan. A new **Energy** tab (Workouts) reproduces tdeecalculator.org (four BMR formulas, the BMR/activity/food-digestion split, goal ladder, macro grid, TDEE by activity level, life-stage adjustments) and charts each day's estimated workout burn stacked on the TDEE baseline against the goal target; a **Progress** tab charts per-exercise top load and volume, weekly volume by body area, burn by equipment, the top burners and personal records. Rules worth remembering: **every MET is a Compendium number** (reworked 2026-10-06): each exercise carries its Compendium code and MET (`tools/compendium_data.json`, read from pacompendium.com, is the source; the workbook supplies exercises, aliases, seconds per rep and rest), a rep-based row can be switched to another resistance category on the log form, and treadmill walking/jogging/running, incline walking, stationary bikes and rowing (by watts) and the elliptical and ski ergometer (by effort) use the Compendium's own tables; the Compendium has no per-lift values, so lifts use its resistance-training categories and are labeled "mapped estimate"; calorie numbers are estimates and labeled so; per-row input limits: sets 1-50, reps 1-200, implements 1-9, minutes up to 600, watts up to 2000.
  - **Known differences from the reference calculator:** it labels PCOS "-6% BMR" but applies no change, Amide applies the rule it states; its "max fat metabolism" row is shown as unavailable; fat mass and waist-to-height fill in only when tape measurements exist.
  - **Follow-ups [G]:** a TDEE history that follows weight changes over time (the chart uses the latest weight for every day); an xlsx export of the workout log in the workbook's layout; workouts with no plan day (free-form).

- ✅ **Encrypted backup, export, share and restore (built 2026-10-06)** (spec: `docs/superpowers/specs/2026-10-06-backup-restore-design.md`; plan: `docs/superpowers/plans/2026-10-06-backup-restore.md`) — Settings → Backup & restore has three tabs. **Back up** makes an encrypted `.amidebackup` file (AES-256-GCM, scrypt passphrase, at least 8 characters, no recovery) of chosen sections, always a new dated file; the administrator can also back up the whole installation (every account, vendors, price lists, library and all uploaded files). **Export / Share:** an Export carries chosen sections to a new computer; a Share file offers only Vendors, Price lists, Library, Inventory, Workouts and Protocols and strips personal records (dose logs, workout logs and fitness tests, sales and vials in use, wallets, accounts, profile, measurements, journal, labs). **Restore / Import:** open a file with its passphrase, see what is inside, then load sections each as **Add** (keep what is there, skip duplicates) or **Replace** (the preview says how many current rows would be deleted); links to things outside the loaded sections are found again by name, and unresolved ones are reported. Shared sections load only for the administrator and only as a merge by name. **Restore everything** (administrator, whole-installation file) saves a safety backup in `data/backups` first (newest three kept), needs the typed word RESTORE, runs in one transaction and signs everyone out. Attached files (COAs, lab reports, workout PDFs, wallet QR images, vendor price-list files, library cards) travel with their sections. Settings: `AMIDE_MAX_BACKUP_MB` (default 512). The older plain JSON export/import and inventory CSV remain under "Older formats". Known limits: the file is built in memory; scheduled backups and cloud destinations are not built; a backup from a newer Amide is refused.

- ✅ **Body photos (built 2026-10-06)** (spec: `docs/superpowers/specs/2026-10-06-body-photos-design.md`; plan: `docs/superpowers/plans/2026-10-06-body-photos.md`) — a private **Body photos** section on Weight & Measurements (with an Add body photo link in the Journal tab). Photos are blurred **on the server** until revealed (click, or an authenticator code when the new **Body Recomp Photo 2FA** setting is on: a correct code clears the blur for 10 minutes, per browser session, and ends on logout, Lock now or expiry). The setting reuses the account's 2FA, needs a code to turn off, blocks removing account 2FA while on, and turns itself off if an administrator or the command line resets 2FA. Uploads lose GPS/camera metadata, iPhone HEIC is converted, large photos are shrunk to 2000 px. Only the owner can fetch a photo (everyone else, including the administrator and people you share with, gets 404). Photos are in your own backup and Export, never in a Share file, and their files are removed with the photo or the account. Not built: editing a photo's date or label (delete and re-add), comparison views, a separate photo-only authenticator.

- ✅ **Food tracking, part A: core (built 2026-10-06)** (spec: `docs/superpowers/specs/2026-10-06-food-tracking-design.md`; plan: `docs/superpowers/plans/2026-10-06-food-tracking.md`) — the **Food** tab replaces the Macros tab on Weight & Measurements (`?tab=macros` redirects). Pick a **diet type** (Balanced, High protein, Low carb, Keto, Custom) and a **goal**; the tab shows the day's **calorie limit** (TDEE plus the goal offset, with the safe-floor notice), eaten and remaining, the **potential deficit** (TDEE + logged workout burn - eaten, with an estimated pounds per week and a surplus when it is negative), a **macro pie chart** for the chosen diet type, and **fulfilment bars** for calories, protein, carbs, fat and fiber (fiber target about 14 g per 1,000 kcal). Log food in four meals: find a food, create one, or quick-add; edit servings; delete. **My foods** are private; a built-in **starter list** of about 280 simple everyday foods (USDA SR Legacy, public domain, built by `tools/build_starter_foods.py`) is read-only and can be copied into My foods. Every entry stores a **snapshot** of its numbers, so editing or deleting a food never changes history. One calorie number: the Food tab uses the Energy tab's TDEE (the tdeecalculator.org clone), so the old Macros calculation, which differed slightly, is gone. Food is in the owner's own backup and Export, never in a Share file, and is removed with the account. **Not built yet [G]:** part B (live USDA search, needs a free API key and internet) and part C (meal plans built from simple foods, a library of ready-made diet plans, and GLP-1 / Retatrutide shot-day eating guidance written in original words, with the owner's three reference pages linked rather than copied).
- ✅ **Price list ingest, part A: the engine inside Amide (built 2026-10-06)** (spec: `docs/superpowers/specs/2026-10-06-price-list-ingest-design.md`; plan: `docs/superpowers/plans/2026-10-06-price-list-ingest.md`) — a token-protected intake (`/api/ingest/...`, administrator-created tokens shown once and stored as hashes, 60 requests a minute, size and count caps) takes price lists posted in chat groups: PDFs (text and scanned), photos (one list over several photos), spreadsheets and typed prices. The file naming convention is no longer needed: the **vendor** comes from the group's mapping, the **warehouse** from the list text, then caption or filename words, then the group's default (else China, flagged), and the **date** from the text when within 45 days of the message. A list that is confident (at least 5 rows, 80% priced, warehouse not assumed, not older than the current list, prices within 0.5x to 2x of the current list, not a duplicate) imports by itself and can be **undone**; anything else waits in the **Price list inbox** (Settings, Admin) where the administrator edits vendor, warehouse and date, approves, rejects or downloads the original. The dashboard gets two alerts: "<VENDOR> released new price list." (everyone, dismissable, gone after 7 days) and "<VENDOR> Telegram group is no longer active." (administrator only). The inbox is in the installation backup; tokens are not. **Not built yet:** part B, the watcher program on the owner's computer that logs in to Telegram and calls this API (own spec).
- ✅ **Price list watcher, part B (built 2026-10-06)** (spec: `docs/superpowers/specs/2026-10-06-price-list-watcher-design.md`; plan: `docs/superpowers/plans/2026-10-06-price-list-watcher.md`) — `watcher/`, a separate program for the owner's computer (Telethon plus httpx, not part of the web app or Docker image). It signs in with the owner's own Telegram account (read only), registers every group by title so it can be mapped in the Price list inbox, reads only the groups Amide lists as enabled and mapped (backfill 7 days once, then from the last message), sends text, PDFs, photos, spreadsheets and albums through a crash-safe disk queue with growing retry waits, and reports a group gone only after two polls in a row (and active again on recovery). Commands: `login`, `run`, `once`, `status`, `install-startup`, `remove-startup`. Credentials and session live only in `%APPDATA%mide-watcher`. Everything above the Telegram connection is tested with fakes; the real Telegram class needs the owner's one-time manual check (`login`, `status`, `once --days 2`).
- ✅ **Ingest topics and skip words, part C (built 2026-10-06)** (spec: `docs/superpowers/specs/2026-10-06-ingest-topics-design.md`; plan: `docs/superpowers/plans/2026-10-06-ingest-topics.md`) — for Telegram forum groups (a group split into named topics), the watcher registers each group's topics and reads only the ones ticked in the Price list inbox (**Only selected topics**); a new topic named like the group's **auto-follow words** (default `price, prices, pricing, pricelist, warehouse`) starts ticked, and Amide re-checks every message's topic. The topic name is a warehouse clue ("US warehouse"), and per-group **skip words** (for example `UK, EU`) set aside lists whose caption, filename, topic name or text mention them, unread and without keeping their text. The Groups table has a search box. Groups without topics behave as before. The real Telegram topic calls need the owner's manual check on one forum group.

## Phase 8 — v0.9: Peptide Library & Learning

- ✅ *Card import, Library screens and owner-editable doses/goal stacks shipped in v0.3.* ✅ Peptide Learning and reordering a goal stack (both built 2026-10-07; see below).

- **Library:** a reference entry per peptide (aliases, common vial sizes, storage, typical reconstitution, half-life, notes, sources). Starts from a small seed file you can extend; inventory and protocols link to it.
- ✅ **Learning (built 2026-10-07):** each person's private notes and saved articles (title, web link, text) on a peptide's library card, all listed and searchable at Library, My notes; part of the person's backup. **Goal stacks:** Library, Goal stacks lists each goal's suggested peptides with up and down buttons.
- Needs care on **sourcing and legal wording**. Everything framed as reference, never as dosing advice (consistent with the README's legal notice).

## Phase 9 — v1.0: Integrations & Polish

- **Health trackers [I] (open; large, outside-service work).** How realistic each one is:
  - *Apple Health* has no web API. Realistic options: import Apple Health's `export.zip`, or an **iOS Shortcut** that posts data to Amide's API. (Possibly https://www.healthyapps.dev/  or some sort of Webhooks app?)
  - *Google Health Connect* is on-device only (same approach: companion Shortcut/app or file import).
  - *Withings, Fitbit, Oura, Hume*: check each for an available cloud API; OAuth connectors where possible.
  - *Renpho Smart Scales* (added 2026-10-03): feed weight, body fat %, and BMI into the Measurements page automatically. Renpho's app is cloud-based and no official public API is known (verify before building). Candidate routes, safest first: (1) rely on the Renpho app's own sync to Apple Health / Health Connect and reuse the Shortcut or export-import path above; (2) import a CSV export from the Renpho app; (3) an unofficial cloud API, which would need the user's Renpho login and could break without notice.
  - Requires **personal API tokens** in Amide.
- **Vendor price lists and the price analyzer.** *Import built 2026-10-05:* `tools/import_price_lists.py` reads price-list PDFs (vendor, optional warehouse and date come from the filename `<Vendor> - [<Warehouse> ]Price List - <date>`), creates the vendor, and stores every product line: code, product, vial size, pack size, pack price, and whether it is a **kit** (exactly 10 vials) or a **box** (fewer), plus extra price tiers and the vendor's shipping wording. A list that names no warehouse is assumed to be China (recorded as assumed). Price history is kept; the newest-dated list per vendor is current the moment it is imported. Amide stores only the parsed data, never the PDFs. **Vendors and price lists are held only in the local database and are never put in the repository (legal).** *Surfaces built 2026-10-06:*
  - ✅ **Vendor-card price chart (built 2026-10-06):** the vendor page's "Price History" card has a drop-down of every product the vendor lists and charts the price per kit or box over time, one colored line per vial size (the same size keeps its color on every product; a vendor with China and USA lists gets a dashed USA line); hover a point for the per-vial price.
  - ✅ **Library-card price range (built 2026-10-06):** each library card shows a per-vial price range (for its most commonly listed vial size, across the current lists, e.g. `10mg · $5.50 – $12.00 per vial · 7 lists`) at the far right end of the Half-life row.
  - Design: `docs/superpowers/specs/2026-10-06-price-analysis-surfaces-design.md`; plan: `docs/superpowers/plans/2026-10-06-price-analysis-surfaces.md`.
  - **Naming rule (owner):** `W/` means *with* and `W/O` means *without* (so `W/DAC` is with DAC, `W/O DAC` is without DAC); a vendor whose name differs from another only by "Peptide" versus "Peptides" is the same vendor.
  - ✅ **Dashboard "NEW PEPTIDE ALERT" (built 2026-10-06):** `NEW PEPTIDE ALERT <peptide> — <vendor or vendors>` for each product in a current price list that matches no library card (mg / mcg / IU products only). Adding a card or an alias clears it; an administrator can Ignore a product that is not a peptide. The first five show, the rest sit under "Show N more".
  - ✅ **Upload on the vendor page + OCR (built 2026-10-06):** saving a vendor with a price-list PDF reads and imports its prices (warehouse: detect from the file, China if it says nothing, or chosen; date defaults to today) and shows a summary on the vendor page; the importer no longer depends on the filename for this path. A PDF that is only a picture (a scan) is read by RapidOCR (`app/library/price_lists/ocr.py`, packages pinned in `requirements.txt`, models bundled so it works offline): words with positions are rebuilt into a table by their place on the page and read by the normal reader. Tables headed "price / kit" with bare doses are read too.
  - ✅ Standalone image price lists (JPG/PNG/WEBP/HEIC) and spreadsheets (xlsx) are read too (built 2026-10-06, vendor page upload and the price list inbox). **Open [A]:** the Docker image gained two system libraries for OCR and has still not been built and tried.
  - *First import, 2026-10-05:* 11 price lists, 1,313 product lines (1,096 matched to a library card; 1,292 kits, 6 boxes, 15 with no pack size stated), 9 vendors created and 1 existing vendor reused. 7 of the 11 lists named no warehouse and were recorded as an assumed China. One scanned PDF, 6 image lists and 1 spreadsheet were skipped. 117 distinct products matched no library card (liquids, blends and a few peptides): these are the candidates the new-peptide alert will surface.
- Charts correlating any metric against doses/protocols — open [I]
- ✅ Multi-user / household support — shipped (accounts v0.4, sharing v0.9)
- ✅ Themes (colorways, v0.8). Open [A]: accessibility pass, full documentation

---

## Phase 10 — v1.1: Beautification (all specific items shipped; one open-ended item deferred)

A dedicated visual-polish pass, pulled together from items across the owner's 2026-09-29 wishlist that are about *how things look* rather than new capability. Every concretely-scoped item below is done; only the vague "General" catch-all at the bottom remains, deliberately deferred until there's a specific page/pain-point to point at.

- ✅ *Dashboard shipped, 2026-09-30* — every widget (Schedule, Alerts, Cost snapshot, Adherence, Water goal, Journal, both placeholders) now renders as its own `.card` panel in a responsive grid instead of stacking as plain full-width sections; each panel gets a colored accent stripe + line-icon in its header, Adherence/Water goal show their number as a big stat, and each panel's title links through to its own page.
  - *Prerequisite fixed 2026-09-30 (found while fixing the Charts item's own dull rendering):* `.card`/`.dashboard-placeholders`/`.measurement-chart` had zero CSS anywhere -- every chart across Measurements, Labs, Fitness Test, and this Dashboard was a bare, unstyled `<h3>` + SVG on the page background. `.card` now has a real surface/border/shadow and chart lines use `--accent` instead of the plain text color.
- ✅ *Dashboard "pop" pass shipped, 2026-09-30* — a real water-intake log (`water_logs` table) backs the Water goal panel: a "+ Log water" popup (three quick-pick amounts plus a custom field) and the panel's own background now fills like a glass toward the day's goal. A new "Body" panel puts the Measurements page's Weight chart and Body Silhouette on the Dashboard (fixed to the default range/metric, not the interactive picker). A "Workouts this week" panel shows a Mon-Sun strip (rest/upcoming/done/missed per day, today ringed) instead of a "due today" item that would vanish the moment an early workout got logged. The whole grid was then rebuilt as an explicit named-area layout, per the owner's own exact arrangement (Schedule/Alerts, Workouts/Adherence, Water/Cost, and Journal/Weight each paired left-right around a big centered, enlarged Body card, Integrations dropped to its own low-key row at the bottom) — catching and fixing a page-wide CSS rule (`section + section` margin) that had been silently throwing the pairs' alignment off.
- ✅ *Library shipped, 2026-09-30* — the dosage-tier badge (Beginner/Intermediate/Advanced) gets a colored dot + text (green/yellow/red via the `--tier-*` tokens), and Side Effects/Contraindications/Drug Interactions each get a colored left border + tinted heading — *the dosing-tier coloring was originally flagged during the peptide-sheet-import spec as "for the Library Redesign phase," which shipped without it; this is that dropped item, landed a little earlier than the rest of Phase 10 and confirmed still correct here*
- ✅ *Calendar shipped, 2026-09-30* — status (On time/Late/Missed/Upcoming) is now the dominant color everywhere instead of a small dot: Month's day marks are full colored bars, Week's marks are colored banners, Day's cards get a colored left-edge accent, and the click-through dialog's top edge matches the clicked occurrence's status. A shared legend sits above all three views. Protocol identity (previously the mark's background color) now comes from the initials/name text already shown, since status took over the bar/banner/edge itself.
  - ✅ *Site-picker art replaced, 2026-09-30* — the injection-site picker's own hand-coded "blob" path is gone; it now reuses the same traced, sex-aware body outline (`_silhouette_shape()`) as the Measurements page, with the 8 site dots repositioned onto the real figure.
- ✅ *Overview chart shipped, 2026-09-30* — a single chart driven by a metric dropdown (every measurement field, BMI, Body fat %, Blood pressure, plus new Heart Rate tracking) sits beside the Body silhouette at the top of the Measurements page, switching client-side with no round-trip; the existing 7-day-to-lifetime range selector now drives it too. The full grid of every metric's own small chart stays below, unchanged, for at-a-glance scanning.
- ✅ *Body silhouette interactivity shipped, 2026-09-30* — all 7 labels moved to one right-hand column; hovering a point shows a custom tooltip with the last two measurements and the delta; clicking jumps the Overview chart to that body part's history (a new averaged "<Location> (avg)" option for the 4 bilateral locations, so an averaged point jumps to the same average it's showing, never an arbitrary side).
- ✅ *Inventory shipped, 2026-09-30* — "New order" now matches "+ Add item"'s styling (btn-primary + icon), with the two floating buttons stacked on mobile instead of overlapping.
- ✅ *Body Outline entry forms shipped, 2026-09-30* — measurement fields now sit label-beside-input with a narrower box (scoped to this one form, not the app-wide field style); the day-range control is a real dropdown on both the Measurements and Labs sub-tabs.
- **Workouts page button spacing (open [J], 2026-10-03):** the gap between the "Choose File" control and the Upload button on the Import-a-PDF form is too wide (the native file input's "No file chosen" text takes the space). Layout is currently inline-styled flexbox; replace with a proper CSS class and tighten that gap.
- **Library caution-tape rounded corners (open [J], 2026-10-03):** the yellow/black stripe on the left edge of Side Effects / Contraindications / Drug Interactions cards works (`.lib-section-caution`, `border-image`), but `border-image` ignores `border-radius`, so the stripe has square corners against the card's rounded ones. Find a way to round them (e.g. mask/clip-path or a layered gradient instead of `border-image`).
- **General (deferred [J], 2026-09-30):** a color/accent pass across the app, tighter visual grouping — the owner's own "overall beautification" note. Left open on purpose: every other Phase 10 item named a specific page/feature to fix; this one didn't, so it's parked until there's a concrete target instead of a vague pass.

---

## Cross-cutting work (ongoing, alongside the phases)

| Area | Plan |
| --- | --- |
| **CI** | ✅ GitHub Actions workflow built 2026-10-07 (`.github/workflows/ci.yml`: tests on every push and pull request, Docker image built every push and published to GitHub Container Registry on `v*` tags). *First run on GitHub not yet seen.* |
| **Releases** [A] (changelog ✅ `CHANGELOG.md`; the v1.0 tag is still to cut) | Semantic versions (`v0.2.0`…), changelog, migrations always forward-compatible |
| **Backups** | ✅ Encrypted backup, export, share and restore shipped 2026-10-06. Open [A]: scheduled automatic backup, restore tested in CI |
| **Security** | Accounts + 2FA + lockout + cross-site form protection (done, v0.4), upload validation (done). ✅ Minimum password length raised to 8 and reverse-proxy / HTTPS guidance written (`docs/DEPLOYING.md`, 2026-10-07) |
| **Data ownership** | Full export at any time in open formats (JSON/CSV); no telemetry, no external calls unless you enable an integration |

---

## Requested enhancements (owner's To Do list, 2026-09-29)

Raw wishlist items, organized by app area to match the source list. Not yet spec'd — these are candidates for future brainstorming/spec/plan cycles, not committed scope.

**Dashboard**
- ✅ *Each card clickable through to its page, shipped 2026-09-30 — see Phase 10*
- *(Grafana-style visual rework and layout pass moved to Phase 10 — Beautification)*

**Inventory**
- ✅ (built 2026-10-07) New-item "Local Seller" checkbox — excludes shipping-time math for that item
- ✅ (built 2026-10-07: default "Use first" order, with Expires and Arrived columns and a Name option) Sort Peptides/Medicines by Expiration, then FIFO by Arrived Date (oldest stock first)
- ✅ BAC Water: vial size (mL), COA (through the order line), and an opened BAC bottle becomes a 28-day room-temperature open vial, ranked by priority (built 2026-10-06, reconstitution supplies)
- ✅ 28-day clock starts on reconstitution (shipped, Active Vials). ✅ the "Put These Dates on Your Labels" popup (built 2026-10-07, with a one-vial label to print) (recon date + 28-day expiry) at reconstitution time *(extends the existing reconstitution flow, Phase 2 ✅)*
- ✅ (built 2026-10-07) On order check-in, offer to print vial labels (name, concentration, batch, blank recon/exp date boxes, "Research Use Only")
  - ✅ **Automatic printing built 2026-10-07:** checking in an order opens one label per received peptide vial and the browser's print window; Settings, Vial labels turns it off and picks the size (Avery 5160 sheet, 2 x 1 in or 4 x 2 in roll; the first is an assumption: change it if you use another). A web page cannot print silently, so the print window is the one extra click.

**Vendors**
- ✅ (built 2026-10-07) Average shipping time on the vendor card — *its own blocking dependency (Phase 5's Vendor management page) has since shipped in v0.6; this is now buildable*
- ✅ Add/remove a vendor card from the list (shipped, Vendors page)

**Protocols**
- ✅ Vitamins/Supplements card and Prescriptions card (built 2026-10-07: two more goal cards; file your own library entries under them). Still open, post-launch: conflict-checking between meds and peptides
- ✅ Multi-goal selection for a single protocol (shipped in the builder: goals merge into one stack)
- ✅ Frequency: day-of-week picker ("Specific days"), every N days, weekly, as needed (shipped). ✅ "X times per week" is the Specific days picker and "X times per day" is the same peptide added once per time of day (the fuller Time of Day list below makes that read naturally); no separate control was built
- ✅ **Expanded Time of Day options (built 2026-10-07: Fasting, Waking, AM, Pre-workout, Post-workout, PM, Before bed, Bedtime, Any; the calendar week view shows only the slots in use and the later ones move up; Today and the Dashboard list doses in day order)** (parked during the Phase 10 Calendar redesign brainstorm, 2026-09-30, so it isn't missed — this is a real data-model change to the dosing/protocol Time of Day concept, not a cosmetic tweak, and deserves its own brainstorm/spec cycle before implementation): Fasting, Waking (immediately after waking), AM (first half of day), Pre-Workout (up to 1hr before), Post-Workout (up to 1hr after), PM (second half of day), Before Bed (up to 1hr before), Bedtime, Any. Slots collapse from the bottom up when unused — e.g. no Waking entries means AM takes the first position; no Before Bed/Bedtime entries means Any moves up rather than leaving a gap.
- [E] Pre-planned stacks: Top 10 Stacks PDF, "Celebrity Stacks" — *note: overlaps with Library's "26 or so Premade Protocols" below, same underlying content*
- ✅ (built 2026-10-07: a print icon on each protocol opens a one-page view) Printable protocol view
- **"How many vials do I need?" calculator**, on the protocol-item level — given a protocol's dose/frequency/duration, work out how many vials to order from a vendor. Brainstormed and spec'd 2026-09-30; split into two sub-projects since the calculator needs a correct day-by-day schedule to total against first:
  - ✅ *Cycle On/Off shipped, 2026-09-30* (spec: `docs/superpowers/specs/2026-09-30-protocol-cycle-on-off-design.md`) — a protocol item can now have one or more "cycle off" week-ranges (e.g. 3-weeks-on/3-weeks-off) during which it's never due, on Today/Calendar/Dashboard alike, regardless of frequency or titration; a "+ Cycle off" control in the builder, independent of the Titration toggle. A whole-branch review caught the builder flow not actually working end-to-end for the single most common titration shape (a ramp step then an open-ended "onward" step) — fixed by allowing a cycle-off to overlap a titration step (`is_due()` already makes the cycle-off win either way) and simplifying a broken toggle button down to one always-present "+ Cycle off".
  - ✅ *Totals popup shipped* (an icon on each saved protocol: total course quantity per item, BAC water, estimated vials) and ✅ the per-item "purchasing unit" (vial vs. 10-vial kit) shipped; ✅ **Shop this protocol** (cheapest one- or two-order plan from the price lists, editable shipping, BAC water from the brands you rank, text-file and email sharing) built 2026-10-07.

**Body Outline**
- ✅ (built 2026-10-07: every marker has its own line, an Add marker button, results may be a number, 0, negative or <5 / >100) Labs form: list every marker with an inline entry box instead of select-then-add; an "Add Marker" button for anything not listed; integer-only entries (positive/negative, `<`/`>` accepted, 0 is valid)
- *(Entry-box sizing/placement and day-range dropdown moved to Phase 10 — Beautification)*

**Charts & Graphs**
- *(Body outline label repositioning, hover, and click-through shipped in Phase 10 — Beautification, 2026-09-30)*
- ✅ (works: a panel can be dated any day since 2000 and the Labs tab now defaults to the Lifetime range, so older panels chart at once) Historical labs backfill
- [I] AI-assisted lab-result interpretation (MedGemma or similar) — *see MedGemma below; this is the same proposal*
- *(Overview chart dropdown + readability shipped in Phase 10 — Beautification, 2026-09-30)*

**Calendar**
- ✅ (shipped with the Phase 10 calendar redesign) Month view: individual card per day, not one long bar (structural, not just visual)
- ✅ (built 2026-10-07) Any view: clicking an item due today should surface Pick Site / Log Dose directly
- Subscribe-to-calendar logic — *already an open Phase 4 item ("Subscribe to calendar on device"); no new roadmap entry needed, just reaffirmed*
- *(Legend, color-bar/banner/accent styling, and the site-picker art moved to Phase 10 — Beautification)*

**Calculator**
- ✅ HGH-specific dosing calculations (IU vials, IU-to-mcg conversion, teaching tool; built 2026-10-06)

**Library**
- ✅ (built 2026-10-07: an "Open in calculator" link on each dosing tier whose dose is a plain amount) Per-peptide "open calculator prefilled with Beginner/Moderate/Advanced/Custom dosing" button
- (code fix built 2026-10-07; the 35 missing sheets are still yours to supply [E]) Find peptides still showing the old card-style entry (High/Moderate/Low evidence tag) and get them onto the sheet-style entry — confirmed two distinct causes, both real:
  1. ✅ **Fixed 2026-10-07 in the loader (`load_sheets` now joins a sheet to the one imported card it is about when the names differ by a parenthetical, alias or spacing, never touching entries you added; re-run `python -m app.library_load_sheets` to merge the existing duplicates).** Was: **31 peptides have a real sheet-style entry that already exists under a slightly different name** (e.g. card "Amylin" vs sheet "Amylin (IAPP)"; card "Atosiban" vs sheet "Atosiban (Tractocile)"), so the import's exact-name match created a second row instead of updating the original — these are the "two entries, same thing" duplicates. A fix belongs in `load_sheets`'s matching logic (e.g. also check aliases), not a data-entry job.
  2. **35 peptides have no matching source file at all yet** (e.g. AMG-133/MariTide, Nafarelin, Epitalon, GLP-1, GIP, and 30 others) — genuinely still card-only, matching "anything with a High/Moderate/Low tag potentially."
- [E] ~26 premade protocols, sourced from the owner's own saved copies of what influencers in the space are running plus community-consensus protocols — *overlaps with Protocols' "Pre-Planned Stacks" above, same source material*
- [E] Standardized footer copy change (Educational/Informational Purposes Only disclaimer wording)
- *(Dosage-tier color bar and caution-style borders moved to Phase 10 — Beautification)*

**Overall**
- *(General visual/color/accent pass — see Phase 10 — Beautification)*

**MedGemma / AI lab interpretation [I]**
- A hybrid architecture proposal: deterministic code for all real calculations (HOMA-IR, LDL, eGFR, dosing conversions, etc.), a self-hostable medical LLM (MedGemma or similar) strictly for *explaining* results and flagging calculation mismatches against a reference database, never for doing the math itself. Includes an AI-acceptance popup/checkbox per upload and a Settings-level on/off toggle. Raises real privacy/PHI-hosting considerations if ever exposed beyond local use.
- *This is architecturally substantial — worth its own brainstorming/spec cycle before it's more than a placeholder line on the roadmap, not something to fold in as a quick bullet.*

---

## Known Limitations

### Library price-list import: vendor naming (resolved 2026-10-05; remaining names are by hand)
Vendors spell one compound many ways ("BPC157", "BPC 157", "Hexarelin Acetate", "Kiss Peptin-10"). `tools/import_price_lists.py` now matches each price-list name to an existing library card in tiers (`app/library/matching.py`), stopping at the first tier with a hit:
1. **exact** name (any case)
2. **normalized**: spacing, punctuation, case and a trailing "Acetate" ignored
3. **related**: the card's Aliases (comma-separated; a slash may sit inside one), or the name without its parenthetical ("Aicar" = "AICAR (Acadesine)")
4. **blend**: the same set of ingredients whatever the doses or order ("BPC157 5mg+TB500 5mg" = "Wolverine Stack (BPC-157 + TB-500)")
5. **fuzzy**: exactly one misspelled word, with the digits and word count identical

Real product differences are never merged: with / without / no / DAC (and anything in a parenthetical containing those words) stay separate, so "with B12" never lands on "without B12" and "CJC-1295 (No DAC)" never on "CJC-1295 DAC". The importer never creates cards and only *adds* specs to a card, so re-importing is safe.

**How to teach it a new spelling:** add the vendor's name to the right card's **Aliases** in the library editor, then re-run the import. A generic card catches its variants this way: the two TRT cards list each testosterone ester (Cypionate, Decanoate, Phenylpropionate, Propionate, Sustanon, Undecanoate) as aliases, since "TRT" is the generic name for any testosterone ester.

**Resolved with the owner's answers (2026-10-05):** `5Amino/MQ`, `Fox04 -DRI` and `SLU332` are aliases on 5-Amino-1MQ, FOXO4-DRI and SLU-PP-332; `CJC1295(Without DAC)5mg+IPA5mg` is the No-DAC CJC-1295 + Ipamorelin product (alias on that card); the GLOW and KLOW blends map to their existing blend cards, and the BPC-157 + TB-500 blends to Wolverine Stack; and cards were created for **Erythropoietin (EPO)**, **Human Menopausal Gonadotropin (HMG)**, **Relaxation PM** (a custom blend; its DSIP / Selank / Oxytocin / Epitalon composition and dose ranges are in the card's Notes) and the five **HGH Fragment 176-191 … 195** products, which are individual products and stay separate cards.

**Still unmatched (5 names; nothing was created for them):** `L-carniting 600mg(Liquid)` (probably the existing L-Carnitine card), `TNT(Testosterone Ethanate+ Trenbolone Ethanate)`, `Tpropionate Isocaproate`, `TB2(BT)`, `THY-1(TA5)`. Add each as an alias of the right card, or create a card, in the Library edit phase. The new cards above are bare (name, specs from the price list, Relaxation PM's notes): their descriptions, "focus" and dosing still need filling in. `KGLOW` has no library card yet.

### Importer-created library cards (cleaned up 2026-10-05)
An early importer created a custom card for every price-list name it couldn't match exactly. The 53 unreferenced ones (ids 220 and 222–274, including the duplicate `HGH 191AA (Somatropin)`) were removed from the local database and the import re-run with the new matcher; backups are `data/amide.db.bak-2026-10-03`, `data/amide.db.bak-2026-10-05` and `data/amide.db.bak-2026-10-05-b`. `B-12` (id 221) was kept because a protocol uses it. Specs for any name still unmatched are not on a card, but they remain in the PDF and attach as soon as a card or alias exists.

### Course Totals: "normally supplied" vial size (done 2026-10-05)
The library card editor now has **Normally supplied vial size** (amount + unit). Total Course Quantities uses it for the vial and BAC water estimate when a protocol item has no linked inventory item. No card has it filled in yet; set it per peptide as needed. (When an inventory item *is* linked, that item's own vial size still wins and must be Lyophilized, as before.)

---

## Open questions (all answered by what was built, 2026-10-07)

1. **Cost:** ✅ each order line holds its cost; shipping and tax are allocated across lines for a true per-vial cost.
2. **Count on reconstitution:** ✅ yes: reconstituting takes one from inventory (and a reconstitution now also uses the supplies it needs).
3. **Remote access:** ✅ self-hosted by whoever runs it; open [A]: write the reverse-proxy and HTTPS guidance.
4. **Users:** ✅ multi-user with private data and opt-in sharing.
5. **Units:** ✅ IU is supported, including IU vials in the calculator.

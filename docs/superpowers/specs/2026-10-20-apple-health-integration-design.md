# Apple Health integration (Health Auto Export): design

Date: 2026-10-20. Status: draft for approval.

## Intent and why

The owner wants every piece of data their iPhone's Apple Health holds to flow into Amide, for two reasons: health tracking with charts and graphs now, and, if they later adopt MedGemma, a clean store that a model can read for evaluation. The phone is the only source: no Apple Watch or fitness device, so the data is mostly phone-counted steps and distance, plus whatever other apps write to Health (a smart scale, a water or food logger). The owner uses the iOS app **Health Auto Export**, which posts JSON to a web address on a schedule. Their Amide runs at a public HTTPS address (kept out of the repo and the docs) and is protected only by Amide's own sign-in and 2FA. No other login sits in front of it.

## Goals

1. Receive everything Health Auto Export can send, not only what Amide charts: health metrics, workouts, symptoms, ECG, heart rate notifications, state of mind, cycle tracking, medications.
2. Keep each export as it arrived (so nothing is lost and parsers can be fixed later), plus a clean per-metric history that charts and a future MedGemma step read.
3. A step-by-step setup page inside Amide with the exact field values for the app, a copy-ready token and address, and a "waiting for first data" status.
4. When the first data arrives, a popup lists the metrics Amide received, in sections, with a switch per metric and per section. My recommended ones are on. The owner approved the mockup (see below).
5. Charts and a Body-tab section for the metrics that are switched on.
6. One week after the first data, a reminder for Claude to compare the owner's own logged workouts with what Apple recorded, and report how they differ.

## Non-goals and anti-goals

- No merging of Apple workouts into Amide's own workout log, calories or progress. They stay in a separate Apple section. (The owner's choice; they want to see how the data compares first.)
- No writing to Apple Health, no reading it directly (no iOS app of our own, no HealthKit).
- No cloud service, no third party: the phone posts straight to the owner's server.
- No MedGemma work now. The only commitment is that the history is easy to read later.
- Not a failure to avoid: a payload that is huge or oddly shaped must never crash the server, block the proxy, or leave a half-imported export behind.
- Amide's own domain is never written into the repo, the docs or the setup page text; the page shows the address of whatever host the owner is viewing it from.

## What the owner decided

- Apple workouts stay separate from Amide's workout log.
- The first-data popup works as mocked up: sections, a switch for each section and for each metric, a "Recommended" tag, "Turn all off", "Use recommended", "Save choices", and a count of how many are on.
- Defaults for a phone with no watch: on = steps, walking and running distance, flights climbed, weight, body fat, lean body mass, BMI, water, dietary energy, protein/carbs/fat/fiber, state of mind, symptoms. Off = active energy, walking speed, height, caffeine, sleep, all heart and vitals, mindful minutes, medications.
- A one-week reminder to compare Apple workouts with the owner's own logged workouts (a scheduled Claude task, created after the first data arrives).

## Facts about Health Auto Export (from its official help pages, read 2026-10-20)

- A "REST API" automation sends an HTTP POST with `Content-Type: application/json` (CSV would be multipart; we use JSON only).
- Fields in the app: automation name, URL, request timeout, HTTP headers (key and value rows), data type (Health Metrics, Workouts, Symptoms, ECG, Heart Rate Notifications, State of Mind, Cycle Tracking, Medications), which metrics, preferred sources, export version (1 legacy, 2 current), date range (Default, Since Last Sync, Today, Yesterday, Previous 7 Days), Summarize Data on or off with a time grouping, Batch Requests, sync cadence.
- The app has no built-in authentication; a token goes in a custom header (for example `Authorization: Bearer ...`).
- The app adds headers `automation-name`, `automation-id`, `automation-aggregation`, `automation-period` and `session-id`.
- The page documents no retry rules and no response body to return. Success or failure shows in the app's activity log by HTTP status. Payloads can reach several hundred megabytes.
- Metrics JSON: a `metrics` array; each entry has `name` (snake_case), `units` and `data`, a list of `{qty, date, source}` with dates like `2024-02-06 14:30:00 -0800`. Special shapes: blood pressure (`systolic`, `diastolic`), heart rate (`Min`, `Avg`, `Max`), sleep analysis (aggregated per day with `totalSleep`, `asleep`, `core`, `deep`, `rem`, `inBed` and start/end times, or per segment), blood glucose (`mealTime`), handwashing and toothbrushing (`value`), insulin delivery (`reason`).
- Workouts JSON (version 2): a `workouts` array with `id`, `name`, `start`, `end`, `duration` (seconds), energy, distance, speed, elevation, heart rate objects and arrays, optional route points when "Include Route Data" is on.
- **Not established:** the top-level wrapper (the pages show `metrics` and `workouts` arrays; whether they sit under a `data` key is not stated), the exact payload shapes for symptoms, ECG, heart rate notifications, state of mind, cycle tracking and medications, the full list of metric names, and how batch requests split a payload. These are settled by capturing a real test export before the parser is finished (see Plan notes).

## How it works (the parts the owner left to the builder; these are my calls)

**Receiving.** A new address under `/api/ingest/` takes the POST. It uses the existing token mechanism that skips the legal-notice and 2FA gate (`app/auth/gate.py` already lets `/api/ingest/` through and the price-list watcher uses it), so Amide's 2FA is not in the way. Health tokens are a separate kind from watcher tokens: any signed-in person can create their own from Settings, Integrations; a health token can only post health data for its owner, and a watcher token cannot. Secrets are shown once and stored as a hash, as now. A request that is too large, malformed, or rate-limited gets a clear HTTP error the app shows in its log. The server answers 200 only after the export is safely stored.

**Storing.**
- Raw: every export is kept as received, compressed, with its receive time, the app's headers, and a content hash. The same export twice is recognised and not stored again. Raw exports are kept 90 days, then deleted; the clean history stays. (My call.)
- Clean history: one table of owner, metric name, start time, end time, value, unit, source, and extra fields (such as systolic and diastolic, min/avg/max, sleep stages) kept as JSON. A unique key on owner, metric, start time and source makes re-posting safe (upsert). Workouts, symptoms, ECG, heart rate notifications, state of mind, cycle tracking and medications each get their own small table or a typed row in a general "health events" table (builder's choice), keeping every field the export carried. Route points and ECG waveforms are kept as stored blobs, not expanded into rows, and are not shown in the UI in this first version.
- Metric choices: a per-owner table of metric name, group, switched on or off. New metrics that show up later appear as "new" and trigger the popup again for just those.
- Times: Apple sends a local time with offset; store the instant in UTC plus the offset, and chart by the owner's Amide time zone.
- Backups: the clean history and the choices go into backups; raw exports and tokens do not (matches how `ingest_tokens` is already excluded).

**Setup page** (Settings, Integrations, "Apple Health" card; this is the one place everything the owner must type or copy lives). The card holds: a **Create token** button (the secret is shown once, with a Copy button, and later only its first characters, last-used time and a **Revoke** button); a Copy button for the full ingest address, built from the host the owner is viewing Amide from; a Copy button for the exact header line `Authorization: Bearer <token>`; and the status line and recent receipts below, plus the metric on/off choices (reopen the popup from here). Numbered steps with copy buttons, written in plain words: create a token; in Health Auto Export create a REST API automation; URL is the page's own address plus the ingest path; add the header `Authorization: Bearer <token>`; Data Type Health Metrics first, then a second automation for Workouts, and others as wanted; Export Format JSON, Export Version 2; Date Range "Since Last Sync"; Summarize Data on with Time Grouping hours (so a year of steps is not a million rows; the first export should be Previous 7 Days or a manual export over a wider range); Batch Requests on; Sync Cadence every hour; tap **Manual Export** to test. Below the steps: a live status line ("Waiting for first data", then "Last received 3 minutes ago, 2,184 readings") and the owner's recent receipts with HTTP-style outcomes, so a failure is visible without the phone. Includes the reverse-proxy note: allow request bodies large enough for the first export (the guide gives the setting by description, without naming a proxy product), and a warning that anything that asks for a separate login in front of Amide must exempt the ingest path.

**First-data popup.** Shown on the owner's next page load after data first arrives (and again, only for new metrics, later). Sections: Activity, Body, Nutrition and water, Sleep, Heart and vitals, Mind and other. Only metrics actually received are listed, each with its unit. Recommended defaults are marked and on; switching a section switches all its metrics; "Use recommended" and "Turn all off" are available. Saving stores the choices. Unticked metrics are still stored; they are just not shown or charted. Choices can be changed any time from the Apple Health settings card.

**Showing it.** An "Apple" tab on the Body page: a chart for each switched-on metric with the same range choices as the other body charts, today's and the week's values on top, and, separately, the list of Apple workouts. Apple data is never copied into the owner's own measurements and never added to the Overview Metric list, so nothing is counted twice; weight, body fat and lean mass from Apple are charted only on the Apple tab, next to (not merged with) the owner's own.

**MedGemma readiness.** One read function returns a person's history for a date range and metric list as plain structured data (metric, time, value, unit), so a later step can pass it to a model. Nothing calls a model now.

**The one-week comparison.** After the first data arrives, create a scheduled Claude task for 7 days later that reads the owner's logged workouts and the Apple workouts for the same period and reports the differences (count, duration, distance, calories). The owner expects Apple to record only walking and running at first.

## Constraints

- Follow existing patterns: FastAPI router, SQLAlchemy models, an Alembic migration (next number is 0077), Jinja templates, the `.btn` system, tests for each piece.
- Never put a vendor name or real price in the repo (standing rule). Use invented examples in tests and docs, and never the owner's domain.
- Unit handling follows Settings, Display: store metric units as sent (and record the unit), convert for display.
- A malformed or unknown payload is stored raw and flagged, never dropped silently and never allowed to break the page.

## Left to the builder

Table shapes beyond what is stated; the exact upsert key; compression format; how the metric list is grouped (the six sections above are the owner's; which metric belongs to which section is the builder's mapping, with unknown metrics under "Mind and other" until grouped); the exact chart types per metric; how the popup is triggered; the maximum accepted request size.

## Plan notes

1. Capture a real test export first (the owner taps Manual Export against a throwaway receiver) so the wrapper and the special shapes are verified, not assumed. Until then the parser is built from the documented shapes and every unverified field is marked "Not established" in code comments and docs.
2. Build in this order: token and receiver with raw storage, then the clean history and parsers, the setup page, the popup and choices, the Body tab, the guides and screenshots, the comparison reminder.

## Calls I made after the builder check (my decisions, not the owner's)

1. **The one-week reminder is not Amide code.** Amide records the time the first data arrived and shows it on the Apple Health card. When the owner tells Claude the data has arrived (or Claude sees it on a later check), Claude creates a scheduled task for 7 days after that moment that reads the owner's logged workouts and the Apple workouts and reports the differences. Nothing inside Amide schedules anything.
2. **No Overview mixing.** Apple data stays on the Apple tab only (see Showing it), so there is no source-priority rule to build.
3. **Non-numeric types.** The popup and the choices table list the numeric metrics plus symptoms and state of mind. Symptoms and state of mind show on the Apple tab as a dated list. ECG, heart rate notifications, cycle tracking and medications are stored only, with no switch and no display; the Apple Health card shows how many of each have been received.
4. **Two milestones.** First build the token, the receiver and raw storage and the setup card, then the owner runs Manual Export once, and the parser, popup and Apple tab are finished against that real sample (the first manual export is also the first real data). If the real shape differs from the documentation, the sample wins.
5. **Summarized readings.** Each stored reading carries a grain (seconds, minutes, hours, days) taken from the automation's headers or the data, an end time of start plus grain, and the grain is part of the unique key. Charts read, for each day, the finest grain stored for that metric and never add two grains together. Cumulative metrics (steps, distance, flights, water, dietary energy) are summed per day; the others (weight, body fat, lean mass, BMI) use the day's last value.
6. **Multiple users.** Data, popup and Apple tab belong to the person who owns the token. The tab and the popup appear only for someone who has data or a health token. An administrator sees another person's Apple data only through the existing Sharing feature, as with other body data.
7. **Limits.** The body is streamed to disk, not read whole. The default cap is 512 MB, set by one environment setting so the owner can raise it. The default rate is 30 requests per minute per token. The setup card names the proxy body-size setting the owner must raise to match.

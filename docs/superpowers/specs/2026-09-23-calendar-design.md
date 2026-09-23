# Calendar — Design

Date: 2026-09-23 · Status: approved by owner in chat

## 1. Intent

A Calendar tab that projects the owner's protocols onto dates: Month (lines), Week (blocks), Day (cards). Clicking any of them shows what is due for that protocol on that date. Viewing only; dose logging comes with Daily Dosing.

## 2. Decisions

| Topic | Decision |
| --- | --- |
| Which protocols | Own protocols that are not paused. Scheduled ones appear from their start date; ended ones appear through the day End was pressed (`ended_on`) or `end_date`. |
| Week | Sunday–Saturday; US week numbers (week 1 contains Jan 1; the last days of December in that week are week 1 of the next year). |
| Placement | New "Calendar" nav tab after Protocols. Protocols stays the front page. |
| Times | No clock times exist: Week/Day views group by AM, PM, Bedtime, Any time. |

## 3. Scheduling rules (`app/calendar/schedule.py`, pure)

For item with protocol start `S` and date `D ≥ S` within the protocol window, `d = (D − S).days`:
daily → always · eod → `d % 2 == 0` · every_n_days → `d % n == 0` · weekdays → `D`'s letter (MTWRFSU) in the item's weekdays · weekly → `d % 7 == 0` · as_needed → never scheduled (listed in Day view's "As needed" section while the protocol runs).
Dose: titration on and a step covers `D`'s protocol week → that step's dose and number; otherwise the item's dose (may be "not set").

## 4. Views (`/calendar?view=month|week|day&date=YYYY-MM-DD`)

- Header: view dropdown, ‹ Today ›, period title. Invalid params fall back to month / today.
- **Month:** grid from the Sunday on/before the 1st to the Saturday on/after the last day. Left column = week number (link → week view). Day number = link → day view. Each protocol has a colour and a lane (fixed for the month, order of first appearance); consecutive due days in a week row form one bar, titled at its start; each day inside a bar is clickable. Max 4 lanes per row; extra protocols on a day → "+N more" (→ day view). Today highlighted; days outside the month dimmed.
- **Week:** 7 day columns (header links to day view) × rows AM / PM / Bedtime / Any time; a block per protocol per slot with its name and number of doses.
- **Day:** sections AM / PM / Bedtime / Any time; a card per protocol listing peptide, dose + unit, step, route, inventory item. "As needed" section.
- **Details panel** (dialog) on click: protocol name, date, each due item (peptide, dose + unit, titration step, time, route, inventory), "Edit protocol" link. Data comes from JSON embedded in the page.
- Colours: 8-colour palette defined for light and dark; protocol colour = position among the user's protocols (by id) mod 8.

## 5. Testing

Unit: each frequency, window edges (start, end_date, ended_on, future start), paused excluded, titration dose/step, as-needed listing, week numbers (incl. year boundary), month grid bounds, bar runs + lanes + overflow. Integration: month/week/day render; week-number and day links; details JSON; privacy (another user's protocols never appear); nav tab.

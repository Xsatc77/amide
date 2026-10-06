# Price Analysis Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (this plan was executed inline). Steps use checkbox syntax.

**Goal:** Show the stored vendor price data in three places: a price-history chart on the vendor card, a `NEW PEPTIDE ALERT` on the dashboard, and a per-vial price range on each library card.

**Architecture:** Read-side helpers in `app/library/price_lists/analysis.py` (pure over query results, tested with invented vendors), a pure multi-series chart helper in `app/library/price_lists/chart.py`, one new small table (`price_alert_ignores`), and thin template/route changes on three existing pages.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Jinja2, inline SVG, vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-price-analysis-surfaces-design.md` (all decisions settled).

## Global Constraints

- No real vendor, price list, price, or filename appears in any tracked file; tests use invented vendors (Acme, Zephyr, Borealis) and invented peptides (Zorvex, Quillamine). Run the local vendor-name scan before every commit.
- Chart y-axis is the **pack price** (price per kit or box as listed); the per-vial price is in the hover text. Range is **price per vial**, for the card's most commonly listed vial size, mg / mcg / IU only, packs with no stated size skipped.
- "Current" list = the newest-dated list per (vendor, warehouse). A "new peptide" is a product in a current list that matches no library card (checked live against cards and aliases, so it clears by itself), has a mg / mcg / IU size, and is not ignored.
- Ignore is administrator-only (404 for others), global, and stored in the database only.
- Nothing is stored in browser storage; the chart opens on the first product every time.
- The Alerts card shows the first 5 new-peptide lines and tucks the rest under "Show N more".

## Tasks

1. **Ignore table.** `PriceAlertIgnore` model + migration `0032` (unique `product_key`). Test: round trip, unique key.
2. **Analysis helpers** (`analysis.py`): `current_lists`, `price_range`, `vendor_price_history`, `new_peptides`, `ignore_product`. Tests: newest list per vendor and warehouse; a vendor's China and USA lists both current; range uses the most commonly listed size, per vial, skips unsized and liquid packs, a single price; history groups by product and size and keeps the lowest price per date; unmatched products group by name; new peptides exclude matched, liquid, ignored, and a product whose card or alias was added later; vendors listed for each.
3. **Chart helper** (`chart.py`): `multi_series_chart`. Tests: shared date and value scales, one series per label, a single date, a single point, an empty set, unsorted input.
4. **Vendor card chart.** `_detail_context` adds a price-history view (per product: legend, geometry, hover text); `vendors/detail.html` gets a "Price History" section with a product drop-down; `price-history.js` shows the selected product; CSS for swatches. Tests: page contains the drop-down and series for a vendor with lists, none for one without, the same size has the same color on every product, USA series dashed.
5. **Dashboard alert.** `dashboard.py` adds `new_peptides` to the alerts (inventory-gated like the rest); `index.html` renders `NEW PEPTIDE ALERT <peptide> — <vendor(s)>`, an Ignore button for administrators, and "Show N more"; `POST /price-alerts/ignore` in `app/routers/price_alerts.py`. Tests: alert lines and vendor wording (one vendor, several), cap of 5 plus the rest, Ignore removes it, non-admin gets 404 and no button, an alias clears it.
6. **Library card range.** `library_detail` adds `price_range`; `library/detail.html` shows it on the Half-life row (far right) in both the sheet and card layouts, or its own row when there is no Half-life; CSS. Tests: shown with the right text, hidden with no prices, own row without Half-life.
7. **Verify in the browser** on a scratch copy of the database (light and dark themes, phone width), update the roadmap, run the whole suite, final review.

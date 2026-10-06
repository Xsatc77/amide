# Price Analysis Surfaces — Design (for owner review)

## Context

The price-list import (`2026-10-05-price-list-import-design.md`) now stores every vendor's price lists:
vendors, warehouse region, and per-product pack size, pack price and kit/box type, with history kept (each
new-dated list is added, the newest-dated list per vendor and warehouse is "current" the moment it is
imported). This spec covers three places that data should appear. **No vendor, price list, or price from the
owner's data appears in this document or anywhere in the repository; all data stays in the local database.**

Nothing here is built yet. Each section ends with the decisions the owner still needs to make.

## Decisions already made (owner)

1. **Price history stays in the database.** Amide never stores the PDFs, so there is nothing to prune; the
   owner's originals stay in their own folder.
2. **The list with the most recent date is current as soon as it is imported.**
3. **Vendor card:** a chart with a drop-down of every peptide that vendor sells, charting price over time,
   with a differently colored line for each vial size (mg / mcg / IU / ml).
4. **Dashboard:** an alert reading `NEW PEPTIDE ALERT <PEPTIDE> <VENDOR or VENDORS>`.
5. **Library card:** as the data matures, a high/low price range on the same line as **Half-life**, at the far
   right end of that boxed area.
6. **The library will stop needing new cards for long stretches**; the importer never creates cards.

## Shared data helpers (`app/library/price_lists/analysis.py`)

Pure functions over query results, unit-tested without a browser:

- `current_lists(session)`: the newest-dated list for each (vendor, warehouse).
- `price_history(session, vendor_id, product_key)`: points `(list_date, pack_price, pack_size, pack_type,
  warehouse)` grouped by vial size and unit. `product_key` is the matched library card id, or the listed
  product name when no card matches.
- `price_range(session, peptide_id)`: low and high over the current lists (basis below).
- `new_peptides(session)`: products to alert on (definition below), each with the vendors that list it.

## A. Vendor card price chart

**Where:** the vendor detail page (`/vendors/{id}`), a new "Price history" card.

**Behavior:**
- A drop-down lists every product the vendor has ever listed, matched peptides by library name first, then
  unmatched products by their listed name. The first entry is selected.
- The chart plots the pack price against the list date. One line per vial size and unit, each size its own
  color from a fixed accessible palette (the same size is the same color on every product). A legend names the
  sizes. A single dated list shows as dots; lines appear as lists accumulate.
- Hovering a point shows the date, the price, the pack (`kit of 10` or `box of 1`), and the price per vial.
- If the vendor has both China and USA lists, a size gets one line per warehouse (USA dashed), labeled
  `10mg · USA`.
- Rendered server-side as inline SVG for every product, with a small script that only shows the selected one
  (the pattern the Measurements overview chart uses). Nothing is stored in the browser (the app stores
  nothing there beyond the sign-in cookie), so the page opens on the first product each time.
- Empty state: "No price lists imported for this vendor yet."
- A new multi-series chart helper is added beside the existing single- and dual-series helpers; it is pure and
  tested.

**Open decision:** the y-axis. Recommended: **pack price** as listed (what the owner pays), with per-vial cost
in the hover. The alternative is per-vial cost on the axis, which makes kits and boxes comparable.

## B. Dashboard "NEW PEPTIDE ALERT"

**Where:** the existing dashboard Alerts card, one line per product:
`NEW PEPTIDE ALERT <peptide> — <vendor>` or `<vendors>` when several list it, linking to the library so a
card can be created, or an alias added to an existing card.

**Recommended definition:** a product in a vendor's current list that
1. matches no library card (so the library needs a card or an alias), and
2. is a peptide-like product (a mg, mcg or IU size), not a liquid or a supply, and
3. has not been ignored.

It disappears by itself once a matching card or alias exists. Each alert has an **Ignore** action for
products that are not peptides (stored in a small local table, never in the repository).

**First import:** the owner's first import leaves a backlog of unmatched products (liquids, blends, a few
peptides). Recommended: show the whole backlog once so it can be triaged with Ignore, then only genuinely new
products appear. The alternative is to treat each vendor's first list as a silent baseline and alert only on
products that appear in a *later* list.

**Visibility:** the same viewers as the existing Alerts card (anyone who sees inventory alerts); vendors are
shared across users.

**Open decisions:** which definition of "new" (above: no library card, versus new to that vendor), and
backlog versus silent baseline.

## C. Library card price range

**Where:** the library detail page's quick-facts block (the boxed area holding Half-life, Route, Cycle). The
range sits on the Half-life line at the far right end of that area. If a card has no Half-life, the range
still gets its own line there. Cards with no current prices show nothing.

**Recommended basis:** price per vial across all current lists, for the card's most commonly listed vial size,
shown as `10mg · $5.50 – $12.00 per vial · 7 lists`, so sizes are not mixed. The alternative is cost per mg
(comparable across sizes, meaningless for IU and ml) or the kit price range per size.

**Open decision:** the basis (above). Also whether blends and boxes (one-vial oils) are included in the range.

## Testing

Analysis helpers are tested with hand-built rows (invented vendors such as Acme and Zephyr): newest list per
vendor and warehouse, history grouping by size, range and per-vial arithmetic, kit versus box, ignored products,
and an alert clearing when a card or alias appears. The chart helper is tested for geometry (shared date and
value scales, one series per size, a single point, an empty set). Pages are checked in the browser for layout in
light and dark themes and at phone width.

## Out of scope

Price alerts (drops and rises), cross-vendor comparison screens, currencies other than USD, editing prices, and
reading scanned PDFs, images and spreadsheets.

---

**For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
superpowers:executing-plans to implement the plan built from this spec, after the owner settles the open
decisions above.

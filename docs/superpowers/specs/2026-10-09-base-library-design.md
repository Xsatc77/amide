# The base library ships with Amide: design

Date: 2026-10-09. Status: awaiting the owner's review.

## Problem

A new install gets 105 empty peptide names (old-style names for 33 of them). The cards the owner worked on, with aliases, tags, summaries, dosing tiers, cycles, stack notes, monitoring tests and the readable sections, live only in the owner's private `data/` folder and database. Loading them into another install by backup merges on name and keeps an existing empty seed entry, so well-known peptides such as Ipamorelin and Kisspeptin arrive with no data and the old-name entries stay as duplicates.

## Goal

A brand-new install has the 105 base peptides, in the new-style names, each with its full card, including aliases. An install that already has the empty or old-style entries is brought to the same state when it updates, without touching anything the person has entered.

## Design

### The data
`app/library/base_library.json`, built from the owner's library by a tool (`tools/export_base_library.py`, run by the owner or the assistant on the owner's machine). One record per base peptide, in the shape `load_sheets` already consumes: name, aliases, tags, summary, the sheet columns, usage tips, `sheet_sections`, `sheet_sections_simple`, dosing tiers, cycle, stack relations, monitoring tests. About 2 MB.

**Left out on purpose:** `cost_estimate_text` (price ranges: the repository rule is that real prices are not shipped), the owner's private `notes`, card images, and anything per-person (inventory, doses, protocols). The card page simply shows no cost section for shipped cards.

**Checks before it is committed:** the vendor denylist scan is run over the JSON (after proving the scan finds a planted name), and a test fails if any `$` amount appears in the shipped file.

### Loading it
`app/library/base_library.py` with `load_base_library(session)`: for each record, find the peptide by name or alias; if it does not exist, create it from the record; if it exists and is an empty seed entry (no card data), fill it from the record; if it already has card data it is left as it is (the owner's or the person's edits win). It never touches `notes`. The loader reuses `load_sheets`, so the rules for old card entries and renames stay in one place.

It runs from a new Alembic migration, **0058**, so it happens once when an install updates and for every fresh install (right after the seed list, which already uses the new-style names). A later release can ship updated content through a new migration or a startup refresh.

### The old-name pairs
The same migration first merges the 33 old-name pairs: an old-name entry with no data is renamed to its new-style name when the new one does not exist, and when both exist everything that points at the old one (protocols, dose logs, price-list rows, goal stacks, notes) is moved to the new one and the empty old one is removed. An old-name entry that holds data is left alone. Names only, no vendor data.

### What the owner's server gets
Update the Amide image (re-pull). On start, migration 0058 renames and merges the duplicates and fills the empty entries from the bundled library, so Ipamorelin, Kisspeptin and the rest have their cards with no restore step. Nothing already entered is overwritten, and the data volume is not touched.

## Out of scope
The roughly 100 extra cards beyond the base 105 (they stay in the owner's private library), card images, price estimates, and shipping any vendor or price-list data.

## Testing
A test that the shipped JSON has exactly the 105 base names with aliases, tiers and sections and contains no `$` amount and no `cost_estimate_text`; migration tests on a database seeded with old-name empty entries (renamed, merged, references moved, entries with data untouched, owner notes untouched, running twice changes nothing); a fresh install ends with 105 full cards; and the vendor scan, proven on a planted name, over the JSON.

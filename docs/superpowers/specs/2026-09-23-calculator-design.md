# Reconstitution Calculator — Design

Date: 2026-09-23 · Status: proceeding under auto mode (owner supplied reference material, no open decisions needing a stop)

## 1. Intent

A live reconstitution calculator: vial amount + BAC water → concentration, draw volume, and U-100 syringe units, plus a reverse solver (target units → required water). Matches the math on the owner's cheat sheet and PepPal's calculator (researched at peppal.app/calculator). Optionally pre-filled from the owner's Inventory or a Protocol's dose. **Not in scope:** turning a reconstituted vial into a persisted "Active Vial" that depletes inventory and tracks a discard date — that is its own future decision (roadmap Phase 2's second half) and is called out to the owner rather than silently built.

## 2. Math (single source of truth: Python; the page calls it live via a JSON endpoint, no duplicate JS math)

- `concentration_mg_per_ml = vial_mg / water_ml`
- `draw_ml = dose_mg / concentration_mg_per_ml`
- `units = draw_ml * 100` (U-100: 100 units = 1 mL, independent of syringe size)
- `doses_per_vial = floor(vial_mg / dose_mg)` (undefined/omitted if dose_mg is 0)
- Reverse solver: `water_ml = vial_mg * (target_units / 100) / dose_mg`
- Dose entered in mg or mcg; mcg → mg by /1000 before all math; results always shown in both mg/mL and mcg/mL for concentration.
- Syringe capacities (U-100): 0.3 mL → 30 u, 0.5 mL → 50 u, 1.0 mL → 100 u. Draw exceeding the chosen syringe's capacity is flagged, not blocked (still computed).
- Zero/blank/negative vial, water, or dose → no crash; a `problems: []` list explains what's missing instead of a result.

## 3. Page (`/calculator`)

- Nav "Calculator" (currently a disabled placeholder) becomes a live link.
- Sections, top to bottom: **Vial** (optional "From inventory" select limited to the owner's Lyophilized/Powder-style items — mediums that need reconstituting: Lyophilized only, since Liquid/Autoinjector/Inhaler/Pill/Drops/Salve are not mixed with water — plus a manual mg field with quick presets 5/10/15/20/30/Other), **BAC water** (mL field, presets 1/2/3 mL/Other, and a "solve for target units" toggle), **Dose** (optional "From protocol" select of the owner's active/scheduled protocol items with a dose set, plus manual value + mg/mcg unit), **Syringe** (0.3/0.5/1.0 mL choice, drives the visual scale and the capacity warning).
- **Result panel**: concentration (mg/mL and mcg/mL), draw volume (mL), U-100 units (large), a horizontal syringe-barrel graphic with tick marks and a fill bar to the computed unit mark, doses-per-vial, and any warnings (over capacity, dose exceeds vial total).
- All fields live-recalculate via `GET /api/calculator/compute` (debounced ~150 ms), so the page still shows a sensible default result before any typing and works with JS disabled (server-rendered initial state from query params).
- Reference table (collapsible) mirroring the owner's cheat-sheet card 2 (U-100 units ↔ mL) and the mg/mcg reminder, for on-page reference.

## 4. Privacy / data touched

Read-only: the owner's own inventory items (Lyophilized only) and own protocol items (for dose prefill). No writes; no new tables.

## 5. Testing

Unit (pure math, exhaustive): concentration, draw, units, doses-per-vial (incl. floor and dose_mg=0), reverse solver round-trips (`water_for_target_units` then forward gives back ~target_units), mcg→mg conversion, capacity flag at/above/below each syringe size, all blank/zero/negative inputs produce `problems` not a crash or division by zero. Integration: page renders defaults; `/api/calculator/compute` returns correct JSON for representative inputs incl. the owner's cheat-sheet example (10 mg / 2 mL / 250 mcg → 5 mg/mL, 0.05 mL, 5 units) and PepPal's worked example (same numbers); inventory/protocol prefill lists contain only the signed-in owner's own rows (privacy); non-Lyophilized inventory items excluded from the vial-prefill list.

# Reconstitution Calculator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live reconstitution calculator (vial + water + dose → concentration/draw/units, plus reverse solver), with optional Inventory/Protocol prefill.

**Architecture:** Pure math module `app/calculator/reconstitution.py` (unit tested exhaustively) is the single source of truth. `app/routers/calculator.py` renders the page (server-computed default state) and a JSON compute endpoint the page's JS calls on every input change — no math duplicated in JS.

**Tech Stack:** FastAPI, Jinja2, vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-calculator-design.md`

## Global Constraints
- Math lives only in `app/calculator/reconstitution.py`; the JS never recomputes it, only calls the API and renders the response.
- Inventory prefill limited to the owner's `Medium.LYOPHILIZED` items; protocol prefill limited to the owner's own protocol items.
- Built in worktree `C:\tmp\amide-calc` (branch `feature/calculator`).

## Review Focus
1. Blank or zero vial/water/dose never raises (division by zero) — returns `problems`. (`test_blank_and_zero_inputs`)
2. The cheat-sheet's own worked numbers (10 mg / 2 mL / 250 mcg) reproduce exactly (5 mg/mL, 0.05 mL, 5 units). (`test_cheat_sheet_example`)
3. Reverse solver: solving for target units then recomputing forward returns that same unit count. (`test_reverse_solver_round_trips`)
4. Draw exceeding the chosen syringe's capacity is flagged (30u/50u/100u boundaries). (`test_syringe_capacity_flag`)
5. Another user's inventory/protocols never appear in the calculator's prefill lists. (`test_calculator_privacy`)

### Task 1: Math module
Files: `app/calculator/{__init__,reconstitution}.py`, tests `tests/test_reconstitution_math.py`.
Produces: `Result(concentration_mg_ml, concentration_mcg_ml, draw_ml, units, doses_per_vial, over_capacity, problems)`, `compute(vial_mg, water_ml, dose_value, dose_unit, syringe_ml) -> Result`, `water_for_target_units(vial_mg, dose_mg, target_units) -> float | None`, `SYRINGE_CAPACITIES_UNITS = {0.3:30, 0.5:50, 1.0:100}`.
- [ ] tests → FAIL → implement → PASS → commit.

### Task 2: Page + API
Files: `app/routers/calculator.py`, `app/templates/calculator/calculator.html`, `app/static/js/calculator.js`, CSS, `app/main.py`, `base.html` nav; tests `tests/test_calculator_page.py`.
- [ ] tests → FAIL → implement → PASS; browser check desktop + phone → commit.

### Task 3: Docs + ship
- [ ] README/ROADMAP (note Active Vial tracking still open); full suite; merge; push.

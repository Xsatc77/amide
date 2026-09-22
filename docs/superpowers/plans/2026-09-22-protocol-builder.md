# Protocol List & Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Protocols page (Active cards, goal cards, Saved protocols) and the goal-driven Protocol Builder, backed by a seeded peptide library.

**Architecture:** New SQLAlchemy models + Alembic migration `0003` (schema and seed). Pure-logic modules (`app/protocols/status.py`, `app/protocols/forms.py`) hold the rules and are unit-tested; `app/routers/protocols.py` wires pages, actions and a read-only JSON API. The builder page is rendered by vanilla JS from a JSON blob the server embeds (state + errors + goals/stacks/peptides/inventory), and submits a normal multipart form so validation/re-render work like Inventory.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, SQLite, Jinja2, vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-protocol-builder-design.md`

## Global Constraints

- No new runtime dependencies.
- Goal slugs/labels exactly as spec §3 table; enum values exactly as spec §3 (`mg`/`mcg`/`IU`; `daily`,`eod`,`every_n_days`,`weekdays`,`weekly`,`as_needed`; `am`,`pm`,`bedtime`,`any`; `subq`,`im`,`oral`,`nasal`,`topical`,`other`; weekday letters `MTWRFSU`).
- Status priority: Ended > Paused > Scheduled > Active; `end_date == today` is still Active.
- Seed inserts only when `peptides` is empty. Seed contains names only (no card text, no doses).
- Share button rendered `disabled` with title "Email sharing coming soon"; no backend route.
- Protocols are only removed by Delete.
- Match existing code style (see `app/routers/inventory.py`, `app/templates/inventory/list.html`).

## Review Focus

1. Deleting an inventory item that a protocol links to → protocol survives, link becomes empty. (Task 4 test `test_inventory_delete_unlinks_protocol_item`)
2. Builder save with a validation error → page re-renders with **every** row, step and goal the user entered, plus the messages. (Task 5 test `test_invalid_submit_preserves_state`)
3. A protocol whose end date is today → still Active on the cards. (Task 2 test `test_end_date_today_is_active`)
4. Two selected goals sharing a peptide → it's suggested once, in first-goal order. (Task 2 test `test_merge_stacks_dedupes_in_goal_order`)
5. Typing a "new" peptide whose name differs only in case/spaces from an existing one → reuses the existing peptide, no duplicate. (Task 5 test `test_custom_peptide_reuses_existing_case_insensitive`)

---

### Task 1: Goals, models, migration 0003 with seed

**Files:**
- Create: `app/goals.py`
- Modify: `app/models.py`
- Create: `migrations/versions/0003_protocols.py`
- Modify: `tests/conftest.py` (clean protocols + custom peptides between tests)
- Test: `tests/test_migrations.py`

**Interfaces — Produces:**
- `app.goals`: `Goal(slug, label, description)` frozen dataclass; `GOALS: tuple[Goal, ...]` (spec order); `GOALS_BY_SLUG: dict[str, Goal]`.
- `app.models` enums (all `str, Enum`, stored with `native_enum=False`, values as listed in Global Constraints): `DoseUnit`, `Frequency`, `TimeOfDay`, `Route`, `PeptideSource` (`card`,`starter`,`custom`).
- `app.models` classes: `Peptide` (`id, name, aliases, card_number, dose_low, dose_mid, dose_high, dose_unit, typical_frequency, notes, source`), `GoalPeptide` (`goal, peptide_id, position`, relationship `peptide`), `Protocol` (`id, name, start_date, end_date, ended_on, paused, titration_enabled, notes, created_at, updated_at`, relationships `goals: list[ProtocolGoal]` and `items: list[ProtocolItem]` ordered by position, `cascade="all, delete-orphan"`; property `goal_slugs -> list[str]`), `ProtocolGoal` (`protocol_id, goal`), `ProtocolItem` (`id, protocol_id, peptide_id, position, dose, dose_unit, frequency, every_n_days, weekdays, time_of_day, route, inventory_item_id, notes`, relationships `peptide`, `inventory_item`, `steps` ordered by start_week, cascade delete-orphan), `TitrationStep` (`id, protocol_item_id, start_week, end_week, dose`).
- FKs: items→protocols CASCADE; items→peptides RESTRICT; items→inventory_items SET NULL; steps→items CASCADE; goal_peptides→peptides CASCADE; protocol_goals→protocols CASCADE.

- [ ] **Step 1: Write failing migration tests** in `tests/test_migrations.py`:

```python
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def _cfg(db: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db.as_posix()}")
    cfg.attributes["configure_logger"] = False
    return cfg


def test_upgrade_from_0002_keeps_inventory_and_seeds(tmp_path):
    db = tmp_path / "a.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0002")
    with sqlite3.connect(db) as c:
        c.execute("insert into inventory_items(name,count,created_at,updated_at) values('Old',2,'2026-09-22','2026-09-22')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        assert c.execute("select name from inventory_items").fetchall() == [("Old",)]
        assert c.execute("select count(*) from peptides where source='card'").fetchone()[0] == 100
        assert c.execute("select count(*) from peptides where source='starter'").fetchone()[0] == 5
        assert c.execute("select card_number from peptides where name='BPC-157'").fetchone()[0] == 2
        goals = {g for (g,) in c.execute("select distinct goal from goal_peptides")}
        assert len(goals) == 8
        first = c.execute(
            "select p.name from goal_peptides g join peptides p on p.id=g.peptide_id "
            "where g.goal='fat-loss' order by g.position").fetchall()
        assert [n for (n,) in first] == ["Retatrutide", "Tirzepatide", "Tesamorelin", "AOD-9604", "MOTS-c"]


def test_downgrade_to_0002(tmp_path):
    db = tmp_path / "b.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")
    with sqlite3.connect(db) as c:
        tables = {t for (t,) in c.execute("select name from sqlite_master where type='table'")}
    assert "protocols" not in tables and "peptides" not in tables and "inventory_items" in tables
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_migrations.py -q` → FAIL (no revision 0003 / no peptides table).
- [ ] **Step 3: Implement** `app/goals.py` (8 goals per spec), the models above, and `migrations/versions/0003_protocols.py`: autogenerate the schema (`alembic revision --autogenerate --rev-id 0003 -m protocols` against a scratch DB at 0002), add check constraints by hand (dose > 0, every_n_days ≥ 2, start_week ≥ 1, end_week ≥ start_week, step dose > 0), then append a seed block: 100 card names in card order (card_number = index + 1, exact list in spec §7 source PDF order), 5 starter extras, and the 8 stacks from spec §7 — executed only when `select count(*) from peptides` is 0. Downgrade drops the six tables.
- [ ] **Step 4: Update `tests/conftest.py`** `clean` fixture to also delete `Protocol` rows and `Peptide` rows with `source == custom`.
- [ ] **Step 5: Run** full suite → PASS (existing 10 + 2 new).
- [ ] **Step 6: Commit** `feat: protocol + peptide library schema with seeded goal stacks`.

### Task 2: Status and stack helpers (pure functions)

**Files:** Create `app/protocols/__init__.py`, `app/protocols/status.py`; Test `tests/test_protocol_status.py`.

**Interfaces — Produces** (`app.protocols.status`):
- `class Status(str, Enum)`: `ACTIVE="active"`, `PAUSED="paused"`, `SCHEDULED="scheduled"`, `ENDED="ended"`; property `label` ("Active", …).
- `protocol_status(p, today: date) -> Status` — `p` needs `ended_on, end_date, paused, start_date`.
- `day_number(start: date, today: date) -> int | None` — 1 on start day; None before start.
- `current_week(start: date, today: date) -> int | None` — `(today-start).days // 7 + 1`; None before start.
- `current_step(steps, week: int | None)` → the step with `start_week <= week <= (end_week or ∞)`, else None.
- `merge_stacks(goal_slugs: list[str], stacks: dict[str, list[int]]) -> list[int]` — union in goal order then stack order, de-duplicated.

- [ ] **Step 1: Write failing tests**

```python
from datetime import date
from types import SimpleNamespace as NS

from app.protocols.status import Status, current_step, current_week, day_number, merge_stacks, protocol_status

T = date(2026, 9, 22)


def P(**kw):
    base = dict(start_date=date(2026, 9, 1), end_date=None, ended_on=None, paused=False)
    return NS(**{**base, **kw})


def test_active_ongoing():
    assert protocol_status(P(), T) is Status.ACTIVE

def test_end_date_today_is_active():
    assert protocol_status(P(end_date=T), T) is Status.ACTIVE

def test_end_date_past_is_ended():
    assert protocol_status(P(end_date=date(2026, 9, 21)), T) is Status.ENDED

def test_ended_on_wins_over_everything():
    assert protocol_status(P(ended_on=T, paused=True, start_date=date(2026, 10, 1)), T) is Status.ENDED

def test_paused_beats_scheduled():
    assert protocol_status(P(paused=True, start_date=date(2026, 10, 1)), T) is Status.PAUSED

def test_scheduled():
    assert protocol_status(P(start_date=date(2026, 9, 23)), T) is Status.SCHEDULED

def test_day_and_week():
    s = date(2026, 9, 1)
    assert day_number(s, s) == 1 and day_number(s, T) == 22
    assert current_week(s, s) == 1 and current_week(s, date(2026, 9, 7)) == 1 and current_week(s, date(2026, 9, 8)) == 2
    assert day_number(T, s) is None and current_week(T, s) is None

def test_current_step():
    steps = [NS(start_week=1, end_week=4, dose=2.5), NS(start_week=5, end_week=None, dose=5)]
    assert current_step(steps, 1).dose == 2.5
    assert current_step(steps, 4).dose == 2.5
    assert current_step(steps, 30).dose == 5
    assert current_step(steps, None) is None
    assert current_step([NS(start_week=3, end_week=4, dose=1)], 1) is None

def test_merge_stacks_dedupes_in_goal_order():
    stacks = {"a": [1, 2, 3], "b": [4, 2, 5]}
    assert merge_stacks(["b", "a"], stacks) == [4, 2, 5, 1, 3]
    assert merge_stacks(["zzz"], stacks) == []
```

- [ ] **Step 2: Run** → FAIL (module missing). **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat: protocol status and stack helpers`.

### Task 3: Builder form parsing & validation

**Files:** Create `app/protocols/forms.py`; Test `tests/test_protocol_forms.py`.

**Form field names** (what the builder JS submits):
`name, start_date, end_date, weeks, notes, titration` (value `1` when ticked), `goal` (repeated),
per item *i* (0-based, contiguous not required): `items-{i}-peptide_id` **or** `items-{i}-new_name`, `items-{i}-dose, -dose_unit, -frequency, -every_n_days, -weekdays` (repeated letters), `-time_of_day, -route, -inventory_item_id, -notes`,
per step *j*: `items-{i}-steps-{j}-start_week, -end_week, -dose`.

**Interfaces — Produces** (`app.protocols.forms`):
- `blank_state(goals: list[str], today: date) -> dict` — builder state for a new protocol (name "", start today, no items; items are filled client-side from stacks).
- `state_from_form(form: Mapping[str, list[str]]) -> dict` — raw strings back into the state shape (for re-render on error).
- `state_from_protocol(p: Protocol, *, repeat: bool = False, today: date | None = None) -> dict` — for edit, and for Repeat (name + " (repeat)", start = today, end shifted by original length, no id).
- State shape: `{"name","start_date","end_date","weeks","notes","titration": bool,"goals": [slug],"items":[{"peptide_id": str,"new_name","dose","dose_unit","frequency","every_n_days","weekdays": "MWF","time_of_day","route","inventory_item_id","notes","steps":[{"start_week","end_week","dose"}]}]}` — all scalar values strings.
- `ParsedStep(start_week:int, end_week:int|None, dose:float)`, `ParsedItem(peptide_id:int|None, new_name:str|None, dose, dose_unit:DoseUnit, frequency:Frequency, every_n_days, weekdays, time_of_day:TimeOfDay, route:Route, inventory_item_id:int|None, notes, steps:list[ParsedStep])`, `ParsedProtocol(name, start_date, end_date, notes, titration_enabled, goals, items)` dataclasses.
- `parse_protocol_form(form, *, peptide_ids: set[int], inventory_ids: set[int]) -> tuple[ParsedProtocol, dict[str, str]]` — errors keyed by field name (`"name"`, `"goal"`, `"items"`, `"items-0-dose"`, `"items-0-steps-1-end_week"`, …). When `weeks` is given and `end_date` blank, end = start + weeks·7 − 1 days. When titration is off, steps are kept only if they parse and never produce errors.

- [ ] **Step 1: Write failing tests** covering: happy path with two items + steps + weekdays; `name`/`goal`/`items` required; bad start date; end before start; weeks → end date; dose blank OK / 0 rejected; every_n_days required ≥ 2; weekdays required for `weekdays`; unknown peptide id / inventory id rejected; new_name trimmed & ≤ 120; steps overlap rejected; open-ended step only last; titration off keeps valid steps silently and drops invalid ones; `state_from_form` round-trips the submitted strings (including steps and weekdays).

Exact tests:

```python
from datetime import date

from app.models import DoseUnit, Frequency
from app.protocols.forms import parse_protocol_form, state_from_form

IDS = dict(peptide_ids={1, 2, 3}, inventory_ids={7})


def F(**pairs):
    return {k.replace("__", "-"): (v if isinstance(v, list) else [v]) for k, v in pairs.items()}


def base(**extra):
    return F(name="Cut", start_date="2026-09-01", goal=["fat-loss"],
             items__0__peptide_id="1", items__0__dose="2.5", items__0__dose_unit="mg",
             items__0__frequency="weekly", items__0__time_of_day="am", items__0__route="subq", **extra)


def test_happy_path():
    p, errors = parse_protocol_form(base(
        titration="1", end_date="", weeks="12",
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="4", items__0__steps__0__dose="2.5",
        items__0__steps__1__start_week="5", items__0__steps__1__end_week="", items__0__steps__1__dose="5",
        items__1__peptide_id="2", items__1__frequency="weekdays", items__1__weekdays=["M", "W", "F"],
        items__1__inventory_item_id="7"), **IDS)
    assert errors == {}
    assert p.end_date == date(2026, 11, 23)
    assert p.items[0].dose_unit is DoseUnit.MG and p.items[0].frequency is Frequency.WEEKLY
    assert [(s.start_week, s.end_week, s.dose) for s in p.items[0].steps] == [(1, 4, 2.5), (5, None, 5.0)]
    assert p.items[1].weekdays == "MWF" and p.items[1].inventory_item_id == 7 and p.items[1].dose is None


def test_required_fields():
    _, e = parse_protocol_form(F(name=" ", start_date="2026-09-01"), **IDS)
    assert set(e) >= {"name", "goal", "items"}


def test_dates():
    _, e = parse_protocol_form(base(start_date="nope"), **IDS)
    assert "start_date" in e
    _, e = parse_protocol_form(base(end_date="2026-08-01"), **IDS)
    assert "end_date" in e
    _, e = parse_protocol_form(base(weeks="0"), **IDS)
    assert "weeks" in e


def test_item_rules():
    _, e = parse_protocol_form(base(items__0__dose="0"), **IDS)
    assert "items-0-dose" in e
    _, e = parse_protocol_form(base(items__0__frequency="every_n_days", items__0__every_n_days="1"), **IDS)
    assert "items-0-every_n_days" in e
    _, e = parse_protocol_form(base(items__0__frequency="weekdays"), **IDS)
    assert "items-0-weekdays" in e
    _, e = parse_protocol_form(base(items__0__peptide_id="99"), **IDS)
    assert "items-0-peptide_id" in e
    _, e = parse_protocol_form(base(items__0__inventory_item_id="8"), **IDS)
    assert "items-0-inventory_item_id" in e


def test_new_peptide_name():
    p, e = parse_protocol_form(base(items__1__new_name="  KPV-X  "), **IDS)
    assert e == {} and p.items[1].new_name == "KPV-X" and p.items[1].peptide_id is None
    _, e = parse_protocol_form(base(items__1__new_name="x" * 121), **IDS)
    assert "items-1-new_name" in e


def test_steps_validation_when_titration_on():
    _, e = parse_protocol_form(base(titration="1",
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="", items__0__steps__0__dose="1",
        items__0__steps__1__start_week="3", items__0__steps__1__end_week="4", items__0__steps__1__dose="2"), **IDS)
    assert "items-0-steps-0-end_week" in e  # open end only on last step
    _, e = parse_protocol_form(base(titration="1",
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="4", items__0__steps__0__dose="1",
        items__0__steps__1__start_week="3", items__0__steps__1__end_week="6", items__0__steps__1__dose="2"), **IDS)
    assert "items-0-steps-1-start_week" in e  # overlap


def test_titration_off_keeps_valid_steps_silently():
    p, e = parse_protocol_form(base(
        items__0__steps__0__start_week="1", items__0__steps__0__end_week="4", items__0__steps__0__dose="1",
        items__0__steps__1__start_week="x", items__0__steps__1__dose="2"), **IDS)
    assert e == {} and not p.titration_enabled
    assert [(s.start_week, s.dose) for s in p.items[0].steps] == [(1, 1.0)]


def test_state_round_trip():
    form = base(titration="1", items__0__steps__0__start_week="1", items__0__steps__0__end_week="4",
                items__0__steps__0__dose="bad", items__1__new_name="Thing", items__1__weekdays=["M", "F"])
    s = state_from_form(form)
    assert s["name"] == "Cut" and s["titration"] is True and s["goals"] == ["fat-loss"]
    assert s["items"][0]["steps"][0]["dose"] == "bad"
    assert s["items"][1]["new_name"] == "Thing" and s["items"][1]["weekdays"] == "MF"
```


- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat: protocol builder form parsing and validation`.

### Task 4: Protocols page, actions, API, nav

**Files:** Create `app/routers/protocols.py`, `app/templates/protocols/list.html`, `app/static/js/protocols.js`; Modify `app/main.py` (include router), `app/templates/base.html` (live Protocols link), `app/templating.py` (label filters), `app/static/css/app.css`; Test `tests/test_protocols.py`.

**Interfaces — Produces:**
- `get_today() -> date` dependency in `app/routers/protocols.py` (tests override via `app.dependency_overrides[get_today]`).
- Routes: `GET /protocols`, `POST /protocols/{id}/pause|resume|end|delete` (303 → `/protocols`, or back to the edit page when `next=edit`), `GET /api/protocols`, `GET /api/protocols/{id}`, `GET /api/peptides`.
- `describe_item(item) -> str` e.g. `"250 mcg · Daily · AM · SubQ"` / `"Dose not set · Every 3 days · SubQ"` / weekdays `"Mon, Wed, Fri"`; `any` time omitted.
- Template filters: `goal_label`, `status_label` (via `Status.label`).

Page layout per spec §5: Active card grid with `.ribbon` "Active" element (CSS: yellow `#facc15` band rotated −45° clipped in the card's top-left corner), goal cards (`button.goal-card[data-goal]` with `aria-pressed`), **Build protocol** link updated by JS to `/protocols/new?goal=…`, Saved protocols table with filter chips (JS, `data-status` on rows). Share: `<button disabled title="Email sharing coming soon">Share</button>` on cards and rows.

- [ ] **Step 1: Write failing integration tests** (helper `make_protocol(db, **kw)` inserts a Protocol with one goal and one item for peptide "BPC-157"):
  - `test_page_sections_and_goal_cards` — 8 goal labels present; "No active protocols" empty state.
  - `test_active_card_has_ribbon_and_details` — active protocol shows `class="ribbon"`, name, `250 mcg · Daily · AM · SubQ`, `Day 22`, `Ongoing`, disabled Share with the tooltip.
  - `test_status_grouping` — with `get_today` fixed to 2026-09-22: active one in cards; scheduled/paused/ended ones in the Saved table with their status tags, not in cards.
  - `test_pause_resume_end_delete` — each action changes state as spec §4; delete removes items (query ProtocolItem count 0).
  - `test_inventory_delete_unlinks_protocol_item` — link item to an inventory row, delete via `/inventory/{id}/delete`, protocol item still exists with `inventory_item_id is None`.
  - `test_api_shapes` — `/api/protocols` includes `status`, `goals`, `items[0].peptide`, titration steps only when enabled; `/api/peptides` length ≥ 105.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat: protocols page with active cards, goal cards and saved protocols`.

### Task 5: Builder page — create, edit, repeat

**Files:** Modify `app/routers/protocols.py`; Create `app/templates/protocols/builder.html`, `app/static/js/protocol-builder.js`; Modify `app/static/css/app.css`; Test `tests/test_protocols.py` (append).

**Interfaces — Consumes:** Task 3 `parse_protocol_form`, `blank_state`, `state_from_form`, `state_from_protocol`; Task 2 `merge_stacks`, `protocol_status`.
**Produces:** `GET /protocols/new` (query `goal` repeated), `POST /protocols`, `GET /protocols/{id}/edit`, `POST /protocols/{id}`, `GET /protocols/{id}/repeat`; `save_protocol(session, protocol, parsed) -> Protocol` (replaces goals/items/steps; resolves `new_name` case-insensitively to an existing peptide or creates `source=custom`).

Builder page embeds `<script type="application/json" id="builder-data">` with keys: `state`, `errors`, `goals` (`[{slug,label,description}]`), `stacks` (`{slug: [peptide_id…]}`), `peptides` (`[{id,name,aliases}]`), `inventory` (`[{id,name,vial_size_mg,medium}]`), `options` (unit/frequency/time/route value+label lists), `is_new` (bool). JS renders: goal chips → suggested peptides (merged stacks; on a new protocol with no items yet, all suggestions pre-ticked) → add-peptide search (`<datalist>` over library names; unknown name → "Add '<name>' to library") → detail rows per ticked peptide (inputs named per Task 3) → protocol settings (name auto-suggested from goal labels until the user edits it; weeks ↔ end date) → titration toggle revealing per-row step tables (steps stay in the DOM when hidden so they are submitted). Errors: `errors[fieldName]` rendered under the matching input; a top alert when any exist.

- [ ] **Step 1: Write failing integration tests:**
  - `test_new_builder_embeds_merged_suggestions` — `/protocols/new?goal=fat-loss&goal=glp1-weight` → builder-data `stacks` has both slugs; `state.goals` == both.
  - `test_create_protocol` — POST valid form with two items, titration + steps → 303; DB has protocol with goals, items in order, steps; appears as Active card.
  - `test_invalid_submit_preserves_state` — POST with blank name and bad step dose, titration on, two items, weekdays → 422; embedded `state` equals `state_from_form` of what was posted; `errors` contains `name` and the step field.
  - `test_custom_peptide_reuses_existing_case_insensitive` — `new_name=" bpc-157 "` → item uses the existing BPC-157 id; peptide count unchanged. `new_name="Brand New"` → one `custom` peptide created.
  - `test_edit_protocol` — edit page state matches protocol; POST changes replace items (old item ids gone).
  - `test_repeat_prefills_and_does_not_save` — `/protocols/{id}/repeat` → 200, state name ends " (repeat)", start = today, end shifted by original length; protocol count unchanged; form action is `/protocols`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Manual browser check** (desktop + 375px): goal cards select/deselect, Build link, builder suggestions pre-ticked, untick/add peptide, titration toggle, save → Active card with ribbon; pause → moves to Saved; repeat; delete confirm; Share disabled.
- [ ] **Step 6: Commit** `feat: goal-driven protocol builder with titration steps`.

### Task 6: Docs

**Files:** Modify `README.md` (Status line), `docs/ROADMAP.md` (mark Protocols/Titration structure shipped, note Library import next).

- [ ] **Step 1:** Update text. **Step 2:** Full test suite PASS. **Step 3: Commit + push** `docs: protocols shipped`.

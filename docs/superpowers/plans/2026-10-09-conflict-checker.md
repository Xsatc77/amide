# Dose-level conflict checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warn, from the doses and schedules a person has entered, when a protocol looks worth asking a prescriber about, with an alerts icon beside the print icon on each protocol and a panel in the builder.

**Architecture:** A pure module `app/library/conflicts.py` takes plain data (items, medicines) and returns findings from four checks (dose vs library range, peptide vs peptide, dose-sensitive medicine cautions, timing). A thin loader `app/protocols/alerts.py` builds that data from the database or from a parsed builder form. Routes return the report as a page, as JSON, and as a builder preview; a small script shows it in a dialog and in the builder.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Jinja templates, vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-conflict-checker-design.md`

## Global Constraints

- Informational only: a finding means "worth asking about", never "safe" or "unsafe"; an empty report does not mean there is no interaction. Nothing blocks saving.
- Every report ends with: "Worth asking your prescriber or pharmacist. Informational only, not medical advice."
- No vendor names or price data anywhere (repo rule). Wording is the project's own.
- Rules stay short and conservative, as tuples in code, reusing `app/library/interactions.py` word lists.
- No outside lookup or AI call.
- Test command: `.venv/Scripts/python.exe -m pytest -q -p no:warnings` (about 2 minutes; do not edit app files while it runs).
- Commit means commit and push (plain push, never force), only when the owner says "commit". Plan tasks below end with a commit step for the executor to run only after that instruction; until then leave work uncommitted.

## Clarifications of the spec (rulings)

- **Timing check.** The Medicines list has no time of day, so "same time of day as a listed sedative" cannot be known. The timing check is therefore: (a) a sedating peptide (DSIP) in a daytime slot (Fasting, Waking, AM, Pre-workout, Post-workout) when the person lists a sedative medicine; (b) two same-class or "avoid" peptides in the same time slot are a Caution, in different slots a Note.
- **Peptide vs peptide** also uses the library's own "avoid" stack relations (`peptide_stack_relations`, relation `avoid`), which carry a written note.
- **Report address.** `GET /protocols/{id}/alerts` is the printable page, `GET /protocols/{id}/alerts.json` the data for the dialog.

## Review Focus

- A protocol with one item, or items with no dose (as needed): no crash, no findings except the dose-range note.
- Peptide with no library dose range: a Note saying there is nothing to compare with, never a Caution.
- Mixed units (mg dose, mcg library range; IU against mg): converted when possible, otherwise a Note that they cannot be compared.
- A protocol shared with the viewer: medicines used are the viewer's own, and another person's protocol is a 404.
- Builder preview with an unfinished form (errors, new peptide with no library entry): returns an empty, "incomplete" report, never a 500.

---

### Task 1: The checker (pure)

**Files:**
- Create: `app/library/conflicts.py`
- Test: `tests/test_conflicts.py`

**Interfaces:**
- Consumes: `app.library.interactions` (`cautions_for`, `GLP1`, `MELANO`, `SEDATING`, `SEDATIVE`, `_has`)
- Produces: `ItemData`, `MedicineData`, `Finding` dataclasses; `check(items: list[ItemData], medicines: list[MedicineData]) -> list[Finding]`; `Finding.as_dict()`; constant `DISCLAIMER`.

- [ ] **Step 1: Write the failing tests**

```python
"""The conflict checker: each of the four checks has a case that must fire and a case that must not."""

from app.library.conflicts import DISCLAIMER, ItemData, MedicineData, check


def item(pid, name, **kw):
    return ItemData(peptide_id=pid, name=name, **kw)


def kinds(findings, check_name):
    return [f for f in findings if f.check == check_name]


def test_dose_above_the_library_high_is_a_caution_and_within_range_is_quiet():
    hot = item(1, "Alpha", dose=12, unit="mg", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    ok = item(2, "Beta", dose=5, unit="mg", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    found = kinds(check([hot], []), "dose")
    assert [f.severity for f in found] == ["caution"] and "above the library's high dose" in found[0].message
    assert kinds(check([ok], []), "dose") == []


def test_units_are_converted_and_unlike_units_are_a_note():
    mcg = item(1, "Alpha", dose=12000, unit="mcg", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    assert kinds(check([mcg], []), "dose")[0].severity == "caution"
    iu = item(2, "Beta", dose=5, unit="IU", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    found = kinds(check([iu], []), "dose")
    assert found[0].severity == "note" and "cannot be compared" in found[0].message


def test_no_library_range_is_a_note_and_a_missing_dose_is_skipped():
    assert kinds(check([item(1, "Alpha", dose=3, unit="mg")], []), "dose")[0].severity == "note"
    assert check([item(1, "Alpha", dose=None, unit="mg", lib_high=10, lib_unit="mg")], []) == []


def test_a_titration_that_more_than_doubles_and_one_well_below_the_low_dose():
    jump = item(1, "Alpha", dose=1, unit="mg", steps=(1, 2.5, 3), lib_low=0.5, lib_mid=2, lib_high=10, lib_unit="mg")
    assert any("more than double" in f.message for f in kinds(check([jump], []), "dose"))
    smooth = item(2, "Beta", dose=1, unit="mg", steps=(1, 2, 3), lib_low=0.5, lib_mid=2, lib_high=10, lib_unit="mg")
    assert not any("more than double" in f.message for f in kinds(check([smooth], []), "dose"))
    low = item(3, "Gamma", dose=0.1, unit="mg", lib_low=1, lib_mid=2, lib_high=10, lib_unit="mg")
    assert "well below" in kinds(check([low], []), "dose")[0].message


def test_two_peptides_of_one_class_are_a_caution_in_one_slot_and_a_note_in_two():
    a = item(1, "Semaglutide", dose=1, unit="mg", time_of_day="am")
    b = item(2, "Tirzepatide", dose=1, unit="mg", time_of_day="am")
    assert [f.severity for f in kinds(check([a, b], []), "stack")] == ["caution"]
    c = item(3, "Tirzepatide", dose=1, unit="mg", time_of_day="pm")
    assert [f.severity for f in kinds(check([a, c], []), "stack")] == ["note"]
    d = item(4, "BPC-157", dose=1, unit="mg")
    assert kinds(check([a, d], []), "stack") == []


def test_the_librarys_avoid_notes_flag_a_pair_once():
    a = item(1, "Alpha Peptide", avoid=(("Beta Peptide", "The library says do not combine these."),))
    b = item(2, "Beta Peptide")
    found = kinds(check([a, b], []), "stack")
    assert len(found) == 1 and "do not combine" in found[0].message
    assert kinds(check([a], []), "stack") == []


def test_medicine_cautions_get_stronger_above_the_library_mid_dose_and_quote_the_medicine_dose():
    meds = [MedicineData("Metformin", "500 mg twice daily")]
    low = item(1, "Semaglutide", dose=0.25, unit="mg", lib_low=0.25, lib_mid=1, lib_high=2.4, lib_unit="mg")
    high = item(1, "Semaglutide", dose=2, unit="mg", lib_low=0.25, lib_mid=1, lib_high=2.4, lib_unit="mg")
    assert kinds(check([low], meds), "medicine")[0].severity == "note"
    found = kinds(check([high], meds), "medicine")[0]
    assert found.severity == "caution" and "Metformin (500 mg twice daily)" in found.message and "matters more" in found.message
    assert kinds(check([high], []), "medicine") == []


def test_a_sedating_peptide_in_a_daytime_slot_with_a_listed_sedative_is_a_timing_caution():
    meds = [MedicineData("Zolpidem")]
    day = item(1, "DSIP", dose=0.1, unit="mg", time_of_day="am")
    night = item(2, "DSIP", dose=0.1, unit="mg", time_of_day="bedtime")
    assert kinds(check([day], meds), "timing")[0].severity == "caution"
    assert kinds(check([night], meds), "timing") == []
    assert kinds(check([day], []), "timing") == []


def test_findings_are_sorted_cautions_first_serialize_and_the_disclaimer_exists():
    hot = item(1, "Alpha", dose=12, unit="mg", lib_low=1, lib_mid=5, lib_high=10, lib_unit="mg")
    note = item(2, "Beta", dose=3, unit="mg")
    found = check([note, hot], [])
    assert [f.severity for f in found] == ["caution", "note"]
    assert set(found[0].as_dict()) == {"severity", "check", "peptide", "other", "message", "peptide_id"}
    assert "prescriber or pharmacist" in DISCLAIMER and check([], []) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_conflicts.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.library.conflicts'`

- [ ] **Step 3: Write the module**

```python
"""Dose- and schedule-aware cautions for a protocol: dose against the library's range, peptide against peptide, medicine cautions that
grow with the dose, and timing. Informational only: a finding means "worth asking about", never "safe" or "unsafe", and no finding does
not mean there is no interaction. Pure functions: no database, no web."""

from dataclasses import dataclass

from app.library.interactions import GLP1, MELANO, SEDATING, SEDATIVE, _has, cautions_for

DISCLAIMER = "Worth asking your prescriber or pharmacist. Informational only, not medical advice."

GH_SECRETAGOGUES = ("cjc", "ipamorelin", "sermorelin", "tesamorelin", "mk-677", "ibutamoren", "ghrp", "hexarelin")
CLASSES = (("GLP-1 medicines", GLP1), ("growth hormone secretagogues", GH_SECRETAGOGUES), ("melanocortin peptides", MELANO))
DAYTIME = {"fasting", "waking", "am", "pre_workout", "post_workout"}
_TO_MCG = {"mcg": 1.0, "mg": 1000.0}
_ORDER = {"caution": 0, "note": 1}


@dataclass(frozen=True)
class ItemData:
    peptide_id: int
    name: str
    aliases: str | None = None
    dose: float | None = None
    unit: str = "mg"                       # mg, mcg or IU
    time_of_day: str = "any"               # a TimeOfDay value
    steps: tuple = ()                      # titration doses in week order
    lib_low: float | None = None
    lib_mid: float | None = None
    lib_high: float | None = None
    lib_unit: str | None = None
    avoid: tuple = ()                      # (partner name, library note) pairs from the card's Avoid list


@dataclass(frozen=True)
class MedicineData:
    name: str
    dose_text: str | None = None           # quoted back, never parsed


@dataclass(frozen=True)
class Finding:
    severity: str                          # "caution" or "note"
    check: str                             # "dose", "stack", "medicine" or "timing"
    peptide: str
    message: str
    peptide_id: int | None = None
    other: str | None = None

    def as_dict(self) -> dict:
        return {"severity": self.severity, "check": self.check, "peptide": self.peptide, "other": self.other,
                "message": self.message, "peptide_id": self.peptide_id}


def _fmt(value: float) -> str:
    return f"{value:g}"


def _convert(value: float, unit: str, to_unit: str | None) -> float | None:
    if unit == to_unit:
        return value
    if unit in _TO_MCG and to_unit in _TO_MCG:
        return value * _TO_MCG[unit] / _TO_MCG[to_unit]
    return None


def _doses(item: ItemData) -> list[float]:
    return [d for d in (item.dose, *item.steps) if d]


def _check_dose(item: ItemData) -> list[Finding]:
    doses = _doses(item)
    if not doses:
        return []
    out = []

    def add(severity, message):
        out.append(Finding(severity, "dose", item.name, message, item.peptide_id))

    peak, floor = max(doses), min(doses)
    if item.lib_unit is None or (item.lib_low is None and item.lib_high is None):
        add("note", f"{item.name}: the library has no dose range to compare your dose with.")
    else:
        peak_c, floor_c = _convert(peak, item.unit, item.lib_unit), _convert(floor, item.unit, item.lib_unit)
        if peak_c is None:
            add("note", f"{item.name}: your dose is in {item.unit} and the library's range is in {item.lib_unit}, so they cannot be compared.")
        else:
            if item.lib_high is not None and peak_c > item.lib_high:
                add("caution", f"{item.name}: {_fmt(peak)} {item.unit} is above the library's high dose of {_fmt(item.lib_high)} {item.lib_unit}.")
            if item.lib_low is not None and floor_c < item.lib_low / 2:
                add("note", f"{item.name}: {_fmt(floor)} {item.unit} is well below the library's low dose of {_fmt(item.lib_low)} {item.lib_unit}.")
    for before, after in zip(item.steps, item.steps[1:]):
        if before and after > 2 * before:
            add("caution", f"{item.name}: the titration goes from {_fmt(before)} to {_fmt(after)} {item.unit}, more than double the previous step.")
    return out


def _names_match(partner: str, item: ItemData) -> bool:
    partner = partner.casefold().strip()
    text = f"{item.name} {item.aliases or ''}".casefold()
    return len(partner) >= 3 and (partner in text or item.name.casefold() in partner)


def _check_stack(items: list[ItemData]) -> list[Finding]:
    out, seen = [], set()
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a.peptide_id == b.peptide_id:
                continue
            same_slot = a.time_of_day == b.time_of_day
            for label, words in CLASSES:
                if _has(f"{a.name} {a.aliases or ''}", words) and _has(f"{b.name} {b.aliases or ''}", words):
                    when = "in the same time slot" if same_slot else "in different time slots"
                    out.append(Finding("caution" if same_slot else "note", "stack", a.name,
                                       f"{a.name} and {b.name} are both {label}, scheduled {when}. Using two together has little research.",
                                       a.peptide_id, b.name))
            for first, second in ((a, b), (b, a)):
                for partner, note in first.avoid:
                    key = (frozenset((first.peptide_id, second.peptide_id)), note)
                    if _names_match(partner, second) and key not in seen:
                        seen.add(key)
                        out.append(Finding("caution" if same_slot else "note", "stack", first.name,
                                           f"{first.name} with {second.name}: {note}", first.peptide_id, second.name))
    return out


def _check_medicines(items: list[ItemData], medicines: list[MedicineData]) -> list[Finding]:
    out = []
    by_name = {m.name: m for m in medicines}
    for item in items:
        doses = _doses(item)
        peak = _convert(max(doses), item.unit, item.lib_unit) if doses and item.lib_unit else None
        raised = peak is not None and item.lib_mid is not None and peak > item.lib_mid
        for found in cautions_for(item.name, item.aliases, [m.name for m in medicines]):
            med = by_name[found["medicine"]]
            label = f"{med.name} ({med.dose_text})" if med.dose_text else med.name
            text = f"{item.name} with {label}: {found['note']}"
            if raised:
                text += f" At the dose in your protocol ({_fmt(max(doses))} {item.unit}) this matters more."
            out.append(Finding("caution" if raised else "note", "medicine", item.name, text, item.peptide_id, med.name))
    return out


def _check_timing(items: list[ItemData], medicines: list[MedicineData]) -> list[Finding]:
    sedative = next((m for m in medicines if _has(m.name, SEDATIVE)), None)
    if sedative is None:
        return []
    return [Finding("caution", "timing", item.name,
                    f"{item.name} is sedating and is scheduled in a daytime slot while you list {sedative.name}. Drowsiness could add up when you are driving or working.",
                    item.peptide_id, sedative.name)
            for item in items if item.time_of_day in DAYTIME and _has(f"{item.name} {item.aliases or ''}", SEDATING)]


def check(items: list[ItemData], medicines: list[MedicineData]) -> list[Finding]:
    """Every finding for these items and medicines, cautions first (each group keeps the protocol's order)."""
    found = []
    for item in items:
        found += _check_dose(item)
    found += _check_stack(items) + _check_medicines(items, medicines) + _check_timing(items, medicines)
    return sorted(found, key=lambda f: _ORDER[f.severity])
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_conflicts.py`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit (only after the owner says "commit")**

```bash
git add app/library/conflicts.py tests/test_conflicts.py
git commit -m "feat: conflict checker (dose, stack, medicine, timing)"
```

---

### Task 2: Medicine dose text

**Files:**
- Create: `migrations/versions/0057_medicine_dose_text.py`
- Modify: `app/models.py` (`UserMedicine`), `app/routers/settings.py` (`add_medicine`), `app/templates/settings/settings.html` (medicines section)
- Test: `tests/test_medicines.py`

**Interfaces:**
- Consumes: existing `/settings/medicines` form (fields `name`, `notes`)
- Produces: `UserMedicine.dose_text: str | None` (80 characters); form field `dose_text`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_medicines.py`)

```python
def test_a_medicine_can_carry_a_dose_text_shown_in_the_list(client, db, me):
    from app.models import UserMedicine
    r = client.post("/settings/medicines", data={"name": "Dose Text Med", "dose_text": " 10 mg daily "}, follow_redirects=False)
    assert r.status_code == 303
    row = db.query(UserMedicine).filter_by(owner_id=me, name="Dose Text Med").one()
    assert row.dose_text == "10 mg daily" and "10 mg daily" in client.get("/settings").text
    assert client.post("/settings/medicines", data={"name": "Too Long Dose", "dose_text": "x" * 81}).status_code == 422
    db.delete(row)
    db.commit()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_medicines.py -k dose_text`
Expected: FAIL (`dose_text` is not a column)

- [ ] **Step 3: Implement**

`app/models.py`, in `UserMedicine` after `notes`:

```python
    dose_text: Mapped[str | None] = mapped_column(String(80))      # optional, quoted back in alerts, never parsed
```

`migrations/versions/0057_medicine_dose_text.py`:

```python
"""settings: optional dose text on a listed medicine

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0057'
down_revision: Union[str, None] = '0056'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_medicines', sa.Column('dose_text', sa.String(80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('user_medicines') as batch:
        batch.drop_column('dose_text')
```

`app/routers/settings.py` `add_medicine`: read `dose_text = " ".join(str(form.get("dose_text") or "").split()) or None`; extend the length check to `(dose_text and len(dose_text) > 80)`; create `UserMedicine(owner_id=uid, name=name, notes=notes, dose_text=dose_text)`.

`app/templates/settings/settings.html`: in the medicine list item show `{% if m.dose_text %}<span class="muted small">{{ m.dose_text }}</span>{% endif %}` beside the name, and add `<label class="field"><span>Dose (optional, for example 10 mg daily)</span><input name="dose_text" maxlength="80"></label>` to the add form.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_medicines.py`
Expected: PASS

- [ ] **Step 5: Commit (only after the owner says "commit")**

```bash
git add migrations/versions/0057_medicine_dose_text.py app/models.py app/routers/settings.py app/templates/settings/settings.html tests/test_medicines.py
git commit -m "feat: optional dose text on a listed medicine"
```

---

### Task 3: Loader and routes

**Files:**
- Create: `app/protocols/alerts.py`, `app/templates/protocols/alerts.html`
- Modify: `app/routers/protocols.py` (new routes after `print_protocol`, before `_builder_data`)
- Test: `tests/test_protocol_alerts.py`

**Interfaces:**
- Consumes: `app.library.conflicts` (Task 1), `UserMedicine.dose_text` (Task 2), `_protocol_query`, `_shared_protocol_query`, `_parse`, `_read_form`, `get_today`
- Produces:
  - `alerts.item_data(peptide, dose, unit, time_of_day, steps) -> ItemData`
  - `alerts.medicines_for(session, uid) -> list[MedicineData]`
  - `alerts.for_protocol(session, p, viewer_id) -> list[Finding]`
  - `alerts.for_parsed(session, parsed, viewer_id) -> list[Finding]`
  - Routes: `GET /protocols/{id}/alerts` (page), `GET /protocols/{id}/alerts.json` (`{"findings": [...], "disclaimer": str}`), `POST /protocols/alerts-preview` (form-encoded builder form; same JSON plus `"incomplete": bool`).

- [ ] **Step 1: Write the failing tests**

```python
"""Alerts for a saved protocol and for the builder form."""

from datetime import date

import pytest

from app.db import SessionLocal
from app.models import DoseUnit, Frequency, Peptide, PeptideSource, Protocol, ProtocolItem, Route, TimeOfDay, UserMedicine
from photo_helpers import other_client


@pytest.fixture
def protocol_id(me):
    with SessionLocal() as s:
        hot = Peptide(name="Alerts Test Alpha", source=PeptideSource.CUSTOM, dose_low=1, dose_mid=5, dose_high=10, dose_unit=DoseUnit.MG)
        bare = Peptide(name="Alerts Test Beta", source=PeptideSource.CUSTOM)
        s.add_all([hot, bare])
        s.flush()
        p = Protocol(name="Alerts Protocol", start_date=date.today(), owner_id=me)
        s.add(p)
        s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=hot.id, dose=12, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, time_of_day=TimeOfDay.AM, route=Route.SUBQ, position=0))
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=bare.id, dose=3, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ, position=1))
        s.commit()
        pid, ids = p.id, [hot.id, bare.id]
    yield pid
    with SessionLocal() as s:
        s.query(Protocol).filter_by(id=pid).delete()
        s.query(Peptide).filter(Peptide.id.in_(ids)).delete()
        s.commit()


def test_the_json_report_lists_cautions_first_with_the_disclaimer(client, protocol_id):
    body = client.get(f"/protocols/{protocol_id}/alerts.json").json()
    assert [f["severity"] for f in body["findings"]] == ["caution", "note"]
    assert "above the library's high dose" in body["findings"][0]["message"] and "prescriber or pharmacist" in body["disclaimer"]


def test_the_page_shows_the_same_report(client, protocol_id):
    page = client.get(f"/protocols/{protocol_id}/alerts")
    assert page.status_code == 200 and "above the library" in page.text and "Alerts Protocol" in page.text


def test_another_persons_protocol_is_a_404_and_a_shared_one_uses_the_viewers_medicines(client, protocol_id, me):
    assert other_client().get(f"/protocols/{protocol_id}/alerts.json").status_code == 404


def test_medicines_shape_the_report(client, db, me, protocol_id):
    med = UserMedicine(owner_id=me, name="Alerts Test Med", dose_text="5 mg")
    db.add(med)
    db.commit()
    try:
        assert client.get(f"/protocols/{protocol_id}/alerts.json").status_code == 200
    finally:
        db.delete(med)
        db.commit()


def test_preview_reads_the_builder_form_and_an_unfinished_form_is_empty_not_an_error(client, protocol_id):
    assert client.post("/protocols/alerts-preview", data={"name": ""}).json() == {"findings": [], "disclaimer": pytest.approx("") or client.post("/protocols/alerts-preview", data={"name": ""}).json()["disclaimer"], "incomplete": True}
```

Replace the last test with this simpler, exact version:

```python
def test_preview_reads_the_builder_form_and_an_unfinished_form_is_empty_not_an_error(client, protocol_id):
    body = client.post("/protocols/alerts-preview", data={"name": ""}).json()
    assert body["findings"] == [] and body["incomplete"] is True and "prescriber or pharmacist" in body["disclaimer"]
```

A preview with real data is covered by the builder test in Task 4 (it posts the form that the builder produces). Also add here a unit-level check that `for_parsed` skips an item with no library peptide:

```python
def test_preview_skips_a_new_peptide_with_no_library_entry(client, db):
    from app.protocols.alerts import for_parsed
    from app.protocols.forms import ParsedItem, ParsedProtocol
    item = ParsedItem(peptide_id=None, new_name="Brand New", dose=5, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, every_n_days=None,
                      weekdays=None, time_of_day=TimeOfDay.ANY, route=Route.SUBQ, inventory_item_id=None, notes=None)
    parsed = ParsedProtocol(name="x", start_date=None, end_date=None, notes=None, titration_enabled=False, goals=[], items=[item])
    assert for_parsed(db, parsed, 1) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_protocol_alerts.py`
Expected: FAIL (404 on the new routes / import error)

- [ ] **Step 3: Implement**

`app/protocols/alerts.py`:

```python
"""Builds the conflict checker's plain data from a saved protocol or a builder form, and runs it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.library.conflicts import ItemData, MedicineData, check
from app.models import Peptide, Protocol, UserMedicine


def item_data(peptide: Peptide, dose, unit: str, time_of_day: str, steps=()) -> ItemData:
    avoid = tuple((r.partner_name, r.note) for r in peptide.stack_relations if r.relation.value == "avoid")
    return ItemData(peptide_id=peptide.id, name=peptide.name, aliases=peptide.aliases, dose=dose, unit=unit, time_of_day=time_of_day,
                    steps=tuple(steps), lib_low=peptide.dose_low, lib_mid=peptide.dose_mid, lib_high=peptide.dose_high,
                    lib_unit=peptide.dose_unit.value if peptide.dose_unit else None, avoid=avoid)


def medicines_for(session: Session, uid: int) -> list[MedicineData]:
    rows = session.scalars(select(UserMedicine).where(UserMedicine.owner_id == uid).order_by(UserMedicine.name)).all()
    return [MedicineData(m.name, m.dose_text) for m in rows]


def for_protocol(session: Session, p: Protocol, viewer_id: int):
    """Findings for a saved protocol, using the viewer's own medicines (a shared protocol is checked against the person looking)."""
    items = [item_data(it.peptide, it.dose, it.dose_unit.value, it.time_of_day.value,
                       [s.dose for s in it.steps] if p.titration_enabled else []) for it in p.items]
    return check(items, medicines_for(session, viewer_id))


def for_parsed(session: Session, parsed, viewer_id: int):
    """Findings for a builder form that has parsed cleanly. Items for a peptide that is not in the library yet are skipped."""
    items = []
    for it in parsed.items:
        peptide = session.get(Peptide, it.peptide_id) if it.peptide_id else None
        if peptide is not None:
            items.append(item_data(peptide, it.dose, it.dose_unit.value, it.time_of_day.value,
                                   [s.dose for s in it.steps] if parsed.titration_enabled else []))
    return check(items, medicines_for(session, viewer_id))
```

`app/routers/protocols.py`: add `from app.library.conflicts import DISCLAIMER` and `from app.protocols import alerts as alerts_mod`; after `print_protocol` add:

```python
def _alert_protocol(session: Session, protocol_id: int, uid: int) -> Protocol:
    p = session.scalar(_protocol_query(uid).where(Protocol.id == protocol_id)) or session.scalar(_shared_protocol_query(uid).where(Protocol.id == protocol_id))
    if p is None:
        raise HTTPException(404, "Protocol not found")
    return p


@router.get("/protocols/{protocol_id}/alerts.json")
def protocol_alerts_json(protocol_id: int, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    found = alerts_mod.for_protocol(session, _alert_protocol(session, protocol_id, uid), uid)
    return JSONResponse({"findings": [f.as_dict() for f in found], "disclaimer": DISCLAIMER}, headers={"Cache-Control": "no-store"})


@router.get("/protocols/{protocol_id}/alerts")
def protocol_alerts_page(protocol_id: int, request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    p = _alert_protocol(session, protocol_id, uid)
    found = alerts_mod.for_protocol(session, p, uid)
    return templates.TemplateResponse(request, "protocols/alerts.html", {"p": p, "findings": [f.as_dict() for f in found], "disclaimer": DISCLAIMER})


@router.post("/protocols/alerts-preview")
async def protocol_alerts_preview(request: Request, session: Session = Depends(get_session), uid: int = Depends(current_user_id)):
    """Alerts for the builder form as it stands. An unfinished form (it would not save) gets an empty report."""
    parsed, errors = _parse(session, await _read_form(request), uid)
    found = [] if errors else [f.as_dict() for f in alerts_mod.for_parsed(session, parsed, uid)]
    return JSONResponse({"findings": found, "disclaimer": DISCLAIMER, "incomplete": bool(errors)}, headers={"Cache-Control": "no-store"})
```

(Ensure `JSONResponse` is imported from `fastapi.responses` in that file.)

`app/templates/protocols/alerts.html`:

```jinja
{% extends "base.html" %}
{% block title %}Alerts: {{ p.name }}{% endblock %}
{% block content %}
<article class="legal">
  <h1>Alerts: {{ p.name }}</h1>
  {% if findings %}
  <ul class="alert-list">
    {% for f in findings %}
    <li class="alert-item alert-{{ f.severity }}"><strong>{{ 'Caution' if f.severity == 'caution' else 'Note' }}</strong> {{ f.message }}
      {% if f.peptide_id %}<a href="/library/{{ f.peptide_id }}">Library card</a>{% endif %}</li>
    {% endfor %}
  </ul>
  {% else %}<p>No alerts. That does not mean there is no interaction; the checks are short on purpose.</p>{% endif %}
  <p class="small muted">{{ disclaimer }}</p>
  <p><a class="btn" href="/protocols">Back to protocols</a></p>
</article>
{% endblock %}
```

(Check that the library detail route is `/library/{id}`; adjust the link if it differs.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_protocol_alerts.py tests/test_protocols.py`
Expected: PASS

- [ ] **Step 5: Commit (only after the owner says "commit")**

```bash
git add app/protocols/alerts.py app/templates/protocols/alerts.html app/routers/protocols.py tests/test_protocol_alerts.py
git commit -m "feat: protocol alerts report (page, JSON, builder preview)"
```

---

### Task 4: Alerts icon, dialog and builder panel

**Files:**
- Create: `app/static/js/protocol-alerts.js`
- Modify: `app/routers/protocols.py` (`list_protocols` passes `alert_counts`), `app/templates/protocols/list.html` (icon beside both print icons, dialog, script), `app/templates/protocols/builder.html` (panel and script), `app/static/css/app.css`
- Test: `tests/test_protocol_alerts.py`

**Interfaces:**
- Consumes: `alerts_mod.for_protocol`, routes from Task 3
- Produces: list context `alert_counts: dict[int, int]`; elements `.alerts-btn[data-protocol-id]`, `#alerts-dialog`, `#alerts-check`, `#alerts-out`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_protocol_alerts.py`)

```python
def test_the_protocol_list_has_an_alerts_icon_with_a_count_beside_print(client, protocol_id):
    page = client.get("/protocols").text
    row = page.split(f'data-protocol-id="{protocol_id}"')[0]
    assert f'class="btn-icon alerts-btn" data-protocol-id="{protocol_id}"' in page
    assert 'id="alerts-dialog"' in page and "protocol-alerts.js" in page
    assert page.index(f'/protocols/{protocol_id}/print') < page.index(f'class="btn-icon alerts-btn" data-protocol-id="{protocol_id}"')
    assert f'data-alerts-count="2"' in page


def test_the_builder_has_a_check_for_alerts_panel_and_the_preview_finds_a_real_conflict(client):
    assert 'id="alerts-check"' in client.get("/protocols/new").text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_protocol_alerts.py -k "icon or builder"`
Expected: FAIL (icon and panel absent)

- [ ] **Step 3: Implement**

`list_protocols`: after `shared_protocols` is loaded add
`alert_counts = {p.id: len(alerts_mod.for_protocol(session, p, uid)) for p in protocols}` and pass `alert_counts=alert_counts` in the template context.

`list.html`: after each of the two print `<a ...>` icons add

```jinja
<button type="button" class="btn-icon alerts-btn" data-protocol-id="{{ p.id }}" data-alerts-count="{{ alert_counts.get(p.id, 0) }}" aria-label="Alerts for {{ p.name }}" title="Alerts for this protocol"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.7 21a2 2 0 0 1-3.4 0"></path></svg>{% if alert_counts.get(p.id) %}<span class="alert-badge">{{ alert_counts[p.id] }}</span>{% endif %}</button>
```

and before the `scripts` block a dialog (same close pattern as the shop dialog):

```jinja
<dialog id="alerts-dialog" class="shop-dialog" aria-labelledby="alerts-title">
  <h2 id="alerts-title" class="section-title">Alerts</h2>
  <div id="alerts-body" aria-live="polite"></div>
  <p class="small muted" id="alerts-disclaimer"></p>
  <div class="dialog-actions"><a class="btn btn-ghost" id="alerts-page" href="#" target="_blank" rel="noopener">Open as a page</a><button type="button" class="btn" data-close>Close</button></div>
</dialog>
```

and `<script src="{{ static_url('js/protocol-alerts.js') }}" defer></script>` in the scripts block. In `builder.html`, before `<div class="builder-foot">` add

```jinja
<section class="builder-step" id="alerts-panel">
  <h2 class="section-title">Alerts</h2>
  <p class="small muted">Check the doses and schedule above for anything worth asking a prescriber about. This never blocks saving.</p>
  <button type="button" class="btn" id="alerts-check">Check for alerts</button>
  <div id="alerts-out" aria-live="polite"></div>
</section>
```

and the same script tag in its scripts block.

`app/static/js/protocol-alerts.js`:

```javascript
// Protocol alerts: the icon beside Print opens the report in a dialog; the builder's panel previews the form. Informational only.
(() => {
  function render(target, data) {
    target.textContent = "";
    if (data.incomplete) { target.textContent = "Finish the protocol (name, dates and at least one item) to check it."; return; }
    if (!data.findings.length) { target.textContent = "No alerts. That does not mean there is no interaction; the checks are short on purpose."; return; }
    const list = document.createElement("ul");
    list.className = "alert-list";
    data.findings.forEach((f) => {
      const item = document.createElement("li");
      item.className = `alert-item alert-${f.severity}`;
      const label = document.createElement("strong");
      label.textContent = f.severity === "caution" ? "Caution " : "Note ";
      item.append(label, document.createTextNode(f.message));
      if (f.peptide_id) {
        const link = document.createElement("a");
        link.href = `/library/${f.peptide_id}`;
        link.textContent = " Library card";
        item.append(link);
      }
      list.append(item);
    });
    target.append(list);
  }

  const dialog = document.getElementById("alerts-dialog");
  if (dialog) {
    document.querySelectorAll(".alerts-btn").forEach((button) => button.addEventListener("click", async () => {
      const id = button.dataset.protocolId;
      const body = document.getElementById("alerts-body");
      body.textContent = "Checking…";
      document.getElementById("alerts-page").href = `/protocols/${id}/alerts`;
      dialog.showModal();
      try {
        const data = await (await fetch(`/protocols/${id}/alerts.json`)).json();
        render(body, data);
        document.getElementById("alerts-disclaimer").textContent = data.disclaimer;
      } catch (error) { body.textContent = "Could not load the alerts."; }
    }));
    dialog.querySelector("[data-close]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("mousedown", (e) => { dialog._backdrop = e.target === dialog; });
    dialog.addEventListener("click", (e) => { if (dialog._backdrop && e.target === dialog) dialog.close(); });
  }

  const check = document.getElementById("alerts-check");
  if (check) check.addEventListener("click", async () => {
    const out = document.getElementById("alerts-out");
    out.textContent = "Checking…";
    try {
      const form = document.getElementById("builder-form");
      const response = await fetch("/protocols/alerts-preview", { method: "POST", body: new URLSearchParams(new FormData(form)) });
      const data = await response.json();
      render(out, data);
      const note = document.createElement("p");
      note.className = "small muted";
      note.textContent = data.disclaimer;
      out.append(note);
    } catch (error) { out.textContent = "Could not check right now."; }
  });
})();
```

CSS (append to `app/static/css/app.css`):

```css
.alerts-btn { position: relative; }
.alert-badge { position: absolute; top: -4px; right: -4px; min-width: 16px; padding: 0 4px; border-radius: 8px; background: #dc2626; color: #fff; font-size: 0.7rem; line-height: 16px; text-align: center; }
.alert-list { list-style: none; margin: 8px 0; padding: 0; }
.alert-item { margin: 6px 0; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border); }
.alert-caution { border-left: 4px solid #dc2626; }
.alert-note { border-left: 4px solid var(--muted); }
```

- [ ] **Step 4: Run to verify pass, then the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings`
Expected: all pass

- [ ] **Step 5: Commit (only after the owner says "commit")**

```bash
git add app/static/js/protocol-alerts.js app/static/css/app.css app/routers/protocols.py app/templates/protocols/list.html app/templates/protocols/builder.html tests/test_protocol_alerts.py
git commit -m "feat: alerts icon beside print on each protocol, and a check panel in the builder"
```

---

### Task 5: Docs

**Files:**
- Modify: `docs/USER_GUIDE.md`, `docs/ROADMAP.md`, `CHANGELOG.md`

- [ ] **Step 1:** In `docs/USER_GUIDE.md` add a short "Alerts" paragraph under Protocols: the bell icon beside Print, what the four checks look at, the optional dose on a listed medicine, and that it is informational only. In `docs/ROADMAP.md` mark the full dose-level conflict checker built (in Group C, the Phase "Vitamins/Supplements" line and the post-launch list). In `CHANGELOG.md` add an entry for the alerts report and migration 0057.
- [ ] **Step 2:** Run the whole suite: `.venv/Scripts/python.exe -m pytest -q -p no:warnings` (expected: all pass).
- [ ] **Step 3: Commit (only after the owner says "commit")**

```bash
git add docs/USER_GUIDE.md docs/ROADMAP.md CHANGELOG.md
git commit -m "docs: alerts report"
```

---

## Self-review

- **Spec coverage:** four checks (Task 1), medicine dose field (Task 2), report page/JSON/preview (Task 3), icon beside print, dialog, builder panel, caution tape unchanged (Task 4), docs (Task 5). The timing reinterpretation is recorded under "Clarifications of the spec".
- **Placeholders:** none; the first draft of the preview test in Task 3 is explicitly replaced by the exact version.
- **Type consistency:** `ItemData`, `MedicineData`, `Finding.as_dict`, `check`, `item_data`, `medicines_for`, `for_protocol`, `for_parsed` and `DISCLAIMER` are named identically in every task.
- **Review Focus coverage:** one-item and no-dose protocols and missing library range (Task 1 tests), mixed units (Task 1), shared/other-person protocol (Task 3), unfinished and new-peptide builder form (Task 3).

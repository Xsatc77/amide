# Peptide Reference Sheet Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** build the data model, parser, and loader for turning one of the user's peptide reference
`.txt` files into structured Library data — the tool itself, not the actual 152-file import run
(which happens later, once the user hands over files for that purpose; this plan never reads or
scans anything beyond the fixtures it writes itself).

**Architecture:** new `Peptide` columns + four child tables for the structured/tabular data
(Task 1); a pure-function parser, `app/library/sheet_parser.py`, that splits one file's raw text
into that shape (Task 2); a `load_sheets()` function in the existing `app/library/loader.py`,
mirroring its established `load_cards()` pattern (Task 3); a one-shot CLI entry point,
`app/library_load_sheets.py`, mirroring the existing `app/library_load.py` (Task 4).

**Tech Stack:** SQLAlchemy 2.0 + Alembic (SQLite), pure-Python text parsing (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-09-29-peptide-sheet-import-design.md`

## Global Constraints

- A name match against an existing `CARD`-sourced `Peptide` is FULLY replaced (its `card_class`,
  `category`, `evidence_level`, `status`, `card_details`, `card_image` cleared to `None`, `source`
  set to `SHEET`) — never left with stale old-card fields alongside the new sheet fields.
- `usage_tips` is never derived by the parser itself — it's a caller-supplied list (a human judgment
  call made per file, per the spec), defaulting to an empty list when not supplied.
- `time_of_day` on a dosing tier is only ever set when the source text names an actual time —
  never inferred/guessed from the route or dose.
- The Dosage Guide table is parsed by column POSITION (Level / Dose / Frequency), not by matching
  an exact header string — the spec confirms real files vary here (`"DOSE"` vs. `"DOSE /
  INJECTION"`).
- This plan does not build a folder-scanning or file-discovery mechanism of any kind — every
  function here takes a file's raw text (or an already-parsed dict) as a plain argument.
- This session's prior features have repeatedly found the same three recurring test-fixture bugs:
  (1) inserting a seeded-name row unconditionally collides with a real seeded row under a unique
  constraint; (2) an unfiltered `select(User.id)` picks the wrong user under full-suite test-order
  pollution — pin via `User.username_key == "tester"`; (3) a POSIX-only `%-d` strftime flag crashes
  on Windows. Watch for all three (the third is unlikely here since this plan renders no templates).

## Review Focus

1. A file with no Cycling Protocol section (a continuous-use compound) must produce a `Peptide`
   with NO `PeptideCycle` row at all — never a row with null/zero weeks.
2. A name match against an existing `CARD`-sourced peptide must fully clear its old card fields,
   not leave them sitting alongside the new sheet data.
3. The Dosage Guide table parser must handle a `"DOSE / INJECTION"` column header the same as a
   plain `"DOSE"` header — position-based, not exact-string-based.
4. A `PeptideStackRelation`'s `partner_name` may not match any existing `Peptide.name` (e.g. a drug
   class like "Methotrexate / folate antagonists", or a not-yet-imported peptide) — this must be
   stored as free text without requiring a foreign-key match, never dropped or errored on.
5. Two different files both containing a "Related Peptides" or "Stacking" mention of a peptide that
   hasn't been imported yet must not cause an import-order dependency — each peptide's own sheet
   loads independently regardless of what its cross-references point to.

---

### Task 1: Data model + migration

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0020_peptide_sheets.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `PeptideSource.SHEET` (added to the existing enum); `DosingTierLevel`,
  `StackRelation` enums; new `Peptide` columns (`half_life_text`, `bioavailability_text`,
  `tmax_text`, `route_summary`, `storage_before_text`, `storage_after_text`,
  `storage_temperature_text`, `legal_status_text`, `cost_estimate_text`, `usage_tips`,
  `sheet_sections`); `PeptideDosingTier`, `PeptideCycle`, `PeptideStackRelation`,
  `PeptideMonitoringTest` models. Every later task consumes these exact names.

- [ ] **Step 1: Write the failing migration test**

Read `tests/test_migrations.py`'s existing `test_0019_adds_labs` for the exact `_cfg`/`command`/
`sqlite3` helper shape, and write a new test in the same style:

```python
def test_0020_adds_peptide_sheets(tmp_path):
    db = tmp_path / "h.db"
    cfg = _cfg(db)
    command.upgrade(cfg, "0019")
    with sqlite3.connect(db) as c:
        c.execute("insert into peptides(name, source, created_at) values ('Test-Compound-9', 'sheet', '2026-09-29')") \
            if False else None  # peptides predates created_at in some builds -- confirmed below instead
        peptide_cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert {"half_life_text", "bioavailability_text", "tmax_text", "route_summary",
               "storage_before_text", "storage_after_text", "storage_temperature_text",
               "legal_status_text", "cost_estimate_text", "usage_tips", "sheet_sections"} <= peptide_cols
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert {"peptide_dosing_tiers", "peptide_cycles", "peptide_stack_relations",
               "peptide_monitoring_tests"} <= tables
        c.execute("insert into peptides(name, source) values ('Test-Compound-9', 'sheet')")
        pid = c.execute("select id from peptides where name='Test-Compound-9'").fetchone()[0]
        c.execute("insert into peptide_dosing_tiers(peptide_id, level, dose_text, frequency_text) "
                  "values (?, 'Beginner', '50mg', 'Daily')", (pid,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into peptide_dosing_tiers(peptide_id, level, dose_text, frequency_text) "
                      "values (?, 'Beginner', '100mg', 'Daily')", (pid,))  # unique (peptide_id, level)
    command.downgrade(cfg, "0019")
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        assert not ({"peptide_dosing_tiers", "peptide_cycles", "peptide_stack_relations",
                    "peptide_monitoring_tests"} & tables)
        peptide_cols = {r[1] for r in c.execute("pragma table_info(peptides)")}
        assert "half_life_text" not in peptide_cols
```

Read the real `peptides` table schema in `app/models.py` first (via `grep -n "class Peptide" -A 25
app/models.py`) to confirm whether it already has a `created_at` column before assuming the insert
statement above needs one — adjust the test's insert statements to match reality exactly (the
sketch above intentionally does not assume `created_at` exists, to avoid guessing wrong).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py -k test_0020 -v`
Expected: FAIL — migration `0020` doesn't exist yet.

- [ ] **Step 3: Add the enums**

In `app/models.py`, add `SHEET = ("sheet", "Reference sheet")` as a new member of the existing
`PeptideSource(LabeledEnum)` (do not reorder or remove `CARD`/`STARTER`/`CUSTOM`). Add two new
plain string enums near it, matching this codebase's existing `class X(str, enum.Enum)` convention
(see `BiologicalSex`, `JournalSideEffect`, `LabMarker` for the exact pattern):

```python
class DosingTierLevel(str, enum.Enum):
    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class StackRelation(str, enum.Enum):
    WORKS_WITH = "works_with"
    AVOID = "avoid"
```

- [ ] **Step 4: Add the new `Peptide` columns**

In the existing `Peptide` class, add:

```python
    half_life_text: Mapped[str | None] = mapped_column(String(100))
    bioavailability_text: Mapped[str | None] = mapped_column(Text)
    tmax_text: Mapped[str | None] = mapped_column(String(100))
    route_summary: Mapped[str | None] = mapped_column(String(100))
    storage_before_text: Mapped[str | None] = mapped_column(Text)
    storage_after_text: Mapped[str | None] = mapped_column(Text)
    storage_temperature_text: Mapped[str | None] = mapped_column(String(100))
    legal_status_text: Mapped[str | None] = mapped_column(Text)
    cost_estimate_text: Mapped[str | None] = mapped_column(Text)
    usage_tips: Mapped[list | None] = mapped_column(JSON)
    sheet_sections: Mapped[dict | None] = mapped_column(JSON)
```

Confirm `JSON` is already imported at the top of `app/models.py` (it should be, from
`card_details`'s own existing `JSON` column) before assuming no import change is needed.

- [ ] **Step 5: Add the four child models**

```python
class PeptideDosingTier(Base):
    """One row per level (Beginner/Intermediate/Advanced) for a peptide's community dosing guide."""
    __tablename__ = "peptide_dosing_tiers"
    __table_args__ = (UniqueConstraint("peptide_id", "level", name="uq_peptide_dosing_tier_level"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    level: Mapped[DosingTierLevel] = mapped_column(_enum_column(DosingTierLevel))
    dose_text: Mapped[str] = mapped_column(String(100))
    frequency_text: Mapped[str] = mapped_column(String(100))
    time_of_day: Mapped[TimeOfDay | None] = mapped_column(_enum_column(TimeOfDay))

    peptide: Mapped["Peptide"] = relationship()


class PeptideCycle(Base):
    __tablename__ = "peptide_cycles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), unique=True)
    on_weeks: Mapped[int | None] = mapped_column(Integer)
    off_weeks: Mapped[int | None] = mapped_column(Integer)
    max_cycles_per_year: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)

    peptide: Mapped["Peptide"] = relationship()


class PeptideStackRelation(Base):
    __tablename__ = "peptide_stack_relations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    partner_name: Mapped[str] = mapped_column(String(120))
    relation: Mapped[StackRelation] = mapped_column(_enum_column(StackRelation))
    note: Mapped[str] = mapped_column(Text)

    peptide: Mapped["Peptide"] = relationship()


class PeptideMonitoringTest(Base):
    __tablename__ = "peptide_monitoring_tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    test_name: Mapped[str] = mapped_column(String(120))
    when_text: Mapped[str] = mapped_column(String(200))
    why_text: Mapped[str] = mapped_column(Text)
    target_text: Mapped[str | None] = mapped_column(String(200))

    peptide: Mapped["Peptide"] = relationship()
```

`partner_name` on `PeptideStackRelation` is deliberately free text with no foreign key to `Peptide`
(Review Focus item 4) — a stacking partner is very often a drug class ("Methotrexate / folate
antagonists") or a peptide not yet imported, never a hard reference.

- [ ] **Step 6: Write the migration**

Create `migrations/versions/0020_peptide_sheets.py`, following `0019_labs.py`'s exact style for
`op.batch_alter_table` on `peptides` (check first whether `peptides.name`'s `COLLATE NOCASE` forces
the raw-SQL rebuild approach `0016_vendor_management.py` used for a similarly-collated column,
rather than assuming `batch_alter_table` is safe) plus `op.create_table` for the four new tables,
mirroring `0019`'s own table/index/constraint style exactly. Compute the `sa.Enum(...)` `length=`
values for `DosingTierLevel`/`StackRelation` the same careful way `0020`'s predecessor migrations
did — read `_enum_column`'s current implementation in `app/models.py` and match its computed
length, don't guess a number.

- [ ] **Step 7: Run the migration test**

Run: `pytest tests/test_migrations.py -k test_0020 -v`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 9: Commit**

```bash
git add app/models.py migrations/versions/0020_peptide_sheets.py tests/test_migrations.py
git commit -m "feat: add peptide reference-sheet columns and dosing/cycle/stacking/monitoring tables"
```

---

### Task 2: Sheet parser

**Files:**
- Create: `app/library/sheet_parser.py`
- Test: `tests/test_sheet_parser.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (pure text in, dict out — no DB access, no model imports
  needed beyond type hints if any).
- Produces: `parse_sheet(text: str) -> dict` returning a dict with keys: `name`, `aliases`
  (list[str]), `tags` (list[str]), `half_life_text`, `route_summary`, `cycle_shorthand`,
  `summary` (the opening TL;DR paragraph), `dosing_tiers` (list of `{"level": str, "dose_text":
  str, "frequency_text": str, "time_of_day": str | None}` — `level` is one of `"Beginner"` /
  `"Intermediate"` / `"Advanced"`, `time_of_day` is one of `"am"`/`"pm"`/`"bedtime"`/`"any"`/`None`
  matching `TimeOfDay`'s stored values), `cycle` (`{"on_weeks": int | None, "off_weeks": int |
  None, "note": str} | None` — `None` when the file has no Cycling Protocol section at all, per
  Review Focus item 1), `stack_relations` (list of `{"partner_name": str, "relation":
  "works_with"|"avoid", "note": str}`), `monitoring_tests` (list of `{"test_name": str, "when_text":
  str, "why_text": str, "target_text": str | None}`), `bioavailability_text`, `tmax_text`,
  `storage_before_text`, `storage_after_text`, `storage_temperature_text`, `legal_status_text`,
  `cost_estimate_text`, `sheet_sections` (dict: section-name -> raw text, for every narrative
  section named in the spec's retention list). `parse_sheet` never sets `usage_tips` — Task 3's
  `load_sheets` accepts it as a separate, caller-supplied argument per peptide (Global Constraint).
  Task 3 consumes this exact return shape.

This task's tests use a fully invented synthetic peptide, "Test-Compound-9", not real content from
any actual reference file — the section HEADERS below (e.g. `"Test-Compound-9 Dosage Guide"`,
`"LEVEL\tDOSE\tFREQUENCY"`) are generic structural labels matching the real format's pattern, but
all narrative text is made up for testing purposes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sheet_parser.py`:

```python
from app.library.sheet_parser import parse_sheet

SAMPLE = """Test-Compound-9

Not medical advice. Talk to your provider before using any peptide.

Full disclaimer
Test-Compound-9
Peptide
Research
Grade B
Synthetic Modulator
Recovery
Test-Compound-9
~3-5 hours half-life
·
Injection
·
6w on / 4w off

Also known as: Test-Compound-9, TC9

Calculate dose
Check with AI
Popular
100
Used?

This is a made-up summary paragraph describing Test-Compound-9 for testing purposes only.

What Is Test-Compound-9?

This is a made-up "what is" narrative paragraph for testing.

How Test-Compound-9 Works

This is a made-up mechanism narrative paragraph for testing.

Test-Compound-9 Benefits
A made-up bulleted benefit for testing.
Another made-up bulleted benefit for testing.

Test-Compound-9 Dosage Guide
Community
Community dosing consensus from peptide research communities
LEVEL	DOSE	FREQUENCY

Beginner
	10mg	Daily

Intermediate
	20mg	Daily

Advanced
	30mg	2x Daily
Note:

A made-up dosing note, take each morning with food for testing purposes.

Cycling Protocol
ON PERIOD
6 weeks
OFF PERIOD
4 weeks

A made-up cycling narrative for testing.

How to Use Test-Compound-9
1
Confirm your vial strength

Generic mechanical instruction, made up for testing.

2
Take in the morning

Take each morning on an empty stomach, made up for testing.

Test-Compound-9 Stacking Protocols
WORKS WITH
Made-Up-Partner-A

A made-up rationale for why these pair well, for testing.

AVOID
Made-Up-Drug-Class

A made-up rationale for why these should be avoided, for testing.

Estimated Cost

A made-up cost estimate paragraph for testing.

Test-Compound-9 Side Effects

A made-up side-effects narrative for testing.

Contraindications
A made-up contraindication for testing.

Drug Interactions
A made-up drug interaction note for testing.

Product Quality

A made-up product-quality narrative for testing.

Recommended Monitoring
TEST	WHEN	WHY	TARGET
Made-up test	Baseline	A made-up reason for testing	A made-up target range

Pharmacokinetics
HALF-LIFE
4h
BIOAVAILABILITY
A made-up bioavailability note for testing.
TMAX
~1 hour
DATA CONFIDENCE
low

Is Test-Compound-9 Legal?

A made-up legal-status narrative for testing.

Who Should Consider Test-Compound-9
A made-up audience bullet for testing.

Related Peptides
Made-Up-Partner-A
Recovery

Storage & Stability
BEFORE RECONSTITUTION
A made-up pre-reconstitution storage note for testing.
AFTER RECONSTITUTION
A made-up post-reconstitution storage note for testing.
TEMPERATURE
2-8°C (36-46°F), refrigerated
"""


def test_parses_name_aliases_and_quick_facts():
    result = parse_sheet(SAMPLE)
    assert result["name"] == "Test-Compound-9"
    assert "TC9" in result["aliases"]
    assert result["half_life_text"] == "~3-5 hours half-life" or "3-5 hours" in result["half_life_text"]
    assert result["route_summary"] == "Injection"


def test_parses_dosing_tiers_with_column_position_not_exact_header():
    result = parse_sheet(SAMPLE)
    tiers = {t["level"]: t for t in result["dosing_tiers"]}
    assert tiers["Beginner"]["dose_text"] == "10mg" and tiers["Beginner"]["frequency_text"] == "Daily"
    assert tiers["Intermediate"]["dose_text"] == "20mg"
    assert tiers["Advanced"]["dose_text"] == "30mg" and tiers["Advanced"]["frequency_text"] == "2x Daily"


def test_dosing_tier_time_of_day_only_set_when_text_names_one():
    result = parse_sheet(SAMPLE)
    tiers = {t["level"]: t for t in result["dosing_tiers"]}
    # implementer: the dosing note says "take each morning" -- decide whether this plan's parser
    # attributes that to all three tiers or leaves time_of_day unset entirely when the mention is
    # in the shared note rather than per-tier text; either is defensible, but assert whichever
    # behavior you implement explicitly here rather than leaving it unasserted, and document the
    # choice in this function's own test name/docstring.
    ...


def test_dosing_table_header_variant_dose_slash_injection_parses_the_same_way():
    """Review Focus item 3: one sampled real file used 'DOSE / INJECTION' instead of 'DOSE' for
    the same column -- the parser must key off column position, not an exact header string."""
    variant = SAMPLE.replace("LEVEL\tDOSE\tFREQUENCY", "LEVEL\tDOSE / INJECTION\tFREQUENCY")
    result = parse_sheet(variant)
    tiers = {t["level"]: t for t in result["dosing_tiers"]}
    assert tiers["Beginner"]["dose_text"] == "10mg" and tiers["Beginner"]["frequency_text"] == "Daily"
    assert tiers["Advanced"]["dose_text"] == "30mg" and tiers["Advanced"]["frequency_text"] == "2x Daily"


def test_dosing_tier_old_names_moderate_and_aggressive_map_to_renamed_tiers():
    """The source files may still use the old Beginner/Moderate/Aggressive naming -- these must be
    stored under the renamed Beginner/Intermediate/Advanced tiers, never a fourth/fifth level."""
    old_names = SAMPLE.replace("Intermediate", "Moderate").replace("Advanced", "Aggressive")
    result = parse_sheet(old_names)
    levels = {t["level"] for t in result["dosing_tiers"]}
    assert levels == {"Beginner", "Intermediate", "Advanced"}


def test_cycle_parsed_when_present():
    result = parse_sheet(SAMPLE)
    assert result["cycle"] == {"on_weeks": 6, "off_weeks": 4, "note": result["cycle"]["note"]}
    assert "made-up cycling narrative" in result["cycle"]["note"]


def test_cycle_is_none_when_section_absent():
    """Review Focus item 1: a continuous-use compound (no Cycling Protocol section at all) must
    produce cycle=None, never a row with null/zero weeks."""
    no_cycle_sample = SAMPLE.split("Cycling Protocol")[0] + SAMPLE.split("How to Use Test-Compound-9", 1)[1]
    no_cycle_sample = "How to Use Test-Compound-9" + no_cycle_sample
    result = parse_sheet(SAMPLE.replace(
        "Cycling Protocol\nON PERIOD\n6 weeks\nOFF PERIOD\n4 weeks\n\nA made-up cycling narrative for testing.\n\n",
        ""))
    assert result["cycle"] is None


def test_stack_relations_work_with_and_avoid():
    result = parse_sheet(SAMPLE)
    by_relation = {r["relation"]: r for r in result["stack_relations"]}
    assert by_relation["works_with"]["partner_name"] == "Made-Up-Partner-A"
    assert by_relation["avoid"]["partner_name"] == "Made-Up-Drug-Class"


def test_stack_relation_partner_name_is_free_text_no_lookup():
    """Review Focus item 4: a partner name that matches no real Peptide (a drug class, or a
    not-yet-imported peptide) must still parse cleanly, never raise or get dropped."""
    result = parse_sheet(SAMPLE)
    names = {r["partner_name"] for r in result["stack_relations"]}
    assert "Made-Up-Drug-Class" in names  # not a real peptide name; parsed anyway


def test_monitoring_tests_table():
    result = parse_sheet(SAMPLE)
    [test] = result["monitoring_tests"]
    assert test["test_name"] == "Made-up test" and test["when_text"] == "Baseline"
    assert test["target_text"] == "A made-up target range"


def test_pharmacokinetics_quick_facts():
    result = parse_sheet(SAMPLE)
    assert result["tmax_text"] and "1 hour" in result["tmax_text"]
    assert result["bioavailability_text"] and "bioavailability note" in result["bioavailability_text"]


def test_storage_fields():
    result = parse_sheet(SAMPLE)
    assert "pre-reconstitution" in result["storage_before_text"]
    assert "post-reconstitution" in result["storage_after_text"]
    assert "2-8" in result["storage_temperature_text"]


def test_narrative_sections_captured_verbatim():
    result = parse_sheet(SAMPLE)
    assert "what is" in result["sheet_sections"]["what_is"].lower()
    assert "mechanism" in result["sheet_sections"]["how_it_works"].lower()
    assert "made-up legal-status" in result["sheet_sections"]["legal"].lower()


def test_missing_section_leaves_field_empty_not_a_crash():
    """A file missing an expected optional section must not crash the parser."""
    stripped = SAMPLE.replace(
        "Who Should Consider Test-Compound-9\nA made-up audience bullet for testing.\n\n", "")
    result = parse_sheet(stripped)
    assert result["sheet_sections"].get("who_should_consider") in (None, "")
```

Fill in `test_dosing_tier_time_of_day_only_set_when_text_names_one`'s body with a real assertion
once you've decided (and documented, per its own comment) how per-tier vs. shared-note time
mentions are attributed — this is explicitly left to your judgment by the plan, not a gap to skip.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sheet_parser.py -v`
Expected: FAIL — `app.library.sheet_parser` doesn't exist yet.

- [ ] **Step 3: Implement `app/library/sheet_parser.py`**

Implement `parse_sheet(text: str) -> dict` per the Interfaces block above. Split the text into
lines, locate the known section headers listed in the spec's Parser section (`What Is <name>?`,
`How <name> Works`, `<name> Benefits`, `<name> Dosage Guide`, `Cycling Protocol`, `How to Use
<name>`, `<name> Stacking Protocols`, `Estimated Cost`, `<name> Side Effects`, `Contraindications`,
`Drug Interactions`, `Product Quality`, `Recommended Monitoring`, `Pharmacokinetics`, `Is <name>
Legal?`, `Who Should Consider <name>`, `Related Peptides`, `Storage & Stability`) by matching the
literal peptide name into each templated header string (the name itself is read from the file's
own first non-blank line), and slice the text between consecutive matched headers as that section's
raw content. The Dosage Guide table's rows are parsed by finding the `Beginner`/`Intermediate`/
`Advanced` (or `Aggressive`/`Moderate` — the source files may still use the OLD tier names; map
`Moderate` → `Intermediate` and `Aggressive` → `Advanced` during parsing so the stored data always
uses the renamed tiers) labels and taking the two non-empty values immediately following each as
dose and frequency, by position, not by validating the table's own header row text (Review Focus
item 3). Map any recognized time-of-day word in the tier's own row/note text ("morning" → `"am"`,
"afternoon"/"evening" → `"pm"`, "bed"/"bedtime" → `"bedtime"`) to `time_of_day`; leave it `None`
when no such word appears. `sheet_sections` keys are snake_case versions of the section's role
(`what_is`, `how_it_works`, `benefits`, `side_effects`, `contraindications`, `drug_interactions`,
`product_quality`, `legal`, `who_should_consider`, `related_peptides`, `citations`) — every
narrative section from the spec's retention list gets one, defaulting to `None`/absent when that
section isn't present in the file at all (never a crash).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sheet_parser.py -v`
Expected: All PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/library/sheet_parser.py tests/test_sheet_parser.py
git commit -m "feat: add peptide reference-sheet text parser"
```

---

### Task 3: Loader — `load_sheets`

**Files:**
- Modify: `app/library/loader.py`
- Test: `tests/test_library_loader.py`

**Interfaces:**
- Consumes: `parse_sheet`'s exact return shape (Task 2); `Peptide`, `PeptideSource.SHEET`,
  `PeptideDosingTier`, `PeptideCycle`, `PeptideStackRelation`, `PeptideMonitoringTest` (Task 1).
- Produces: `load_sheets(session: Session, sheets: list[dict]) -> LoadReport` — each dict in
  `sheets` is a `parse_sheet(...)` result with one added key, `"usage_tips": list[str]` (the
  caller-supplied curated list; defaults to `[]` if the key is absent). Reuses the existing
  `LoadReport` dataclass from this same file (do not create a second report type).

- [ ] **Step 1: Write the failing tests**

Read `tests/test_library_loader.py`'s existing tests for `load_cards` first, to match its exact
`SessionLocal`/fixture conventions, then add:

```python
def test_load_sheets_creates_a_new_peptide(db):
    from app.library.loader import load_sheets
    sheet = {
        "name": "Test-Compound-9", "aliases": ["TC9"], "tags": ["Recovery"],
        "half_life_text": "~3-5 hours", "route_summary": "Injection", "cycle_shorthand": "6w on / 4w off",
        "summary": "A made-up summary.", "dosing_tiers": [
            {"level": "Beginner", "dose_text": "10mg", "frequency_text": "Daily", "time_of_day": None},
        ],
        "cycle": {"on_weeks": 6, "off_weeks": 4, "note": "A made-up note."},
        "stack_relations": [{"partner_name": "Made-Up-Partner-A", "relation": "works_with", "note": "n"}],
        "monitoring_tests": [{"test_name": "Made-up test", "when_text": "Baseline", "why_text": "y", "target_text": None}],
        "bioavailability_text": None, "tmax_text": "~1 hour",
        "storage_before_text": "a", "storage_after_text": "b", "storage_temperature_text": "c",
        "legal_status_text": "d", "cost_estimate_text": "e", "sheet_sections": {"what_is": "f"},
        "usage_tips": ["Take each morning on an empty stomach."],
    }
    report = load_sheets(db, [sheet])
    assert report.created == ["Test-Compound-9"]
    p = db.query(Peptide).filter_by(name="Test-Compound-9").one()
    assert p.source == PeptideSource.SHEET
    assert p.usage_tips == ["Take each morning on an empty stomach."]
    assert len(p.dosing_tiers) == 1  # implementer: add this relationship to Peptide in Task 1's
    # model if it isn't already implied there -- check before assuming it exists.


def test_load_sheets_replaces_an_existing_card_sourced_peptide(db):
    """Review Focus item 2: a name match against a CARD-sourced peptide must fully clear its old
    card fields, not leave them alongside the new sheet fields."""
    from app.library.loader import load_sheets
    existing = Peptide(name="Test-Compound-9", source=PeptideSource.CARD, card_class="Old class",
                       category="Old category", card_details={"old": "data"})
    db.add(existing)
    db.commit()
    sheet = {"name": "Test-Compound-9", "aliases": [], "tags": [], "half_life_text": None,
            "route_summary": None, "cycle_shorthand": None, "summary": None, "dosing_tiers": [],
            "cycle": None, "stack_relations": [], "monitoring_tests": [], "bioavailability_text": None,
            "tmax_text": None, "storage_before_text": None, "storage_after_text": None,
            "storage_temperature_text": None, "legal_status_text": None, "cost_estimate_text": None,
            "sheet_sections": {}, "usage_tips": []}
    load_sheets(db, [sheet])
    db.refresh(existing)
    assert existing.source == PeptideSource.SHEET
    assert existing.card_class is None and existing.category is None and existing.card_details is None


def test_load_sheets_stack_relation_partner_not_matching_any_peptide_is_fine(db):
    from app.library.loader import load_sheets
    sheet = {"name": "Test-Compound-9", "aliases": [], "tags": [], "half_life_text": None,
            "route_summary": None, "cycle_shorthand": None, "summary": None, "dosing_tiers": [],
            "cycle": None,
            "stack_relations": [{"partner_name": "Nonexistent Drug Class", "relation": "avoid", "note": "n"}],
            "monitoring_tests": [], "bioavailability_text": None, "tmax_text": None,
            "storage_before_text": None, "storage_after_text": None, "storage_temperature_text": None,
            "legal_status_text": None, "cost_estimate_text": None, "sheet_sections": {}, "usage_tips": []}
    load_sheets(db, [sheet])  # must not raise
    p = db.query(Peptide).filter_by(name="Test-Compound-9").one()
    assert p.stack_relations[0].partner_name == "Nonexistent Drug Class"
```

Fill in `test_load_sheets_creates_a_new_peptide`'s `p.dosing_tiers` reference by first checking
whether Task 1 added a `dosing_tiers`/`stack_relations` back-reference relationship on `Peptide`
itself (it did not, in the sketch above — only the child tables have a `peptide` relationship back
to `Peptide`). Add the missing reverse relationships to `Peptide` in this task if Task 1 didn't
already include them (check `app/models.py` directly rather than assuming): `dosing_tiers:
Mapped[list["PeptideDosingTier"]] = relationship(cascade="all, delete-orphan")`, and equivalently
for `cycle` (a single object, not a list, given the one-per-peptide unique constraint),
`stack_relations`, `monitoring_tests`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_library_loader.py -k load_sheets -v`
Expected: FAIL — `load_sheets` doesn't exist yet.

- [ ] **Step 3: Implement `load_sheets` in `app/library/loader.py`**

Mirror `load_cards`'s exact shape: match by name (case-insensitive — `Peptide.name` already has
`COLLATE NOCASE`, confirm this via `app/models.py` before assuming a manual `.lower()` comparison
is needed), create a new `Peptide` if none exists, and when one does exist, clear its old card
fields (`card_class = category = evidence_level = status = card_details = card_image = None`)
before setting `source = PeptideSource.SHEET` and writing every sheet field onto it. Replace
(delete-then-recreate, matching this app's established "replace child rows" idiom — see
`save_protocol`'s own goals-replacement for the precedent) the peptide's `dosing_tiers`,
`stack_relations`, and `monitoring_tests` from the sheet's lists, and its `cycle` (a single
optional child row, not a list) from the sheet's `cycle` dict or `None`. Return the same
`LoadReport` dataclass `load_cards` already uses, appending to `created`/`updated` accordingly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_library_loader.py -v`
Expected: All PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/library/loader.py tests/test_library_loader.py
git commit -m "feat: add load_sheets to import parsed peptide reference sheets"
```

---

### Task 4: CLI entry point

**Files:**
- Create: `app/library_load_sheets.py`
- Test: `tests/test_library_load_sheets.py`

**Interfaces:**
- Consumes: `parse_sheet` (Task 2), `load_sheets` (Task 3).
- Produces: a CLI script, `python -m app.library_load_sheets <sheet1.txt> [<sheet2.txt> ...]` —
  each argument is a path to ONE already-on-disk file the user has explicitly provided for this
  purpose (this script takes an explicit list of paths on the command line, typed or pasted by the
  user at the time they run it — it does not walk a directory, glob a pattern, or read any path not
  given to it as an argument).

- [ ] **Step 1: Write the failing test**

Read `tests/` for how the existing `app/library_load.py` CLI (if it has a test) or its own
docstring describes invocation, then write:

```python
def test_library_load_sheets_reads_named_files_and_reports(tmp_path, capsys, monkeypatch):
    from app import library_load_sheets

    sample = tmp_path / "test-compound-9.txt"
    sample.write_text(
        "Test-Compound-9\n\nWhat Is Test-Compound-9?\n\nA made-up narrative for testing.\n",
        encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["library_load_sheets", str(sample)])
    library_load_sheets.main()
    output = capsys.readouterr().out
    assert "created" in output.lower() or "Test-Compound-9" in output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_library_load_sheets.py -v`
Expected: FAIL — `app.library_load_sheets` doesn't exist yet.

- [ ] **Step 3: Implement `app/library_load_sheets.py`**

Mirror `app/library_load.py`'s own structure (`if __name__ == "__main__":` calling a `main()`
function, its own `SessionLocal` usage for a real DB session). `main()` reads `sys.argv[1:]` as a
list of file paths (never a directory, never a glob — each argument is read individually via
`open(path, encoding="utf-8").read()`), calls `parse_sheet` on each, attaches an empty
`usage_tips: []` by default (the curated-tips step described in the spec happens by hand later,
outside this script, when the user actually supplies real files for that purpose — this script's
job is just to prove the pipeline works end-to-end, not to perform that judgment call), and calls
`load_sheets` once with the full list, printing the returned `LoadReport.summary()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_library_load_sheets.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add app/library_load_sheets.py tests/test_library_load_sheets.py
git commit -m "feat: add CLI entry point for importing named peptide reference sheet files"
```

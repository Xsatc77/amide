# Peptide Reference Sheet Import Design (sub-project 1 of 3)

**Goal:** the first of three sub-projects for bringing 152 rich peptide reference files (provided
one at a time by the user; the app never browses for or reads them on its own) into the Library —
Data Organization (this spec's data model), the File Parser that turns a file into that data, and
running it over the full set. Library Redesign (surfacing this depth of data in the UI) and
Protocols wiring (dosing-tier suggestions, cycling defaults, stacking warnings) are separate,
later sub-projects with their own spec/plan/build cycles.

**Source material:** the user's own `.txt` exports of individual peptide reference pages (one
peptide per file), each following a consistent section structure. `all.txt` is an index/listing
page, not a peptide, and is discarded. Every file this pipeline processes is one the user hands
over directly — this spec does not authorize scanning a folder or reading anything the user hasn't
explicitly provided for this purpose.

**Deferred, explicitly out of scope for this spec:** the Library page's visual redesign (a
follow-on spec); wiring any of this data into the Protocol builder (a follow-on spec after that);
automatic file discovery — every file is supplied by the user, one at a time or as an explicit
batch they hand over, never read proactively.

## Field retention (confirmed with the user against the template file, `5-Amino-1MQ`)

**Header/identity — retained:**
- Name, aliases ("also known as")
- Classification tags/badges (e.g. "NNMT Inhibitor", "Fat Loss", "Oral Compound", "Small Molecule")
- Quick-fact badges: half-life, route, cycling shorthand
- Opening TL;DR summary paragraph

**Removed from the original proposal, per user correction:**
- Popularity count, community evidence tier, reference count, "Verified" date
- The entire "Science vs. Community Consensus" section (alignment label, scientific-evidence and
  community-experience sub-blocks, divergence summary)
- The "Before and After" phase-by-phase timeline and its "Science vs. Community" timeline table
- The redundant "What to Expect" retelling of the same timeline

**Narrative sections — retained as display-only text blocks** (`sheet_sections` JSON, see Data
model): What Is / How It Works (mechanism), Benefits (bulleted), Side Effects (narrative +
"Common Issues & Solutions"), Contraindications, Drug Interactions, Product Quality (risk label +
narrative + recommendations), Is It Legal? (narrative), Who Should Consider It, Related Peptides
(name + category, for future cross-linking).

**Citations/References:** retained, but rendered collapsed-until-expanded in the UI — a Library
Redesign concern; this spec just stores them in `sheet_sections` like the other narrative blocks.

**Structured/tabular data — retained as real columns/child tables (see Data model):**
- Dosage Guide table → `PeptideDosingTier`
- Cycling Protocol → `PeptideCycle`
- "Protocols by Goal" phased schedule → part of `sheet_sections` (narrative; not enough distinct
  peptides have more than one goal-phased schedule to justify its own table yet)
- Stacking Works With / Avoid → `PeptideStackRelation`
- Recommended Monitoring table → `PeptideMonitoringTest`
- Pharmacokinetics quick facts (half-life, bioavailability, Tmax, data-confidence) → real columns
- Storage & Stability (before/after reconstitution, temperature) → real columns
- Estimated Cost (type + narrative + price) → real column

**How to Use — re-scoped per user instruction, not stored verbatim:** the numbered steps in this
section are a mix of generic mechanical instructions (take a capsule, confirm strength, store
correctly, run the cycle length) that duplicate data already captured elsewhere, and genuine
timing/lifestyle tips (e.g. "take fasting," "with food if GI discomfort," "morning dosing," "split
50 mg morning / 50 mg afternoon"). Only the latter are retained, into a new `usage_tips` list on
`Peptide`. Distinguishing the two requires judgment, not a fixed keyword list — during the actual
per-file import (Task in the implementation plan), each file's "How to Use" section is read and
the genuine tips are extracted by hand for that file, the same way the field-retention list above
was produced by reading the template file directly, rather than by a regex heuristic that would
mis-classify a tip written in an unexpected phrasing. The raw "How to Use" text is NOT discarded
entirely — it still goes into `sheet_sections` for traceability, so a future re-review can check
the curated `usage_tips` against the source.

**Discarded as page furniture, not peptide data** (confirmed, unchanged from the earlier proposal):
"Calculate dose" / "Check with AI" buttons and the AI-provider list; the rendered decay-curve
chart's raw axis-label dump and its generic disclaimer; "Related Tools" (generic site nav, identical
on every page).

## New legal notices (Library Redesign concern, captured here so the requirement isn't lost)

Three disclaimers, shown on every peptide's page (not sourced from the `.txt` files, not stored
per-peptide — fixed template copy, added during Library Redesign):
1. This app's existing `/notice` legal disclaimer text.
2. "These dosages have not been verified by medical authorities unless otherwise stated."
3. "Procurement of some peptides is restricted in some jurisdictions. The data contained is for
   educational purposes only and is not an encouragement to circumvent any city, state, or federal
   laws governing the purchase and use of any illegal, unregulated, or 'gray market' medical
   supplies."

Exact placement (page footer vs. per-card) is a Library Redesign decision, not this spec's.

## Data model

```python
class PeptideSource(LabeledEnum):
    CARD = ("card", "Peptide card")        # existing
    SHEET = ("sheet", "Reference sheet")   # new -- this pipeline's source
    STARTER = ("starter", "Starter list")  # existing
    CUSTOM = ("custom", "Added by you")    # existing


class DosingTierLevel(str, enum.Enum):
    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"   # renamed from "Moderate" per user
    ADVANCED = "Advanced"           # renamed from "Aggressive" per user
    # Display color-coding (Beginner=green, Intermediate=yellow, Advanced=red) is a Library
    # Redesign concern -- flagged here so the requirement isn't lost, not implemented by this spec.


class StackRelation(str, enum.Enum):
    WORKS_WITH = "works_with"
    AVOID = "avoid"
```

`Peptide` gains (a name match with an existing `CARD`-sourced peptide is fully replaced -- its
`card_class`/`category`/`evidence_level`/`status`/`card_details`/`card_image` columns are cleared
and `source` becomes `SHEET`, per the user's explicit choice on the overlap question):

```python
    half_life_text: Mapped[str | None] = mapped_column(String(100))
    bioavailability_text: Mapped[str | None] = mapped_column(Text)
    tmax_text: Mapped[str | None] = mapped_column(String(100))
    route_summary: Mapped[str | None] = mapped_column(String(100))       # e.g. "Oral", "Injection"
    storage_before_text: Mapped[str | None] = mapped_column(Text)
    storage_after_text: Mapped[str | None] = mapped_column(Text)
    storage_temperature_text: Mapped[str | None] = mapped_column(String(100))
    legal_status_text: Mapped[str | None] = mapped_column(Text)
    cost_estimate_text: Mapped[str | None] = mapped_column(Text)
    usage_tips: Mapped[list[str] | None] = mapped_column(JSON)           # curated, see above
    sheet_sections: Mapped[dict | None] = mapped_column(JSON)            # narrative blocks, see above
```

New child tables:

```python
class PeptideDosingTier(Base):
    """One row per level (Beginner/Intermediate/Advanced) for a peptide's community dosing guide."""
    __tablename__ = "peptide_dosing_tiers"
    __table_args__ = (UniqueConstraint("peptide_id", "level", name="uq_peptide_dosing_tier_level"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    level: Mapped[DosingTierLevel] = mapped_column(_enum_column(DosingTierLevel))
    dose_text: Mapped[str] = mapped_column(String(100))          # e.g. "100mg", "500mcg"
    frequency_text: Mapped[str] = mapped_column(String(100))     # e.g. "Daily", "2x Daily"
    time_of_day: Mapped[TimeOfDay | None] = mapped_column(_enum_column(TimeOfDay))  # only if the
    # source text actually names a time (e.g. "each morning") -- never inferred/guessed.


class PeptideCycle(Base):
    __tablename__ = "peptide_cycles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), unique=True)
    on_weeks: Mapped[int | None] = mapped_column(Integer)
    off_weeks: Mapped[int | None] = mapped_column(Integer)
    max_cycles_per_year: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)


class PeptideStackRelation(Base):
    __tablename__ = "peptide_stack_relations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    partner_name: Mapped[str] = mapped_column(String(120))   # free text -- may not match a Peptide row
    relation: Mapped[StackRelation] = mapped_column(_enum_column(StackRelation))
    note: Mapped[str] = mapped_column(Text)


class PeptideMonitoringTest(Base):
    __tablename__ = "peptide_monitoring_tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    peptide_id: Mapped[int] = mapped_column(ForeignKey("peptides.id", ondelete="CASCADE"), index=True)
    test_name: Mapped[str] = mapped_column(String(120))
    when_text: Mapped[str] = mapped_column(String(200))
    why_text: Mapped[str] = mapped_column(Text)
    target_text: Mapped[str | None] = mapped_column(String(200))
```

## File Parser

New module `app/library/sheet_parser.py`: takes one file's raw text plus a hand-curated
`usage_tips` list and `retained` overrides (see below), returns a dict matching the fields above.

- Splits the file into sections using the literal sub-headings identified from the template file
  (`What Is <name>?`, `How <name> Works`, `<name> Benefits`, `<name> Dosage Guide`, `Cycling
  Protocol`, `How to Use <name>`, `<name> Stacking Protocols`, `Estimated Cost`, `<name> Side
  Effects`, `Contraindications`, `Drug Interactions`, `Product Quality`, `Recommended Monitoring`,
  `Pharmacokinetics`, `Is <name> Legal?`, `Who Should Consider <name>`, `Related Peptides`,
  `Storage & Stability`) rather than the page's own nav breadcrumb, which only anchors coarser
  groups and skips several of these as sub-sections.
- The Dosage Guide table is parsed by column position (Level / Dose / Frequency), not exact header
  text — confirmed necessary since one sampled file used "DOSE / INJECTION" instead of "DOSE" for
  the same column.
- Defensive throughout: a missing expected section (e.g. no Cycling Protocol for a continuous-use
  compound) leaves that field/table empty, never a crash. A file that doesn't match the expected
  shape at all is skipped and reported, not silently dropped.
- `usage_tips` is NOT derived by the parser itself — it's supplied per-file (see How to Use above),
  since distinguishing a genuine tip from generic instruction is a judgment call made once per file
  during import, not a mechanical extraction.

A one-shot import script, `python -m app.library_load_sheets`, mirrors the existing
`library_load.py` convention: takes the parsed-and-curated dict list (not a folder path — this
pipeline is never handed a directory to scan on its own) and calls a new `load_sheets(session,
sheets)` function in `app/library/loader.py`, alongside the existing `load_cards`. `load_sheets`
matches by name (case-insensitive, matching `load_cards`' own convention), fully replacing an
existing `CARD`-sourced peptide's card fields per the confirmed overlap rule, or creating a new
`Peptide` row with `source=SHEET`.

## Review focus

1. A peptide whose text file has no Cycling Protocol section (a continuous-use compound) must get
   a `Peptide` with no `PeptideCycle` row at all, never a row with null/zero weeks that could be
   misread as "0 weeks on."
2. A name match against an existing `CARD`-sourced peptide must fully clear that peptide's old
   `card_class`/`category`/`evidence_level`/`status`/`card_details`/`card_image` fields, not leave
   stale old-card data sitting alongside the new sheet fields.
3. `usage_tips` must never include a step that's purely mechanical (e.g. "confirm your capsule
   strength," "store at room temperature" when Storage & Stability already captures it) — the
   per-file curation step is the safeguard here, not a mechanical filter.
4. `time_of_day` on a `PeptideDosingTier` row must stay null when the source text doesn't name an
   actual time — never inferred from context (e.g. never guessing AM just because oral dosing is
   typically taken with breakfast).
5. The Dosage Guide table parser must handle the column-header variance already observed (e.g.
   "DOSE" vs. "DOSE / INJECTION") by position, not by requiring an exact header string match.

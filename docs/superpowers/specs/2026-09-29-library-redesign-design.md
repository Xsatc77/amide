# Peptide Library Redesign — Design

**Spec status:** approved in chat; pending written-spec review.

## Problem

The Library list and detail pages (`app/templates/library/list.html`,
`app/templates/library/detail.html`) were built entirely around the old
"graphical card" data (`Peptide.card_details`, `card_class`,
`evidence_level`, `card_image`). The peptide-sheet-import pipeline (built
earlier this project) now populates 144 of ~150 peptides with much richer
data — `summary`, `tags`, `half_life_text`, `dosing_tiers`,
`cycle`, `stack_relations`, `monitoring_tests`, `sheet_sections_simple`,
`storage_*`, `legal_status_text` — but `card_details` is cleared (set to
`None`) for every sheet-sourced peptide, so the existing templates render
"No card imported for this peptide" for all of them even though rich data
exists. The list page shows "No card" as the subtitle for the same reason.

This redesign updates both pages to render sheet-sourced data properly,
without breaking the existing rendering path for card-sourced peptides
(remaining ones not yet converted) or user-added STARTER/CUSTOM peptides
(which have neither).

## Goals

- A sheet-sourced peptide's detail page shows: name, goal-stack chips,
  color-coded tags, summary, quick facts (half-life/route/cycle),
  dosing tiers, cycle, stacking (works-with/avoid), monitoring tests,
  storage, and the plain-language narrative sections — in that order.
- A sheet-sourced peptide's list tile shows color-coded tags instead of
  "No card".
- Every one of the 8 existing protocol Goals gets its own color; a
  peptide's own goal-stack membership (from the existing `GoalPeptide`
  table) renders as small colored chips using that palette, and the
  Goal Cards on the Protocols page (`/protocols`) are recolored to match
  (they currently share one plain accent color).
- Each peptide **tag** (free text like "Tissue Repair", "GLP-1 Agonist")
  is mapped, by hand, to 0–3 of the 8 Goals and rendered with that
  goal's color(s) — a background split into equal diagonal (135°) bands
  when a tag maps to more than one goal. A tag with no goal mapping
  (grades, "FDA-Approved", "Research Only", chemical-class descriptors
  like "Tetrapeptide") renders in a neutral grey, never forced into an
  unrelated goal's color.
- Card-sourced peptides (any peptide with `card_details` still set)
  render exactly as they do today — this redesign adds a second
  rendering path, it does not touch the existing one.
- STARTER/CUSTOM peptides with neither card nor sheet data keep showing
  today's "No card imported" empty state.

## Explicitly out of scope

- **No cost/pricing display.** `cost_estimate_text` stays in the
  database but is never rendered — prices go stale too fast to be worth
  showing.
- **No original-vs-simplified toggle.** Only `sheet_sections_simple` is
  shown for now; `sheet_sections` (the original scientific text) stays
  in the database, unused by the UI, until a future decision to expose
  it.
- **No card numbering scheme for sheet-sourced peptides.** `card_number`
  only ever made sense for the original 100-card set. Rather than invent
  a numbering scheme for 150+ peptides, the "Card N" badge is dropped
  from the detail page entirely (for every peptide, not just
  sheet-sourced ones), and both pages sort alphabetically by name instead
  of by card number. The `card_number` column stays in the schema, just
  unused by these templates now.
- **usage_tips** stays supported by the data model and (if ever
  populated) would render as a distinct "Tips" callout, but is out of
  scope for this pass since no peptide currently has any curated tips.

## Data model additions

No changes to `Peptide` or its child tables — every field this redesign
needs already exists from the peptide-sheet-import work. Two new,
purely presentational additions:

### `app/goals.py` — per-goal color

```python
@dataclass(frozen=True)
class Goal:
    slug: str
    label: str
    description: str
    color: str  # NEW: a CSS custom-property suffix, e.g. "fat-loss"
```

Each goal's `color` is a short token (`fat-loss`, `muscle-recovery`,
`gh-performance`, `longevity`, `skin-beauty`, `wellness`, `glp1-weight`,
`sleep-recovery`) used to look up `--goal-<token>` and
`--goal-<token>-soft` CSS variables — mirrors how `--accent`/
`--accent-soft` already work. Palette (light mode; dark-mode variants
follow the same lighten/darken pattern already used for `--accent-soft`
under `@media (prefers-color-scheme: dark)`):

| Goal | token | color |
|---|---|---|
| Fat Loss / Metabolic | `fat-loss` | `#f97316` (orange) |
| Muscle & Recovery | `muscle-recovery` | `#22c55e` (emerald) |
| Growth Hormone / Performance | `gh-performance` | `#8b5cf6` (violet) |
| Longevity & Cellular Health | `longevity` | `#6366f1` (indigo) |
| Skin & Beauty | `skin-beauty` | `#ec4899` (rose) |
| Wellness / General Health | `wellness` | `#eab308` (amber) |
| GLP-1 / Weight Management | `glp1-weight` | `#06b6d4` (cyan) |
| Sleep & Recovery | `sleep-recovery` | `#3b82f6` (blue) |

### `app/library/tag_goals.py` — tag → goal(s) mapping (new module)

```python
# Maps a peptide tag (exact string, as stored in Peptide.tags) to 1-3 goal
# slugs from app.goals.GOALS_BY_SLUG. A tag absent from this mapping (or
# explicitly mapped to an empty list) renders in the neutral/grey style --
# this covers safety grades, regulatory status, and chemical-class
# descriptors that aren't about a use-case goal at all.
TAG_GOALS: dict[str, list[str]] = {
    "Tissue Repair": ["muscle-recovery"],
    "Gut Health": ["wellness", "glp1-weight", "fat-loss"],
    "GLP-1 Agonist": ["glp1-weight"],
    "FDA-Approved": [],       # neutral -- regulatory status, not a goal
    "Research Only": [],      # neutral
    "Grade A": [], "Grade B": [], "Grade C": [], "Grade D": [],  # neutral
    ...
}


def goal_colors_for_tag(tag: str) -> list[str]:
    """Returns 0-3 goal color tokens for a tag, equally weighted."""
    return TAG_GOALS.get(tag, [])
```

Built by hand (by the assistant) covering the ~450 unique tags present
across the current 150-peptide corpus at implementation time, using the
same judgment-based approach already used for narrative simplification —
not a keyword heuristic. A tag introduced by a future import that isn't
yet in `TAG_GOALS` simply renders neutral until someone adds it; this is
a graceful degradation, not a crash.

A Jinja filter/macro renders the tag chip's background from this list:
zero entries → `.tag-neutral` (grey); one entry → solid goal color; two
or three entries → a `linear-gradient(135deg, ...)` with hard color
stops at equal fractions (50/50, or 33/33/34).

## Detail page (`library/detail.html`)

Branch strictly on `p.source == PeptideSource.SHEET` (the authoritative
flag `load_sheets` sets) rather than on any individual field like
`p.summary`, since a future sheet import could in principle leave an
individual field like `summary` empty for one file while still being
sheet-sourced. Two full rendering paths in the same
template file (Jinja `{% if %}` at the top), sharing only the outer
page chrome and the existing right-hand sidebar ("Your info" /
"Used in protocols", unchanged).

**Sheet-sourced path**, top to bottom:

1. **Header**: `p.name`, goal-stack chips (colored per the palette
   above, from the existing `goals` context var — already computed by
   `_goal_map()`), tag chips (colored/gradient per `tag_goals.py`, grey
   for unmapped).
2. **Summary**: `p.summary` as a lead paragraph.
3. **Quick facts strip**: `p.half_life_text`, `p.route_summary`,
   `p.cycle.on_weeks`/`off_weeks` shown as "Nw on / Mw off" (reusing the
   existing `PeptideCycle` relationship) — three-column strip, any
   missing value simply omitted (not a blank cell).
4. **Dosing tiers** table: one row per `PeptideDosingTier`
   (Beginner/Intermediate/Advanced), columns Dose / Frequency / Time of
   day (blank cell, not "None", when a tier lacks a value) — ordered by
   the tier's canonical order, not insertion order.
5. **Cycle note**: `p.cycle.note`, if present (the on/off numbers
   already shown in the quick-facts strip above).
6. **Stacking**: two columns from `p.stack_relations`, split by
   `relation == StackRelation.WORKS_WITH` vs `AVOID`, each entry showing
   `partner_name` + `note`.
7. **Monitoring**: table from `p.monitoring_tests` — Test / When / Why
   / Target, blank cell for a missing value.
8. **Narrative sections**, each only rendered if present in
   `p.sheet_sections_simple`, in this fixed order: What Is, How It
   Works, Benefits, Side Effects, Contraindications, Drug Interactions,
   Legal, Who Should Consider, Product Quality. Bullet-style sections
   (benefits, contraindications, drug_interactions, who_should_consider)
   render as a `<ul>`; the rest as a paragraph.
9. **Storage**: `storage_before_text` / `storage_after_text` /
   `storage_temperature_text`, if any are present.

**Card-sourced path**: today's template, byte-for-byte unchanged (just
moved under the `{% else %}` branch).

**Neither** (STARTER/CUSTOM with no sheet, no card): today's "No card
imported for this peptide" empty state, unchanged.

## List page (`library/list.html`)

- Sort order changes from `card_number.is_(None), card_number, name` to
  just `name` (case-insensitive, matching the column's own collation) —
  applies to every peptide, not only sheet-sourced ones, since the "Card
  N" concept is being dropped project-wide per the numbering decision
  above.
- Each tile's second line: card-sourced peptides keep showing
  `card_class` exactly as today. Sheet-sourced peptides show up to 3 tag
  chips (same colored/gradient/grey rendering as the detail page, sized
  down) instead of the current plain-text "No card". Peptides with
  neither (STARTER/CUSTOM, no sheet) keep showing nothing, as today.
- The `evidence_level` tag on a tile only ever applied to card-sourced
  peptides (`p.evidence_level` is cleared on sheet import) — unchanged,
  it simply won't appear for sheet-sourced tiles, same as today.
- Search (`data-search` attribute, `lib.js`) gains sheet-sourced tags to
  its search haystack (currently `name, aliases, card_class, category`)
  so searching "gut health" finds BPC-157 etc.

## Protocols page — Goal Cards recolor

`app/templates/protocols/list.html`'s `.goal-card` elements currently
share one flat `--accent` color for their pressed state and border.
Each card gets a `data-goal-color="{{ g.color }}"` (or a per-goal CSS
class) so its accent border/background-on-press uses that goal's own
`--goal-<token>`/`--goal-<token>-soft` variables instead of the shared
`--accent`. This is the only change to that page — layout, copy, and
interaction (multi-select, "Build protocol" link) are untouched.

## Testing

- A route test for `/library/{id}` on a sheet-sourced peptide asserts
  the new sections' key content appears in the rendered HTML (summary
  text, a dosing tier's dose text, a narrative section's simplified
  text) and that `sheet_sections` (the original text) does NOT appear
  anywhere in the response.
- A route test for `/library/{id}` on an untouched card-sourced peptide
  asserts the response is unchanged from today (same key strings as the
  existing card-path tests already cover).
- A route test for `/library/{id}` on a STARTER/CUSTOM peptide with
  neither sheet nor card data still shows the empty state.
- A unit test for `goal_colors_for_tag()` covering: a single-goal tag, a
  multi-goal tag, and an unmapped tag (empty list → neutral).
- A route test for `/library` (list page) confirms a sheet-sourced tile
  renders tag chips and a card-sourced tile still renders `card_class`.
- A render test (or a simple string-content assertion) confirms the
  "Card N" badge no longer appears anywhere on either page.

## Review Focus

1. A peptide with `sheet_sections_simple` present but missing 2-3 of the
   9 narrative keys (a real, common case — not every file had every
   section) must render only the sections it has, not empty headings.
2. A peptide with zero dosing tiers (shouldn't happen per the importer,
   but defensively) must not render an empty table.
3. A tag present in `Peptide.tags` but absent from `TAG_GOALS` must
   render neutral, never raise a `KeyError`.
4. The list page's alphabetical sort must be case-insensitive and
   stable for the small number of peptides sharing a first letter.
5. A STARTER/CUSTOM peptide that has `card_details` explicitly set to
   `{}` (empty dict, not `None` — possible via manual edits) must be
   treated as "no card", not crash on empty-dict truthiness edge cases
   the current template's `{% if not card %}` already handles via
   `p.card_details or {}` upstream in the route.

# Peptide Library Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Library list and detail pages render the peptide-sheet-import data (summary, tags, dosing tiers, cycle, stacking, monitoring, plain-language narrative sections) that's currently invisible in the UI, with peptide tags color-coded by their related protocol Goal(s), while leaving the existing card-sourced and empty-state rendering paths untouched.

**Architecture:** Two small new modules (`app/goals.py` gets a `color` field; a new `app/library/tag_goals.py` holds a hand-curated tag→goal(s) mapping) feed two new Jinja filters that render a tag chip's color/gradient. `app/routers/library.py` and its two templates gain a second rendering branch, selected on `Peptide.source == PeptideSource.SHEET`, alongside the existing card-sourced and empty-state branches which are not modified. The Protocols page's Goal Cards get the same per-goal colors in a final small task.

**Tech Stack:** FastAPI + Jinja2 (existing `app.templating.templates`), SQLAlchemy ORM, plain CSS custom properties (no new frontend dependency).

**Spec:** docs/superpowers/specs/2026-09-29-library-redesign-design.md

## Global Constraints

- No cost/pricing is ever rendered (`cost_estimate_text` stays in the DB, unused by these templates).
- Only `sheet_sections_simple` is rendered; `sheet_sections` (original scientific text) is never rendered by any template in this plan.
- The "Card N" badge is removed from the detail page for every peptide (not just sheet-sourced), and both the list and detail pages sort/order alphabetically by name instead of by `card_number`.
- Card-sourced peptides (`p.card_details` truthy) and STARTER/CUSTOM peptides with neither card nor sheet data must render byte-for-byte as they do today — every existing test in `tests/test_library.py` must keep passing unmodified.
- Goal colors are defined once (`:root`) and are NOT duplicated per named colorway (`tequila_sunrise`, `fireworks`, etc.) — they're saturated enough to read on any of the app's existing surface colors, so a single definition is enough (YAGNI: touching 9 colorway blocks for a set of already-vivid accent colors is not worth the maintenance surface).
- A peptide tag not present in the hand-curated `TAG_GOALS` mapping must render neutral (grey, reusing the existing `.tag-plain` class) and must never raise.

## Review Focus

- A sheet-sourced peptide missing some of the 9 narrative keys (the common case — not every source file had every section) must render only the sections it has, never an empty heading. → Task 4.
- A sheet-sourced peptide with zero dosing tiers (defensive; shouldn't happen from the importer, but a manually-added SHEET-sourced row could) must not render a header-only empty table. → Task 4.
- A tag mapped to 2 or 3 goals must render a gradient with hard (not blended) color stops at equal fractions, per the spec's "diagonal, not vertical, breaks" requirement — the gradient angle must be 135deg, never a default top-to-bottom gradient. → Task 3.
- The list page's alphabetical sort must be case-insensitive (matching `Peptide.name`'s own `COLLATE NOCASE`), so e.g. "aicar" and "AICAR" interleave correctly with the rest of the alphabet rather than all-lowercase or all-uppercase names clustering separately. → Task 5.
- A STARTER/CUSTOM peptide with `card_details` explicitly set to `{}` (an empty dict, not `None`) must still show the existing "No card imported" empty state, not a broken half-rendered card section — this already works today via `p.card_details or {}` in the route, but Task 4's new branch condition must not disturb it. → Task 4.

---

### Task 1: Goal color palette

**Files:**
- Modify: `app/goals.py`
- Modify: `app/static/css/app.css`
- Test: `tests/test_goals.py` (new file)

**Interfaces:**
- Produces: `Goal.color: str` (a short token like `"fat-loss"`), and CSS custom properties `--goal-<token>` for all 8 tokens, consumed by Task 3's filters and Task 6's Protocols template.

- [ ] **Step 1: Write the failing test**

Create `tests/test_goals.py`:

```python
from app.goals import GOALS, GOALS_BY_SLUG


def test_every_goal_has_a_unique_color_token():
    tokens = [g.color for g in GOALS]
    assert len(tokens) == len(GOALS)
    assert len(set(tokens)) == len(GOALS), "goal color tokens must be unique"
    assert all(tokens)  # none blank


def test_goals_by_slug_exposes_color():
    assert GOALS_BY_SLUG["fat-loss"].color == "fat-loss"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_goals.py -v`
Expected: FAIL with `TypeError: Goal.__init__() missing 1 required positional argument: 'color'` (or `AttributeError` if `Goal` doesn't take `color` yet).

- [ ] **Step 3: Add the `color` field and populate all 8 tokens**

Modify `app/goals.py`:

```python
"""The fixed list of protocol goals shown as cards on the Protocols page."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Goal:
    slug: str
    label: str
    description: str
    color: str  # token used to look up --goal-<color> in app.css


GOALS: tuple[Goal, ...] = (
    Goal("fat-loss", "Fat Loss / Metabolic", "Metabolism and body composition", "fat-loss"),
    Goal("muscle-recovery", "Muscle & Recovery", "Tissue repair and recovery", "muscle-recovery"),
    Goal("gh-performance", "Growth Hormone / Performance", "GH secretagogues and performance", "gh-performance"),
    Goal("longevity", "Longevity & Cellular Health", "Mitochondrial and cellular health", "longevity"),
    Goal("skin-beauty", "Skin & Beauty", "Skin, hair and appearance", "skin-beauty"),
    Goal("wellness", "Wellness / General Health", "Immune, mood and general support", "wellness"),
    Goal("glp1-weight", "GLP-1 / Weight Management", "Incretin-based weight management", "glp1-weight"),
    Goal("sleep-recovery", "Sleep & Recovery", "Rest and restoration", "sleep-recovery"),
)

GOALS_BY_SLUG: dict[str, Goal] = {g.slug: g for g in GOALS}
```

(Each goal's `color` token is identical to its `slug` here since all 8 slugs are
already unique and CSS-safe — this keeps the two concepts obviously in sync
rather than inventing a second, separately-maintained vocabulary.)

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_goals.py -v`
Expected: PASS (2/2)

- [ ] **Step 5: Add the CSS custom properties**

Modify `app/static/css/app.css` — add immediately after the closing `}` of the
top `:root { ... }` block (the one starting `--bg: #f6f7f8;`, before the
`@media (prefers-color-scheme: dark)` block):

```css
:root {
  /* Per-goal accent colors, shared by the Library's tag chips and the
     Protocols page's Goal Cards. One definition for all colorways (see
     Global Constraints) -- these are saturated enough to read on any of
     the app's existing surface colors. */
  --goal-fat-loss: #f97316;
  --goal-muscle-recovery: #22c55e;
  --goal-gh-performance: #8b5cf6;
  --goal-longevity: #6366f1;
  --goal-skin-beauty: #ec4899;
  --goal-wellness: #eab308;
  --goal-glp1-weight: #06b6d4;
  --goal-sleep-recovery: #3b82f6;
}
```

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass (no regressions; CSS changes aren't exercised by pytest).

- [ ] **Step 7: Commit**

```bash
git add app/goals.py app/static/css/app.css tests/test_goals.py
git commit -m "feat: add a color token to each protocol Goal"
```

---

### Task 2: Tag → goal(s) mapping

**Files:**
- Create: `app/library/tag_goals.py`
- Test: `tests/test_tag_goals.py`

**Interfaces:**
- Consumes: `app.goals.GOALS_BY_SLUG` (Task 1) for slug validation.
- Produces: `goal_colors_for_tag(tag: str) -> list[str]` — returns 0-3 goal
  slugs (not color hexes; Task 3's filter looks up the color). Consumed by
  Task 3.

This task's real deliverable is `TAG_GOALS`, a hand-curated mapping covering
every tag currently in the live database — not a keyword heuristic. Do NOT
guess a mapping from the tag string alone; use judgment the same way the
narrative-simplification work earlier in this project did.

- [ ] **Step 1: Extract the current tag vocabulary to work from**

Run this to get every unique tag currently in the database, most common first
(this is a live snapshot to curate against, not something to commit):

```bash
py -c "
import sys
sys.path.insert(0, '.')
from app.db import SessionLocal
from app.models import Peptide, PeptideSource
from collections import Counter
db = SessionLocal()
c = Counter()
for p in db.query(Peptide).filter(Peptide.source == PeptideSource.SHEET):
    for t in (p.tags or []):
        c[t] += 1
for tag, n in c.most_common():
    print(tag)
db.close()
" > /tmp/tag_vocab.txt
wc -l /tmp/tag_vocab.txt
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_tag_goals.py`:

```python
from app.goals import GOALS_BY_SLUG
from app.library.tag_goals import TAG_GOALS, goal_colors_for_tag


def test_unmapped_tag_returns_empty_list():
    assert goal_colors_for_tag("Not A Real Tag XYZ") == []


def test_every_mapped_slug_is_a_real_goal():
    for tag, slugs in TAG_GOALS.items():
        assert len(slugs) <= 3, f"{tag!r} maps to more than 3 goals"
        for slug in slugs:
            assert slug in GOALS_BY_SLUG, f"{tag!r} maps to unknown goal {slug!r}"


def test_a_known_single_goal_tag():
    # "Tissue Repair" is unambiguously about recovery from injury.
    assert goal_colors_for_tag("Tissue Repair") == ["muscle-recovery"]


def test_a_known_multi_goal_tag():
    # "Gut Health" plausibly relates to general wellness, GLP-1/weight
    # management, and fat loss -- must be exactly these three, in this order.
    assert goal_colors_for_tag("Gut Health") == ["wellness", "glp1-weight", "fat-loss"]


def test_grade_and_regulatory_tags_are_unmapped():
    # Safety grades and regulatory status aren't about a use-case goal.
    for tag in ("Grade A", "Grade B", "Grade C", "Grade D", "FDA-Approved", "Research Only"):
        assert goal_colors_for_tag(tag) == [], f"{tag!r} should be neutral (unmapped)"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `py -m pytest tests/test_tag_goals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.library.tag_goals'`

- [ ] **Step 4: Build `app/library/tag_goals.py`**

```python
"""Hand-curated mapping from a peptide tag (exact string, as stored in
Peptide.tags) to 0-3 protocol-goal slugs from app.goals.GOALS_BY_SLUG.

A tag absent from TAG_GOALS (or explicitly mapped to an empty list) renders
neutral/grey -- this deliberately covers safety grades ("Grade A"..."Grade D"),
regulatory status ("FDA-Approved", "Research Only"), and chemical-class
descriptors ("Tetrapeptide", "Bioregulator") that aren't about a use-case goal
at all, so they are never forced into an unrelated goal's color.

When a tag maps to more than one goal, list slugs in a stable, meaningful
order (most-relevant goal first) -- Task 3's gradient renderer uses this
order for its diagonal color bands, so the order is user-visible.
"""

TAG_GOALS: dict[str, list[str]] = {
    "Tissue Repair": ["muscle-recovery"],
    "Gut Health": ["wellness", "glp1-weight", "fat-loss"],
    "GLP-1 Agonist": ["glp1-weight"],
    "Weight Management": ["glp1-weight", "fat-loss"],
    "Fat Loss": ["fat-loss"],
    "Anti-Aging": ["longevity"],
    "Neuroprotective": ["longevity"],
    "Neuroprotection": ["longevity"],
    "Oral Peptide": [],
    "Nootropic": ["gh-performance", "longevity"],
    "Growth Hormone": ["gh-performance"],
    "Growth Hormone Secretagogue": ["gh-performance"],
    "GH Secretagogue": ["gh-performance"],
    "Performance Enhancement": ["gh-performance"],
    "Body Recomposition": ["muscle-recovery", "fat-loss"],
    "Accelerated Healing": ["muscle-recovery"],
    "Wound Healing": ["muscle-recovery"],
    "Recovery": ["muscle-recovery", "sleep-recovery"],
    "Appetite Regulation": ["glp1-weight"],
    "Topical Peptide": ["skin-beauty"],
    "Anti-Wrinkle": ["skin-beauty"],
    "Cosmeceutical": ["skin-beauty"],
    "Anti-Inflammatory": ["wellness"],
    "Immunomodulatory": ["wellness"],
    "Cognitive Enhancement": ["longevity", "gh-performance"],
    "Anxiolytic": ["wellness", "sleep-recovery"],
    "BDNF Enhancer": ["longevity"],
    "Sleep Regulation": ["sleep-recovery"],
    # Safety grades, regulatory status, and pure chemical-class descriptors
    # are deliberately unmapped (render neutral) -- they are not a use-case
    # goal, and forcing one would misrepresent what the tag actually says.
    "Grade A": [], "Grade B": [], "Grade C": [], "Grade D": [],
    "FDA-Approved": [], "Research Only": [], "Bioregulator": [],
    "Tetrapeptide": [], "Neuropeptide": [], "Khavinson": [], "Khavinson Peptide": [],
}


def goal_colors_for_tag(tag: str) -> list[str]:
    """0-3 goal slugs for a tag, equally weighted, in display order. Returns
    an empty list for any tag not in TAG_GOALS -- never raises."""
    return TAG_GOALS.get(tag, [])
```

The five tags above (`test_a_known_single_goal_tag` etc.) are the ones the
test file pins exactly. Beyond those, populate `TAG_GOALS` with every tag
from `/tmp/tag_vocab.txt` (Step 1) using the same judgment: 1-3 goals for a
genuinely thematic tag, an empty list for anything that's a grade, a
regulatory label, or a bare chemical/structural class name. This is real
curation work across ~450 entries — do not stop at the handful shown above.

- [ ] **Step 5: Run test to verify it passes**

Run: `py -m pytest tests/test_tag_goals.py -v`
Expected: PASS (5/5)

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/library/tag_goals.py tests/test_tag_goals.py
git commit -m "feat: add hand-curated tag-to-goal mapping for Library tag colors"
```

---

### Task 3: Tag chip color/gradient rendering

**Files:**
- Modify: `app/templating.py`
- Modify: `app/static/css/app.css`
- Test: `tests/test_templating.py` (new file)

**Interfaces:**
- Consumes: `app.library.tag_goals.goal_colors_for_tag` (Task 2).
- Produces: Jinja filter `tag_style(tag: str) -> str` (an inline CSS
  `background`/`color` declaration string, or `""` for a neutral tag),
  registered as `templates.env.filters["tag_style"]`. Consumed by Task 4
  (detail page) and Task 5 (list page) templates identically.

- [ ] **Step 1: Write the failing test**

Create `tests/test_templating.py`:

```python
from app.templating import tag_style


def test_tag_style_empty_for_unmapped_tag():
    assert tag_style("Grade A") == ""


def test_tag_style_solid_color_for_single_goal_tag():
    style = tag_style("Tissue Repair")
    assert "var(--goal-muscle-recovery)" in style
    assert "linear-gradient" not in style


def test_tag_style_diagonal_gradient_for_multi_goal_tag():
    style = tag_style("Gut Health")
    assert "linear-gradient(135deg" in style
    assert "var(--goal-wellness)" in style
    assert "var(--goal-glp1-weight)" in style
    assert "var(--goal-fat-loss)" in style
    # Three equal hard-stop bands, not a blended gradient: each color's own
    # stop must appear twice (start % and end %) with no soft midpoint.
    assert style.count("var(--goal-wellness)") == 1  # one background-image color-stop entry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_templating.py -v`
Expected: FAIL with `ImportError: cannot import name 'tag_style' from 'app.templating'`

- [ ] **Step 3: Implement `tag_style` and register it**

Modify `app/templating.py` — add near the other filter functions:

```python
from app.library.tag_goals import goal_colors_for_tag


def tag_style(tag: str) -> str:
    """Inline CSS for a tag chip: solid goal color for a single-goal tag, a
    135-degree hard-stop gradient across equal bands for a multi-goal tag,
    or "" for a tag with no goal mapping (the template falls back to the
    existing .tag-plain neutral styling for that case)."""
    slugs = goal_colors_for_tag(tag)
    if not slugs:
        return ""
    if len(slugs) == 1:
        return f"background: var(--goal-{slugs[0]}); color: #fff;"
    band = 100 / len(slugs)
    stops = []
    for i, slug in enumerate(slugs):
        start = i * band
        end = (i + 1) * band
        stops.append(f"var(--goal-{slug}) {start:.4g}% {end:.4g}%")
    return f"background: linear-gradient(135deg, {', '.join(stops)}); color: #fff;"


templates.env.filters["tag_style"] = tag_style
```

(Place the `from app.library.tag_goals import ...` alongside the existing
`from app.goals import GOALS_BY_SLUG` import at the top of the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_templating.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Add the CSS class for a colored tag chip**

Modify `app/static/css/app.css` — add near the existing `.tag` / `.tag-plain`
rules (search for `.tag-plain` in the file):

```css
.tag-goal { color: #fff; border: none; }
```

(`.tag-plain`, used for the neutral/unmapped case, already exists and needs
no change.)

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/templating.py app/static/css/app.css tests/test_templating.py
git commit -m "feat: render peptide tag chips in their goal's color(s)"
```

---

### Task 4: Detail page — sheet-sourced rendering path

**Files:**
- Modify: `app/routers/library.py:64-73` (the `library_detail` route)
- Modify: `app/templates/library/detail.html`
- Test: `tests/test_library.py`

**Interfaces:**
- Consumes: `PeptideSource` (existing), `p.tags | tag_style` (Task 3),
  `goals | map('goal_label')` (existing filter), `DosingTierLevel` (existing,
  for canonical tier ordering).
- Produces: nothing new consumed by a later task in this plan (Task 5 is
  independent).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_library.py` (near the existing detail-page tests — open
the file first and place these alongside the other `def test_detail...`
functions so fixtures like `client`, `bpc`, `db` are already in scope):

```python
def _sheet_test_peptide(db) -> Peptide:
    """A fresh SHEET-sourced peptide with one of everything this task
    renders. Cleaned up automatically by the autouse `clean` fixture
    (conftest.py), which deletes SHEET-sourced peptides -- and their child
    rows via the DB-level ON DELETE CASCADE -- after the test."""
    from app.models import (
        DosingTierLevel, PeptideCycle, PeptideDosingTier, PeptideMonitoringTest,
        PeptideStackRelation, StackRelation, TimeOfDay,
    )
    p = Peptide(
        name="Test-Sheet-Peptide", source=PeptideSource.SHEET,
        summary="A made-up plain-language summary for testing.",
        tags=["Tissue Repair", "Grade A"], half_life_text="~3-5 hours",
        route_summary="Injection",
        sheet_sections_simple={
            "what_is": "A made-up plain-language description for testing.",
            "benefits": "A made-up plain-language benefit for testing.",
        },
        sheet_sections={"what_is": "THE ORIGINAL SCIENTIFIC TEXT MUST NEVER RENDER"},
    )
    db.add(p)
    db.flush()
    p.dosing_tiers = [PeptideDosingTier(level=DosingTierLevel.BEGINNER, dose_text="10mg",
                                         frequency_text="Daily", time_of_day=TimeOfDay.AM)]
    p.cycle = PeptideCycle(on_weeks=6, off_weeks=4, note="A made-up cycle note for testing.")
    p.stack_relations = [PeptideStackRelation(partner_name="Made-Up-Partner", relation=StackRelation.WORKS_WITH,
                                               note="A made-up stacking note for testing.")]
    p.monitoring_tests = [PeptideMonitoringTest(test_name="Made-up test", when_text="Baseline",
                                                 why_text="A made-up reason for testing.")]
    db.commit()
    return p


def test_detail_renders_sheet_sourced_peptide(client, db):
    p = _sheet_test_peptide(db)
    resp = client.get(f"/library/{p.id}")
    assert resp.status_code == 200
    body = resp.text
    assert "A made-up plain-language summary for testing." in body
    assert "A made-up plain-language description for testing." in body
    assert "10mg" in body and "Daily" in body
    assert "A made-up cycle note for testing." in body
    assert "Made-Up-Partner" in body
    assert "Made-up test" in body
    # The original scientific text must never appear -- only the simplified version.
    assert "THE ORIGINAL SCIENTIFIC TEXT MUST NEVER RENDER" not in body


def test_detail_sheet_sourced_tags_get_goal_colors(client, db):
    p = _sheet_test_peptide(db)
    resp = client.get(f"/library/{p.id}")
    body = resp.text
    assert "var(--goal-muscle-recovery)" in body  # "Tissue Repair" tag
    assert "tag-plain" in body  # "Grade A" tag renders neutral


def test_detail_sheet_sourced_peptide_missing_some_sections_renders_only_present_ones(client, db):
    """Review Focus: a sheet-sourced peptide missing most of the 9 narrative keys must not
    render empty headings for the missing ones."""
    p = _sheet_test_peptide(db)
    p.sheet_sections_simple = {"what_is": "Only this one section exists for testing."}
    db.commit()
    resp = client.get(f"/library/{p.id}")
    body = resp.text
    assert "Only this one section exists for testing." in body
    assert "Side Effects" not in body
    assert "Contraindications" not in body


def test_detail_card_sourced_peptide_unchanged(client, db):
    """Existing card-sourced rendering must not regress."""
    p = with_card(db)
    resp = client.get(f"/library/{p.id}")
    assert resp.status_code == 200
    assert "No card imported" not in resp.text
    assert "Card " not in resp.text  # the "Card N" badge is removed project-wide


def test_detail_no_card_no_sheet_peptide_shows_empty_state(client, db):
    p = Peptide(name="Test-Bare-Peptide", source=PeptideSource.CUSTOM)
    db.add(p)
    db.commit()
    resp = client.get(f"/library/{p.id}")
    assert "No card imported for this peptide." in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_library.py -k "sheet_sourced or no_card_no_sheet or card_sourced_peptide_unchanged" -v`
Expected: FAIL — `test_detail_card_sourced_peptide_unchanged` fails on the
`"Card "` assertion (today's template still shows "Card 2"); the sheet-sourced
tests fail because none of that content renders yet.

- [ ] **Step 3: Update the route**

Modify `app/routers/library.py`. Add a tier-ordering helper near the top
(after the existing `evidence_class` function) and pass a sorted-tiers list
into the template context:

```python
from app.models import DosingTierLevel  # add to the existing models import line

_TIER_ORDER = {DosingTierLevel.BEGINNER: 0, DosingTierLevel.INTERMEDIATE: 1, DosingTierLevel.ADVANCED: 2}


@router.get("/library/{peptide_id}")
def library_detail(peptide_id: int, request: Request, session: Session = Depends(get_session)):
    p = _get_peptide(session, peptide_id)
    used_in = session.scalars(
        select(Protocol).join(ProtocolItem)
        .where(ProtocolItem.peptide_id == p.id, Protocol.owner_id == request.state.user.id)
        .order_by(Protocol.start_date.desc()).distinct()).all()
    dosing_tiers = sorted(p.dosing_tiers, key=lambda t: _TIER_ORDER.get(t.level, 99))
    return templates.TemplateResponse(request, "library/detail.html", {
        "p": p, "card": p.card_details or {}, "goals": _goal_map(session).get(p.id, []), "used_in": used_in,
        "dosing_tiers": dosing_tiers,
    })
```

- [ ] **Step 4: Rewrite the template with the new branch**

Replace `app/templates/library/detail.html` in full:

```html
{% extends "base.html" %}
{% set active_nav = "library" %}
{% block title %}{{ p.name }}{% endblock %}

{% macro bullets(items) %}{% if items %}<ul class="bullets">{% for i in items %}<li>{{ i }}</li>{% endfor %}</ul>{% endif %}{% endmacro %}

{% set is_sheet = p.source.value == "sheet" %}

{% block content %}
<div class="page-head lib-head">
  <div>
    <p class="small"><a href="/library">← Library</a></p>
    <h1>{{ p.name }}</h1>
    {% if not is_sheet and card.subtitle %}<p class="muted">{{ card.subtitle }}</p>{% endif %}
    <div class="tags lib-tags">
      {% if is_sheet %}
        {% for tag in p.tags or [] %}
        {% set style = tag | tag_style %}
        <span class="tag {{ 'tag-goal' if style else 'tag-plain' }}" {% if style %}style="{{ style }}"{% endif %}>{{ tag }}</span>
        {% endfor %}
      {% else %}
        {% if p.card_class %}<span class="tag">{{ p.card_class }}</span>{% endif %}
        {% if p.category %}<span class="tag">{{ p.category }}</span>{% endif %}
        {% if p.evidence_level %}<span class="ev-tag {{ p.evidence_level | evidence_class }}">Evidence: {{ p.evidence_level }}</span>{% endif %}
        {% if p.status %}<span class="tag tag-plain">{{ p.status }}</span>{% endif %}
      {% endif %}
    </div>
    {% if goals %}
    <div class="tags lib-tags">
      {% for slug in goals %}<span class="tag tag-goal" style="background: var(--goal-{{ slug }}); color: #fff;">{{ slug | goal_label }}</span>{% endfor %}
    </div>
    {% endif %}
  </div>
</div>

<div class="lib-layout">
  <div class="lib-main">
    {% if is_sheet %}
      {% if p.summary %}<p class="lead">{{ p.summary }}</p>{% endif %}

      {% if p.half_life_text or p.route_summary or p.cycle %}
      <section class="lib-section">
        <dl class="kv kv-tight quick-facts">
          {% if p.half_life_text %}<dt>Half-life</dt><dd>{{ p.half_life_text }}</dd>{% endif %}
          {% if p.route_summary %}<dt>Route</dt><dd>{{ p.route_summary }}</dd>{% endif %}
          {% if p.cycle %}<dt>Cycle</dt><dd>{{ p.cycle.on_weeks }}w on / {{ p.cycle.off_weeks }}w off</dd>{% endif %}
        </dl>
      </section>
      {% endif %}

      {% if dosing_tiers %}
      <section class="lib-section">
        <h2 class="section-title">Dosing (community)</h2>
        <table class="lib-table">
          <thead><tr><th>Level</th><th>Dose</th><th>Frequency</th><th>Time of day</th></tr></thead>
          <tbody>
            {% for t in dosing_tiers %}
            <tr><td>{{ t.level.value }}</td><td>{{ t.dose_text or '—' }}</td><td>{{ t.frequency_text or '—' }}</td>
                <td>{{ t.time_of_day.value if t.time_of_day else '—' }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </section>
      {% endif %}

      {% if p.cycle and p.cycle.note %}
      <section class="lib-section">
        <h2 class="section-title">Cycle notes</h2>
        <p>{{ p.cycle.note }}</p>
      </section>
      {% endif %}

      {% if p.stack_relations %}
      <section class="lib-section two-col">
        <div>
          <h2 class="section-title">Works with</h2>
          {% for r in p.stack_relations if r.relation.value == "works_with" %}
          <p><strong>{{ r.partner_name }}</strong>{% if r.note %} — {{ r.note }}{% endif %}</p>
          {% endfor %}
        </div>
        <div>
          <h2 class="section-title">Avoid</h2>
          {% for r in p.stack_relations if r.relation.value == "avoid" %}
          <p><strong>{{ r.partner_name }}</strong>{% if r.note %} — {{ r.note }}{% endif %}</p>
          {% endfor %}
        </div>
      </section>
      {% endif %}

      {% if p.monitoring_tests %}
      <section class="lib-section">
        <h2 class="section-title">Recommended monitoring</h2>
        <table class="lib-table">
          <thead><tr><th>Test</th><th>When</th><th>Why</th><th>Target</th></tr></thead>
          <tbody>
            {% for m in p.monitoring_tests %}
            <tr><td>{{ m.test_name }}</td><td>{{ m.when_text or '—' }}</td><td>{{ m.why_text or '—' }}</td>
                <td>{{ m.target_text or '—' }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </section>
      {% endif %}

      {% set simple = p.sheet_sections_simple or {} %}
      {% set narrative_order = [
        ("what_is", "What is it?"), ("how_it_works", "How it works"), ("benefits", "Benefits"),
        ("side_effects", "Side effects"), ("contraindications", "Contraindications"),
        ("drug_interactions", "Drug interactions"), ("legal", "Legal status"),
        ("who_should_consider", "Who should consider this"), ("product_quality", "Product quality"),
      ] %}
      {% set bullet_keys = ("benefits", "contraindications", "drug_interactions", "who_should_consider") %}
      {% for key, title in narrative_order if simple.get(key) %}
      <section class="lib-section">
        <h2 class="section-title">{{ title }}</h2>
        {% if key in bullet_keys %}
          {{ bullets(simple[key].split("\n")) }}
        {% else %}
          <p>{{ simple[key] }}</p>
        {% endif %}
      </section>
      {% endfor %}

      {% if p.storage_before_text or p.storage_after_text or p.storage_temperature_text %}
      <section class="lib-section">
        <h2 class="section-title">Storage</h2>
        <dl class="kv">
          {% if p.storage_before_text %}<dt>Before reconstitution</dt><dd>{{ p.storage_before_text }}</dd>{% endif %}
          {% if p.storage_after_text %}<dt>After reconstitution</dt><dd>{{ p.storage_after_text }}</dd>{% endif %}
          {% if p.storage_temperature_text %}<dt>Temperature</dt><dd>{{ p.storage_temperature_text }}</dd>{% endif %}
        </dl>
      </section>
      {% endif %}
      <p class="small muted">From an imported reference sheet, simplified for readability. Informational only — not a recommendation.</p>

    {% elif not card %}
    <div class="empty"><p><strong>No card imported for this peptide.</strong></p>
      <p class="muted">Peptides added by you or from the starter list have no card. Your own info is on the right.</p></div>
    {% else %}
    {% if card.applications %}
    <section class="lib-section">
      <h2 class="section-title">Key applications</h2>
      <div class="app-grid">
        {% for a in card.applications %}<div class="app-tile"><strong>{{ a.title }}</strong><span class="small muted">{{ a.detail }}</span></div>{% endfor %}
      </div>
    </section>
    {% endif %}

    <section class="lib-section">
      <h2 class="section-title">How it works</h2>
      {% if card.mechanism_flow %}
      <ol class="flow">{% for step in card.mechanism_flow %}<li>{{ step }}</li>{% endfor %}</ol>
      {% endif %}
      {{ bullets(card.mechanism) }}
      {% if card.clinical_use_note %}<p class="note">{{ card.clinical_use_note }}</p>{% endif %}
    </section>

    <section class="lib-section two-col">
      <div>
        <h2 class="section-title">Evidence {% if card.evidence and card.evidence.level %}<span class="ev-tag {{ p.evidence_level | evidence_class }}">{{ card.evidence.level | title }}</span>{% endif %}</h2>
        {{ bullets(card.evidence.points if card.evidence else []) }}
      </div>
      <div>
        <h2 class="section-title">Clinical caution</h2>
        {{ bullets(card.cautions) }}
      </div>
    </section>

    {% if card.quick_info %}
    <section class="lib-section">
      <h2 class="section-title">Quick practical info</h2>
      <dl class="kv">{% for k, v in card.quick_info.items() %}<dt>{{ k }}</dt><dd>{{ v or '—' }}</dd>{% endfor %}</dl>
    </section>
    {% endif %}

    {% if card.regulatory %}
    <section class="lib-section">
      <h2 class="section-title">Regulatory status</h2>
      <dl class="kv">{% for k, v in card.regulatory.items() %}<dt>{{ k }}</dt><dd>{{ v or '—' }}</dd>{% endfor %}</dl>
    </section>
    {% endif %}

    {% if card.quick_read %}
    <section class="lib-section">
      <h2 class="section-title">Clinical read in 10 seconds</h2>
      {{ bullets(card.quick_read) }}
    </section>
    {% endif %}

    {% if card.references %}
    <section class="lib-section">
      <h2 class="section-title">Key references</h2>
      {{ bullets(card.references) }}
    </section>
    {% endif %}
    <p class="small muted">From your peptide card. Informational only — not a recommendation.</p>
    {% endif %}
  </div>

  <aside class="lib-side">
    {% if not is_sheet and p.card_image %}
    <a class="card-thumb" href="/library/{{ p.id }}/card" target="_blank" rel="noopener" title="Open the full card">
      <img src="/library/{{ p.id }}/card" alt="{{ p.name }} card" loading="lazy">
      <span class="small">Open full card ↗</span>
    </a>
    {% endif %}

    <div class="side-box">
      <div class="side-head"><h2 class="section-title">Your info</h2><a class="btn btn-ghost" href="/library/{{ p.id }}/edit">Edit</a></div>
      <dl class="kv kv-tight">
        <dt>Dose range</dt>
        <dd>{% if p.dose_low is not none or p.dose_mid is not none or p.dose_high is not none %}
          {{ [p.dose_low, p.dose_mid, p.dose_high] | reject('none') | map('dose_num') | join(' – ') }} {{ p.dose_unit.value if p.dose_unit else '' }}
          {% else %}<span class="muted">Not set</span>{% endif %}</dd>
        <dt>Frequency</dt><dd>{{ p.typical_frequency or '—' }}</dd>
        <dt>Aliases</dt><dd>{{ p.aliases or '—' }}</dd>
        <dt>Notes</dt><dd class="pre">{{ p.notes or '—' }}</dd>
      </dl>
    </div>

    <div class="side-box">
      <h2 class="section-title">Used in protocols</h2>
      {% if used_in %}
      <ul class="plain-list">{% for proto in used_in %}<li><a href="/protocols/{{ proto.id }}/edit">{{ proto.name }}</a> <span class="small muted">{{ proto.start_date | shortdate }}</span></li>{% endfor %}</ul>
      {% else %}<p class="muted small">Not in any protocol yet.</p>{% endif %}
    </div>
  </aside>
</div>
{% endblock %}
```

Note what changed from today's template: the `{% if p.card_number %}Card {{
p.card_number }}{% endif %}` badge next to `<h1>` is gone entirely (Global
Constraint); the goal-stack chips move from a plain-text "In goal stacks: ..."
line into colored `tag-goal` chips using the same per-goal colors; and the
whole `is_sheet` branch is new, sitting alongside the untouched `{% elif not
card %}` / `{% else %}` branches from today's template.

- [ ] **Step 5: Add the small amount of new CSS these sections need**

Modify `app/static/css/app.css` — add near the existing `.kv` / `.lib-section`
rules:

```css
.lib-table { width: 100%; border-collapse: collapse; }
.lib-table th, .lib-table td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
.lead { font-size: 1.05rem; }
.quick-facts { display: flex; gap: 24px; flex-wrap: wrap; }
.quick-facts dt { font-weight: 600; }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `py -m pytest tests/test_library.py -v`
Expected: All PASS, including every pre-existing test in the file (card-sourced
and empty-state behavior unchanged).

- [ ] **Step 7: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/routers/library.py app/templates/library/detail.html app/static/css/app.css tests/test_library.py
git commit -m "feat: render sheet-imported peptide data on the Library detail page"
```

---

### Task 5: List page — sheet-sourced tiles, alphabetical sort, search

**Files:**
- Modify: `app/routers/library.py:54-61` (the `library_list` route)
- Modify: `app/templates/library/list.html`
- Test: `tests/test_library.py`

**Interfaces:**
- Consumes: `p.tags | tag_style` (Task 3).
- Produces: nothing consumed by a later task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_library.py`:

```python
def test_list_sorts_alphabetically_case_insensitively(client, db):
    p1 = Peptide(name="zzz-test-first", source=PeptideSource.CUSTOM)
    p2 = Peptide(name="AAA-test-second", source=PeptideSource.CUSTOM)
    db.add_all([p1, p2])
    db.commit()
    resp = client.get("/library")
    body = resp.text
    assert body.index("AAA-test-second") < body.index("zzz-test-first")


def test_list_no_card_number_badge_anywhere(client, db):
    resp = client.get("/library")
    assert "Card #" not in resp.text and "lib-num" not in resp.text


def test_list_sheet_sourced_tile_shows_tags_not_no_card(client, db):
    p = _sheet_test_peptide(db)
    resp = client.get("/library")
    body = resp.text
    tile_start = body.index(f'href="/library/{p.id}"')
    tile_end = body.index("</a>", tile_start)
    tile = body[tile_start:tile_end]
    assert "Tissue Repair" in tile
    assert "No card" not in tile


def test_list_card_sourced_tile_unchanged(client, db):
    p = with_card(db)
    resp = client.get("/library")
    body = resp.text
    tile_start = body.index(f'href="/library/{p.id}"')
    tile_end = body.index("</a>", tile_start)
    tile = body[tile_start:tile_end]
    assert p.card_class in tile
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_library.py -k "list_sorts or list_no_card_number or list_sheet_sourced_tile" -v`
Expected: FAIL — sort test fails because today's order is by `card_number`
first; the badge test fails because `lib-num`/"Card #" still renders; the
sheet-tile test fails because the tile still says "No card".

- [ ] **Step 3: Update the route's sort**

Modify `app/routers/library.py`:

```python
@router.get("/library")
def library_list(request: Request, session: Session = Depends(get_session)):
    peptides = session.scalars(select(Peptide).order_by(Peptide.name)).all()
    return templates.TemplateResponse(request, "library/list.html", {
        "peptides": peptides, "goals": GOALS, "goal_map": _goal_map(session),
        "added_sources": {PeptideSource.STARTER, PeptideSource.CUSTOM},
    })
```

(`Peptide.name` already has `collation="NOCASE"` at the column level, so a
plain `.order_by(Peptide.name)` is already case-insensitive — no extra
`func.lower(...)` needed.)

- [ ] **Step 4: Update the template**

Replace `app/templates/library/list.html` in full:

```html
{% extends "base.html" %}
{% set active_nav = "library" %}
{% block title %}Library{% endblock %}

{% block content %}
<div class="page-head">
  <div>
    <h1>Library</h1>
    <p class="muted"><span id="lib-count">{{ peptides | length }}</span> peptides</p>
  </div>
</div>

<div class="lib-toolbar">
  <input id="lib-search" type="search" placeholder="Search name, alias, class, category or tag" autocomplete="off" aria-label="Search the library">
  <div class="chips" role="group" aria-label="Filter">
    <button type="button" class="chip" data-filter="all" aria-pressed="true">All</button>
    {% for g in goals %}
    <button type="button" class="chip" data-filter="{{ g.slug }}" aria-pressed="false">{{ g.label }}</button>
    {% endfor %}
    <button type="button" class="chip" data-filter="added" aria-pressed="false">Added by me</button>
  </div>
</div>

<div class="lib-grid" id="lib-grid">
  {% for p in peptides %}
  {% set is_sheet = p.source.value == "sheet" %}
  {% set search = [p.name, p.aliases or '', p.card_class or '', p.category or '', (p.tags or []) | join(' ')] | join(' ') | lower %}
  <a class="lib-tile" href="/library/{{ p.id }}"
     data-search="{{ search }}"
     data-goals="{{ goal_map.get(p.id, []) | join(' ') }}"
     data-added="{{ '1' if p.source in added_sources else '0' }}">
    <strong>{{ p.name }}</strong>
    {% if is_sheet %}
      <span class="tags lib-tags">
        {% for tag in (p.tags or [])[:3] %}
        {% set style = tag | tag_style %}
        <span class="tag small {{ 'tag-goal' if style else 'tag-plain' }}" {% if style %}style="{{ style }}"{% endif %}>{{ tag }}</span>
        {% endfor %}
      </span>
    {% elif p.card_class %}<span class="small muted">{{ p.card_class }}</span>
    {% else %}<span class="small muted">No card</span>{% endif %}
    {% if p.evidence_level %}<span class="ev-tag {{ p.evidence_level | evidence_class }}">{{ p.evidence_level }}</span>{% endif %}
  </a>
  {% endfor %}
</div>
<p class="muted" id="lib-empty" hidden>No peptides match.</p>
{% endblock %}

{% block scripts %}
<script src="{{ static_url('js/library.js') }}" defer></script>
{% endblock %}
```

(The `<span class="lib-num">...#N</span>` line from today's template — the
per-tile "Card N" badge — is removed entirely, per the Global Constraint.
`app/static/js/library.js` needs no change: it already just reads whatever
the `data-search` attribute contains.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_library.py -v`
Expected: All PASS.

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/routers/library.py app/templates/library/list.html tests/test_library.py
git commit -m "feat: show tag chips on sheet-sourced Library tiles, sort alphabetically"
```

---

### Task 6: Protocols page — recolor Goal Cards

**Files:**
- Modify: `app/templates/protocols/list.html`
- Modify: `app/static/css/app.css`
- Test: `tests/test_protocols.py`

**Interfaces:**
- Consumes: `Goal.color` (Task 1).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_protocols.py` (open the file first to match its existing
`client` fixture usage style):

```python
def test_goal_cards_use_their_own_color(client):
    resp = client.get("/protocols")
    body = resp.text
    assert "var(--goal-fat-loss)" in body
    assert "var(--goal-sleep-recovery)" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_protocols.py -k goal_cards_use_their_own_color -v`
Expected: FAIL (neither string appears in today's response).

- [ ] **Step 3: Update the template**

Modify `app/templates/protocols/list.html` — find the existing goal-card loop
(`<button type="button" class="goal-card" data-goal="{{ g.slug }}" ...>`) and
add an inline custom-property style:

```html
<button type="button" class="goal-card" data-goal="{{ g.slug }}" aria-pressed="false"
        style="--goal-color: var(--goal-{{ g.color }});">
  <span class="goal-check" aria-hidden="true">✓</span>
  <strong>{{ g.label }}</strong>
  <span class="small muted">{{ g.description }}</span>
</button>
```

- [ ] **Step 4: Update the CSS to use the new `--goal-color` variable**

Modify `app/static/css/app.css` — change the existing `.goal-card:hover` and
`.goal-card[aria-pressed="true"]` rules (search for `.goal-card:hover`) from:

```css
.goal-card:hover { border-color: var(--accent); }
.goal-card[aria-pressed="true"] { border-color: var(--accent); background: color-mix(in srgb, var(--accent-soft) 45%, var(--surface)); }
```

to:

```css
.goal-card:hover { border-color: var(--goal-color, var(--accent)); }
.goal-card[aria-pressed="true"] { border-color: var(--goal-color, var(--accent)); background: color-mix(in srgb, var(--goal-color, var(--accent-soft)) 25%, var(--surface)); }
.goal-card[aria-pressed="true"] .goal-check { background: var(--goal-color, var(--accent)); }
```

(The `var(--goal-color, var(--accent))` fallback means any other place
`.goal-card` might be reused without the inline style keeps today's plain
accent-colored behavior — nothing else in the codebase renders a `.goal-card`
outside this one template, but the fallback costs nothing and avoids a silent
visual break if that ever changes.)

- [ ] **Step 5: Run test to verify it passes**

Run: `py -m pytest tests/test_protocols.py -k goal_cards_use_their_own_color -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite**

Run: `py -m pytest -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/templates/protocols/list.html app/static/css/app.css tests/test_protocols.py
git commit -m "feat: recolor Protocols Goal Cards to match each goal's own color"
```

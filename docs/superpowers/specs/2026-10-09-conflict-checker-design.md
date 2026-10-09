# Dose-level conflict checker: design

Date: 2026-10-09. Status: awaiting the owner's review.

## Purpose

Amide warns the person, using the doses and schedules they have actually entered, when something looks worth asking a prescriber or pharmacist about. Today the only check is by name: a peptide that matches a listed medicine gets caution tape (`app/library/interactions.py`). This adds four dose- and schedule-aware checks and a report.

The warnings are informational. A finding means "worth asking about", never "safe" or "unsafe", and an empty report does not mean there is no interaction. Nothing is ever blocked: saving a protocol always works.

## Scope (the owner chose all four)

1. **Dose vs library range.** A dose above the peptide's high dose, or far below its low dose, and a titration step that more than doubles the previous step. Uses the library's `dose_low`, `dose_mid`, `dose_high`, `dose_unit` and the protocol's titration steps.
2. **Peptide vs peptide.** Two peptides of the same class in one protocol (two GLP-1s, two growth-hormone secretagogues, and similar), plus the pairs already named in each library card's Works-with and interaction notes where they are structured.
3. **Dose-sensitive medicine interactions.** The Medicines list (Settings) gains an optional free-text dose field, for example "10 mg daily". Cautions that matter more at higher peptide doses, such as diabetes medicine with a GLP-1 above its starting dose, say so.
4. **Timing.** A sedating peptide scheduled in the same time of day as a listed sedative, and doses stacked in the same time-of-day slot that a rule says to separate.

## Design

### The checker

A pure module `app/library/conflicts.py` (no database, no web), next to `interactions.py`. Input: a protocol described as plain data (its items with peptide, dose, unit, time of day, titration steps) and the person's medicines (name, optional dose text). Output: a list of findings.

A finding has: `severity` ("note" or "caution"), `check` (one of `dose`, `stack`, `medicine`, `timing`), `peptide` (and `other` when two items are involved), `message` (plain English, one or two sentences), and `peptide_id` for the link to the library page. Every message is written in the project's own words; no vendor data is involved. The report always ends with: "Worth asking your prescriber or pharmacist. Informational only, not medical advice."

Rules live in code as short tuples, like the medicine rules, so each can be tested and reviewed. Class lists are small and conservative.

### Where the report shows

- **Protocol screen:** an alerts icon beside the print icon on each protocol row (and in the same place on the user's own protocols). The icon carries a count badge when there are findings and is plain when there are none. It opens the report for that protocol in a dialog (same pattern as the other dialogs), with each finding linked to the peptide's library page.
- **Protocol builder:** the same report as a panel before saving, refreshed when the items change. It never blocks the Save button.
- **Protocol list:** the existing caution tape stays.
- **Report URL:** `GET /protocols/{id}/alerts` returns the report as JSON for the dialog; the full page is also reachable at the same address for printing.

### Medicines dose field

`UserMedicine` gains a nullable `dose_text` (String 80) through a new Alembic migration. It is optional, shown in the Medicines list, and only used to word a finding ("with insulin, 10 mg daily"). Rules never depend on parsing it; the rule fires on the medicine name, and the dose text is quoted back to the person.

### Honest limits

- The rules are short on purpose. Most peptides have little interaction research, and the report says so.
- The dose-range check can only compare with the library's reference range. If a peptide has no range, the check says "no library range to compare with" as a note, not a caution.
- Timing is by time-of-day slot (the nine named slots), not clock time.

### Testing

Each of the four checks gets a known-positive and a known-negative test, so a silently broken rule cannot pass (the lesson from the vendor scanner). Further tests: severity ordering, no findings for an empty or single-item protocol, the dose text quoted but never parsed, the alerts icon showing the count and opening the report, the builder panel not blocking Save, and the endpoint refusing another person's protocol.

## Out of scope

Blocking saves, dosing recommendations, any outside lookup or AI call, and reading interaction data from free-text library sheets (too unreliable for warnings).

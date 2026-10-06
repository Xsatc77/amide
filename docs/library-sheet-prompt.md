# Prompt for writing library sheets with an AI platform

Use with `docs/library-sheet-template.txt`. One compound per conversation. Use a platform with live web search and
citations (a "deep research" mode if it has one). Fill the four `{{...}}` inputs, then paste the template at the end.

---

## Prompt (copy from here)

You are a careful research editor writing one plain-language reference sheet for a personal, informational peptide
library. This is not medical advice and must never read like it.

INPUTS
- Compound: {{NAME}}
- Kind: {{peptide | blend of peptides | anabolic steroid or hormone | biologic | other}}
- Other names I know (use them for "Also known as" and verify them): {{ALIASES}}
- Library names (the ONLY names allowed for "Works with", "Avoid" and "Related Peptides"): {{LIBRARY_NAMES, comma separated}}

STEP 1: IDENTIFY. Confirm what this compound is from at least two independent authoritative sources. If the name is
ambiguous, if you cannot confirm it exists, or if sources disagree about what it is, output ONLY the line
"NEEDS HUMAN REVIEW: <reason>" and stop. Do not guess.

STEP 2: RESEARCH, in this order of trust.
 A. Regulators and official labels: FDA, DailyMed, DEA scheduling, EMA, Health Canada, TGA.
 B. Peer-reviewed work and trial registries: PubMed, PMC, ClinicalTrials.gov, Cochrane.
 C. Institutional or clinician-written explainers (hospital, university, pharmacopeia).
 D. Community reports (forums, user experience threads). Allowed ONLY to describe what people commonly report, and every
    such statement must say "community-reported".
 Vendor and marketing pages are never evidence of benefit, dose, safety or legality.

STEP 3: WRITE the sheet in the template below, following these rules.
 1. NEVER invent anything. This covers doses, half-lives, trial results, legal schedules, citations and URLs. If you
    cannot verify a value, write "Not established" (for short fields) or leave that line out (for list items). A shorter
    true sheet is correct; a complete-looking guess is a failure.
 2. Mark the strength of each claim in the words themselves, for example "Approved label:", "Human trial:", "Animal
    study only:", "Community-reported:". Never present animal or community data as proven in people.
 3. Dosing (Beginner, Intermediate, Advanced): use low, typical and high values of ranges reported in approved labels or
    published human trials. If an approved label gives ONE fixed dose, put that dose in all three tiers and add "labeled
    dose" after it. If only community sources exist, write the value and add "community-reported, not clinically
    established". If no human data exists at all, write "Not established" in all three. Never write "recommended".
 3b. Read the full primary document, not a summary: for an approved drug, the full prescribing information, including its
    clinical pharmacology section (half-life, time to peak, bioavailability) and its storage section. "Not established"
    is allowed for those fields only after you have read that section and the value is truly absent from it.
 4. Safety: always include documented serious risks even when rare. Never minimise. No promotional wording, no cure
    claims, no "safe" without the evidence tier.
 5. Legal: state United States status first, checked against FDA or DEA sources, and name the schedule for any
    controlled substance. Add the month and year you checked, in the sentence.
 6. Blends: describe every component and its amount. Evidence belongs to the components; say plainly when no data on the
    blend itself exists.
 7. Plain language: about an eighth-grade reading level, short sentences, no jargon without an immediate plain
    explanation. Paraphrase sources; never copy sentences.
 8. Stack partners come only from the Library names input. If none fit, omit the Stacking section.
 9. Citations: 5 to 12 real sources you actually opened. Give author or organisation, title, source, year and URL. A
    citation you cannot open and confirm must not appear.
10. If key information is older than five years in a fast-moving area, say so in the sentence.

STEP 4: SELF-AUDIT before answering. Silently check and correct: every number traced to a source; every URL opened and
matching its claim; nothing invented; no recommendations; template headers exact; list sections one item per line;
partner names from the Library names input only.

OUTPUT, in exactly three parts and nothing else:
PART A, "VERIFICATION LOG": a table with columns Claim or field | Source title | URL | Tier (A regulator, B peer-reviewed,
C institutional, D community). One row per dose, half-life, legal statement, safety statement and benefit. Maximum 30 rows.
PART B, "SHEET": the finished file inside ONE plain-text code block. Follow the template exactly: same header lines in
the same order, header text spelled exactly as given, no markdown (no #, *, bold, bullets or numbering), no commentary.
Under Recommended Monitoring, the FIRST line must be exactly: TEST | WHEN | WHY | TARGET. Then one test per line with
" | " between columns. Leave a whole section out only when you have no verified content for it; these
are required: name, Also known as, summary, What Is, How It Works, Benefits, Side Effects, Contraindications, Legal,
Citations. No line other than a header may equal a header text.
PART C, "OPEN QUESTIONS": a short list of what you could not verify, every field you set to "Not established", any naming
ambiguity, and your yes or no on each self-audit item.

TEMPLATE
(paste the contents of library-sheet-template.txt here)

---

## Second pass: cross-check with a different platform (paste the sheet in)

You are an adversarial fact-checker. Below is a reference sheet for {{NAME}}. For every dose, half-life, legal statement,
safety statement, benefit and citation, search independently and report: CONFIRMED, WRONG (give the correct value and
source), or UNVERIFIABLE. Open every URL and say whether it exists and supports the sentence it is attached to. Flag any
claim stated more strongly than its evidence, and any missing serious risk. Output a table, then a list of required
corrections. Do not rewrite the sheet.

## Your own spot check (two minutes per sheet)
Dose ranges, half-life, the legal sentence, and the first three side effects. If any of these is wrong, rerun the whole
sheet; do not patch it.

# Peptide Library & Card Import — Design

Date: 2026-09-22 · Status: approved by owner in chat (approach A)

## 1. Intent

Bring the owner's 100 peptide cards (PDF) into Amide as a browsable, searchable **Library**: each peptide's card details as readable text next to the original card image, plus the owner's own fields (Low/Mid/High dose, frequency, aliases, notes, goal stacks) that they edit. Card content is the owner's private copy: it lives in `data/` and is never committed.

### Decisions made with the owner

| Topic | Decision |
| --- | --- |
| View | Text details **and** card image per peptide; searchable list |
| Editing | Owner fields editable (aliases, Low/Mid/High + unit, typical frequency, notes, goal stacks); card fields read-only |
| Extraction | Approach A: position-based text extraction by a one-off script; card images rendered alongside; spot-checked against images |
| Dependency | PDF library (PyMuPDF, AGPL) used **only** by `tools/import_cards.py` via `tools/requirements.txt`; the app gains no dependency |
| Storage | `data/library/cards.json` + `data/library/cards/NNN.webp` (private, backed up with `data/`) |
| Re-import | Script can be rerun on a new PDF; `python -m app.library_load` reloads the DB from `cards.json` without the PDF |
| Ads | Pages without a card (10, 45, 80) skipped |

## 2. Data (migration `0004`)

New nullable columns on `peptides`:

| column | type | notes |
| --- | --- | --- |
| `card_class` | str(200) | e.g. "Cytoprotective peptide" |
| `category` | str(200) | card's clinical category |
| `evidence_level` | str(100) | e.g. "Low / experimental" |
| `status` | str(200) | card's overall status |
| `card_details` | JSON | all other sections (§3) |
| `card_image` | str(100) | filename in `data/library/cards/` |

Owner fields (`aliases`, `dose_low/mid/high`, `dose_unit`, `typical_frequency`, `notes`) already exist and are **never touched by an import**. Goal stacks (`goal_peptides`) are owner data too.

## 3. `cards.json` format

A list, one object per card:

```json
{
  "card_number": 2,
  "name": "BPC-157",
  "subtitle": "Synthetic pentadecapeptide derived from a gastric protein",
  "class": "Cytoprotective peptide",
  "category": "Tissue repair / gastrointestinal",
  "evidence_level": "Low / experimental",
  "status": "Not approved — investigational use",
  "applications": [{"title": "Tissue healing", "detail": "Experimental models"}],
  "mechanism_flow": ["BPC-157", "Repair signaling", "Angiogenesis and fibroblasts", "Tissue repair"],
  "mechanism": ["Cytoprotective action described in experimental models.", "..."],
  "clinical_use_note": "Established clinical use: not defined. Human data are limited; use restricted to research.",
  "evidence": {"level": "LOW EXPERIMENTAL", "points": ["Mostly preclinical data.", "..."]},
  "cautions": ["Long-term safety not established.", "..."],
  "quick_info": {"Half-life": "...", "Routes studied": "...", "Pharmacokinetics": "...", "Stability": "...", "Interactions": "...", "Monitoring": "..."},
  "regulatory": {"FDA": "...", "EMA": "...", "ANVISA": "...", "WADA": "...", "Research": "...", "Off-label": "..."},
  "quick_read": ["Peptide associated with tissue repair.", "..."],
  "references": ["Sikiric et al., 2018 — Curr Pharm Des (review)", "..."],
  "image": "002.webp",
  "page": 2
}
```

Missing/empty sections are allowed (empty string / list / object).

## 4. Loader (`app/library/loader.py`)

`load_cards(session, cards: list[dict]) -> LoadReport` — for each card, find the peptide by `card_number`; if its name (case-insensitive) differs from the card name, report a **mismatch** and skip; if no peptide has that number, **create** it (`source=card`). Set the six card columns from the card; leave owner fields untouched. Idempotent. `LoadReport` lists updated, created, mismatched.

`python -m app.library_load [path]` (default `data/library/cards.json`) runs migrations then the loader and prints the report.

## 5. Extractor (`tools/import_cards.py <pdf> [--data-dir DIR]`)

- Skips pages that are not cards; detects card number and title.
- Reads words with coordinates and assigns them to template regions (header boxes, applications tiles, mechanism flow + bullets, clinical-use banner, evidence / caution columns, six quick-info tiles, six regulatory tiles, four quick-read tiles, references); within a region words are ordered by line (y) then x and joined; letter-spaced headings are dropped.
- Renders each card page to WebP (~110 dpi) as `NNN.webp`.
- Writes `cards.json`, then loads it via `app.library.loader` and prints the report.
- Verified by comparing ≥ 10 cards (spread across the deck) to their images before loading the owner's real data.

## 6. Screens

- **Nav:** Library link between Protocols and Dosing.
- **`/library`** — search (name, aliases, class, category; client-side), filter chips: All · each goal · Added by me (`starter` + `custom`); tile grid: card #, name, class, evidence tag; "No card" when none.
- **`/library/{id}`** — header (name, card #, subtitle, class / category / evidence / status tags, goal tags); main column (applications, how it works, clinical use note, evidence, cautions, quick info table, regulatory table, quick read, references); side column (card image → full size in new tab; Your info: Low/Mid/High + unit, frequency, aliases, notes, Edit; Used in protocols).
- **`/library/{id}/edit`** — aliases, dose low/mid/high (> 0, optional; low ≤ mid ≤ high when given), unit, typical frequency, notes, goal checkboxes (tick → appended to that goal's stack; untick → removed). Validation re-render like Inventory.
- **`/library/{id}/card`** — serves the card image from `data/library/cards/` (404 if none).
- **Builder:** each peptide row gets a "View card" link to `/library/{id}` (new tab).
- **API:** `/api/peptides` gains the card columns (`card_class`, `category`, `evidence_level`, `status`, `has_card`).

## 7. Testing

Unit: loader (update, create, mismatch skip, owner fields untouched, idempotent); extractor pure helpers (line grouping/joining, heading filter). Integration: list page (search data, filters, tiles), detail (sections, image link, used-in-protocols), edit (validation, goal membership add/remove, dose ordering), card image route (serves / 404, no path traversal), builder link, API fields. Migration: upgrade from 0003 keeps data; downgrade. Manual: extraction spot-check; desktop + phone layouts.

# Vendor Price List Import — Design

## Context

The owner keeps vendors' price lists (PDFs, scans, one spreadsheet) in a folder outside the repository. A
future **price analyzer** will compare vendors by cost per kit and per vial, so the numbers must be stored in Amide
now, structured and tied to a vendor and a warehouse region. This spec covers only the **import**:
reading the lists, creating the vendors, storing the lines, and adding vial sizes to library cards.
The analyzer's screens, the scanned lists, and the spreadsheet are out of scope (see the end).

The 12 PDFs in the folder share one idea but not one layout. Eleven have a text layer; one is a scan and is
skipped for now. The importer replaces the earlier single-layout parser (`app/library/price_list_parser.py`),
and the sample list that parser was built for must still produce the same library specs.

## Decisions already made (from the conversation)

1. **Pack prices are stored now**, not just reported (owner's answer).
2. **Warehouse region is stored** with each price list (`us` or `china`). China warehouses can be
   cheaper on product but dearer on shipping and carry customs risk (detention or destruction); US
   warehouses run slightly dearer on product, about $10 cheaper on shipping, and deliver more easily. The
   analyzer will need this. **When a list names no warehouse, China is assumed** (owner's rule), and the
   record says it was assumed so a stated China and a guessed one can be told apart.
3. **Scans are skipped** for now (a scanned PDF, the image lists). Spreadsheets are also out of scope.
4. **Importing a price list creates its vendor.** The vendor name is the first segment of the filename.
   An existing vendor is reused when the names match ignoring case and the generic words "Peptide" /
   "Peptides" (so a file from `Zephyr` reuses an existing `Zephyr Peptides`, and `Northwind` reuses `Northwind`).
5. **File naming convention:** `<Vendor> - [<Warehouse> ]Price List[ NEW] - <YYYY-MM-DD>[-NN].<ext>`. The
   vendor is always first and the date always last; the warehouse is an optional word (`China`, `USA`)
   inside the middle segment, present in only some filenames.
6. **A kit is always exactly 10 vials; anything smaller is a box** (owner's rule). Oils such as testosterone
   esters are typically a one-vial box (priced "$30/1vial"). What a price buys is called a *pack*: each line
   stores its `pack_size` (the vials the list states, never assumed), its `pack_price`, and a `pack_type` of
   `kit` (10), `box` (fewer than 10), or none (size not stated, or more than 10, which is also flagged
   `unusual-pack-size` for review).
7. **The vendor's shipping wording is kept as written**, not interpreted; the owner will decide later how
   to weigh it.

## Data model (one migration, `0031`)

**`price_lists`** — one row per imported file.

| column | notes |
|---|---|
| `id` | |
| `vendor_name` | text from the filename; kept even if the vendor row is later deleted |
| `vendor_id` | FK `vendors.id`, `ON DELETE SET NULL`, indexed |
| `warehouse` | enum `us` / `china` (not null, default `china`) |
| `warehouse_source` | enum `filename` / `text` / `assumed` / `manual` (not null): how `warehouse` was decided |
| `list_date` | date from the filename |
| `source_filename` | unique; the idempotency key |
| `shipping_note` | text, nullable; the list's shipping/freight/customs lines joined with newlines |
| `currency` | `"USD"` (every list seen is USD) |
| `imported_at` | timestamp |

**`price_list_items`** — one row per product line.

| column | notes |
|---|---|
| `id`, `price_list_id` | FK `price_lists.id`, `ON DELETE CASCADE`, indexed |
| `code` | the vendor's short code (`RT10`), nullable |
| `product_name` | as listed after repair (below), nullable |
| `peptide_id` | FK `peptides.id`, `ON DELETE SET NULL`, nullable: the matched library card |
| `vial_amount`, `vial_unit` | `10`, `mg`; unit text is one of `mg`, `mcg`, `IU`, `ml`, `mg/ml` |
| `pack_size` | integer, nullable (1 for `250mg/ml*1vials`; 10 for `10mg*10vials`) |
| `pack_price` | float, nullable: what the list charges for the pack |
| `pack_type` | text, nullable: `kit` (exactly 10 vials), `box` (fewer than 10), else null |
| `extra_prices` | JSON, nullable: other price columns by their header (`{"10kits+": 173}`, wholesale) |
| `flags` | JSON list, nullable: review flags (below) |

Per-vial cost is `pack_price / pack_size` and is computed by the analyzer, not stored. Lines for
non-peptides (water, acetic acid, supplies) are stored too, with `peptide_id` null.

Models: `PriceList`, `PriceListItem` in `app/models.py`, with a cascade from list to items. Vendor
deletion leaves the price lists (name kept, `vendor_id` null).

## Filename parsing

`parse_filename(name) -> (vendor, warehouse, list_date)`, pure and tested alone.

- Strip the extension (including `.jpg.jpg`) and surrounding spaces; split on ` - `.
- **Vendor** = the first segment, trimmed. **Date** = the final `YYYY-MM-DD`, ignoring a trailing `-NN` page
  suffix. A file with no vendor or no date is skipped and reported, never guessed.
- **Warehouse** = `china` if the middle segments contain `China` or `Chinese`; `us` if they contain `USA`,
  `US` or `United States` (source `filename`); otherwise decided by the first-page text (a line containing
  "warehouse" with one of those words, e.g. "Chinese Warehouse"; source `text`); otherwise **`china`
  with source `assumed`**. The filename wins over the text, and `--warehouse` wins over both (source
  `manual`). Matching is case-insensitive and whole-word.
- `Price List`, `Pricelist`, `price list`, `Price List NEW` are all treated as the same filler.

## Vendor resolution

`vendor_key(name)` = the matcher's `name_key` with the words `peptide` and `peptides` removed. A vendor
exists if its key equals the file vendor's key; the existing vendor is reused untouched. Otherwise a vendor
is created: `name` as written in the filename, `created_by_id` null, `price_list_updated_at` = the list
date (an existing vendor's date is only moved forward, never back). Vendor names are unique ignoring case
in the database, so the lookup is exact and a collision cannot occur. Vendors are shared by every user of
the installation.

## Reading a PDF

A reader, `app/library/price_lists/`, turns a PDF into rows. Most vendors publish a ruled table, so it reads
the table's **cells** (pdfplumber) and infers each column's role from what the cells contain, not from header
words: the spec column holds `10mg*10vials`-style text, price columns are numeric cells to its right, the code
column is the one whose digits usually match the vial size (`RT10` = 10mg), and the name column is the text
column next to the code. A page with no usable table (a plain text list) falls back to reading text lines.

1. **Find rows.** A row is a table row whose spec cell parses: `10mg*10vials`, `10mg x 10 vials`,
   `10mg *10`, `100IU*10vi`, `250mg/ml*1vials` (amount, unit, vials in the pack; "vials" optional or
   truncated; a number after `*` followed by a unit is a concentration, not a vial count). The **code** comes
   from the code column (or the start of a cartridge name, `RT10 (Double Chamber Cartridge)`). The **pack price**
   is the base price column (a column headed MOQ, stock or weight is never a price; with several numeric columns
   the one headed "price" or holding `$` values is the base); a price written `$45`, `45`, `45 USD`, `USD 45`,
   `$30/1vial` or `$30/vial` parses, and a row with none is flagged `no-price`. Further price columns go to
   `extra_prices` under their header text. A list with no code column keeps its names and its rows are flagged
   `no-code`. Text lines that look like products but yielded no row are counted and shown in the report.
2. **Assign names.** A name cell may span several lines (wrapped blend names stay in one cell). Vendors merge a
   name cell across a group, so only one row of the group carries it, at the top or in the middle. A **run**
   is a stretch of consecutive rows with the same code prefix (the letters of `RT10`, `2AD`, `ACU50`), or
   consecutive rows with no code. Rows above a run's first name take that name and rows below its last name
   take it. A row between two *different* names is filled only when the list puts names on the first row of a
   group (it belongs to the name above); otherwise it is left unnamed and flagged `ambiguous-name`, never
   guessed, because two products can share a code prefix (`GR2...`, `GR6...`).
3. **Repair names with the code.** A prefix → name table is learned from the lists themselves (vendors
   agree: `RT` = Retatrutide, `TR` = Tirzepatide, `CD`/`CND` = the two CJC-1295 variants). A name that is
   blank, or that matches no library card while the learned name does ("Retarutide"), is replaced and the
   row flagged `name-from-code`. Repair never crosses a with / without / no / DAC difference ("no DAC" is not
   turned into "with DAC"; "without", "WO" and "whitout" all count as "no"), and a name repaired on an earlier
   import is not counted when learning again. A prefix is learned only from names that agree across at least two
   vendors, and a vendor's own spelling is kept when it already matches a card.
4. **Check the code against the size.** If the code's digits differ from the vial amount (`RT10` listed
   as 20mg), the row is flagged `code-size-mismatch`. It is a flag for review, not an error; blends
   (`BB20` = 10mg + 10mg) and `10K` = 10000 IU are expected to flag.
5. **Shipping.** Lines containing `ship`, `freight`, `customs`, or `postage` become `shipping_note`.

The reader has no knowledge of the database; it returns plain row objects. Spec, price, code and
shipping parsing are small pure functions covered by unit tests.

## Matching to the library

Each row's product name goes through `app/library/matching.py` (exact, normalized, aliases, blend,
single-typo; with/without/DAC kept apart). `peptide_id` is the matched card; when several cards match,
the first by source (`card`, `starter`, `sheet`, `custom`) then id. Rows with a unit of `mg`, `mcg`, or
`IU` also add `"{amount:g}{unit}"` to **every** matched card's `library_specifications` (the existing
union behavior), so the protocol builder keeps working as before. The importer never creates a library
card.

## Import behavior and tool

`tools/import_price_lists.py PATH [PATH ...] [--dry-run] [--warehouse us|china]`

- A path is a PDF or a folder (its `*.pdf` files); other file types are listed as skipped.
- **Idempotent.** A file is keyed by `source_filename`: re-importing replaces that list's items and
  updates its vendor link, warehouse and shipping note instead of duplicating. A newer-dated list from the
  same vendor is a separate list (history).
- `--warehouse` overrides the detected value and is accepted only with a single file.
- `--dry-run` parses and prints the report and writes nothing.
- One transaction per file; a file that fails is reported and does not stop the others.
- **Report**, per file: vendor (new or reused), warehouse (and how it was found), rows read, rows matched
  to a library card, rows flagged, and the flagged and unmatched lines. A final list names every file whose
  warehouse was **assumed**, so the owner can correct any that are really US (`--warehouse us`, re-import).

## Testing

Vendor PDFs are not added to the repository. Unit tests build rows and word boxes by hand:
filename parsing (every form seen: with and without warehouse, `Pricelist`, `NEW`, page suffix, trailing
space), warehouse precedence (manual > filename > text > assumed China), spec forms, price forms, name-run propagation, the code → name repair and its two-vendor rule,
code/size flags, vendor reuse (`Zephyr` → `Zephyr Peptides`) and creation, one shared
vendor for a vendor's China and USA lists, idempotent re-import, vendor deletion leaving the list, the dry run
writing nothing, and the importer never creating a library card. A run against the real folder is shown to
the owner (rows per vendor, unmatched, flagged); the old sample list is re-run to confirm its specs are unchanged.

## Out of scope

The price analyzer's screens and calculations; shipping-cost math; scanned PDFs and image lists (they
need OCR); spreadsheets; currencies other than USD; editing prices in the UI.

## Review Focus

- A name wrapped over three lines (`GHK-CU 50mg + BPC-157 10mg + TB-500 10mg`) must land on one row, not
  split across neighbors.
- A list with no warehouse in its name or text imports as `china` with source `assumed` (not `filename`
  or `text`), and appears in the report's assumed list; a later `--warehouse us` re-import corrects it.
- Re-importing the same file twice leaves exactly one list and one set of items.
- A vendor's China and USA lists must share one vendor and be two lists with different warehouses.
- A row whose price column holds `$30/1vial` stores pack price 30, pack size 1, and type `box`.
- A pack of more than 10 vials is flagged `unusual-pack-size` and typed null, never called a kit.
- A code with no name anywhere (and no learned prefix) imports with a null name and a flag, not a crash.
- `Bacteriostatic water` / `Acetic Acid` lines are stored, matched to no card, and add no library specs.

---

**For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
superpowers:executing-plans to implement the plan built from this spec.

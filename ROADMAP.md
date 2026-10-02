# Amide Roadmap

## Known Limitations

### Library Name Inconsistency
**Status:** Documented issue  
**Severity:** Medium  
**Description:** Vendors and price lists use inconsistent naming conventions for the same peptides, leading to potential duplicate entries in the library:
- Example: AOD-9604 / AOD9604 / AOD 9604 (hyphenated, no-space, space variants)
- These variants can be created during bulk price list imports if not normalized

**Mitigation Applied:**
- Added data consolidation script (`fix_aod9604.py`)
- Consolidated AOD-9604 variants to canonical hyphenated format
- Merged library specifications from all variants

**Future Fix (Roadmap Item):**
- Add preprocessing to price list parser to normalize peptide names:
  - Strip leading/trailing whitespace
  - Normalize spacing (convert spaces to hyphens)
  - Case normalization
- Implement fuzzy matching for import deduplication
- Add validation rules to prevent duplicate entries on insert

**Impact:**
- Could affect protocol items if user searches for variant names
- Minimized by current implementation (typeahead search handles partial matches)
- May create redundant inventory items if not caught during import

---

## Features in Progress

## Completed Features
- ✅ Price list PDF parsing (pdfplumber integration)
- ✅ Library specifications population from price list
- ✅ Protocol Builder typeahead with library specs display
- ✅ Course Totals popup with dynamic vial size calculator

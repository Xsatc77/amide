# tools/

Helper scripts that are **not** part of the running app.

## `import_cards.py` — import your peptide-card PDF into the Library

Needs PyMuPDF, which only this tool uses (the Amide app itself does not depend on it):

```bash
pip install -r tools/requirements.txt
python tools/import_cards.py "path/to/Peptide Cards.pdf"
```

- Reads each card page (pages without a card, e.g. adverts, are skipped).
- Writes `data/library/cards.json` and one image per card in `data/library/cards/`.
- Loads the card details into the database, matched to the library by card number (the name must match too;
  mismatches are reported and skipped). Your own doses, notes, aliases and goal stacks are never changed.

Options: `--data-dir DIR` (another Amide data folder), `--no-load` (files only), `--show N` (print card N's
extracted text and stop — handy for checking a new PDF).

To reload the database from an existing `cards.json` (fresh database, another server) no PDF or PyMuPDF is
needed: `python -m app.library_load`.

The card content comes from your PDF and stays in your `data/` folder; it is never committed to git.

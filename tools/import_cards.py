"""Import the owner's peptide-card PDF into Amide's library.

    pip install -r tools/requirements.txt
    python tools/import_cards.py "path/to/Peptide Cards.pdf" [--data-dir DIR] [--no-load] [--show N]

Writes <data dir>/library/cards.json and one JPEG per card in <data dir>/library/cards/, then loads the
cards into the database (card fields only; your own doses, notes, aliases and goal stacks are untouched).

Every card uses the same template, but sections shift a little from card to card. Each section is found
from its heading on the page (e.g. "K E Y  A P P L I C A T I O N S") and read relative to it; inside a
section, text runs are ordered top-to-bottom, then left-to-right, and joined. PyMuPDF is only needed
here, not by the Amide app itself.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

PAGE_RIGHT = 600  # PDF points; cards are A4 portrait (595 x 842)
QUICK_INFO_ROWS = [("Half-life", "Routes studied"), ("Pharmacokinetics", "Stability"), ("Interactions", "Monitoring")]
REGULATORY_KEYS = ["FDA", "EMA", "ANVISA", "WADA", "Research", "Off-label"]

# Headings/labels printed inside sections, compared with spaces removed and upper-cased.
LABELS = {"CLASS", "CLINICALCATEGORY", "EVIDENCELEVEL", "OVERALLSTATUS", "EVIDENCE", "CLINICALCAUTION",
          "HALF-LIFE", "ROUTESSTUDIED", "PHARMACOKINETICS", "STABILITY", "INTERACTIONS", "MONITORING",
          "FDA", "EMA", "ANVISA", "WADA", "RESEARCH", "OFF-LABEL"}


# ---------------------------------------------------------------- pure text helpers (unit-tested)
# A run is (x0, y0, x1, y1, text, bold[, size]).

def group_lines(runs, y_tol: float = 3.0):
    """Group runs into lines by vertical centre, top to bottom; each line left to right."""
    lines: list[list] = []
    for run in sorted(runs, key=lambda r: ((r[1] + r[3]) / 2, r[0])):
        cy = (run[1] + run[3]) / 2
        if lines and abs(cy - lines[-1][0]) <= y_tol:
            lines[-1][1].append(run)
        else:
            lines.append([cy, [run]])
    return [sorted(runs_, key=lambda r: r[0]) for _, runs_ in lines]


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,;:)])", r"\1", text)
    text = re.sub(r"([–-]) (?=[a-z])", r"\1", text)  # word broken at a dash across lines
    return re.sub(r"\(\s+", "(", text)


def line_text(line) -> str:
    return _tidy(" ".join(r[4] for r in line))


def join_lines(lines) -> str:
    return _tidy(" ".join(line_text(line) for line in lines))


def is_spaced_heading(text: str) -> bool:
    """Letter-spaced headings such as 'K E Y A P P L I C A T I O N S'."""
    tokens = text.split()
    # One two-letter token is allowed: adjacent letters sometimes land in the same run ("K E Y R E F").
    return len(tokens) >= 5 and sum(len(t) == 1 for t in tokens) >= len(tokens) - 1


_LETTER_RUN = re.compile(r"^[A-Z0-9&-]( ?[A-Z0-9&-])?$")


def strip_spaced_headings(runs):
    """Drop the letters of letter-spaced headings ("Q U I C K  P R A C T I C A L ..."). Their first
    letters can sit inside a neighbouring section's column, so they are removed before reading."""
    keep = []
    for line in group_lines(runs):
        letters = [r for r in line if _LETTER_RUN.match(r[4])]
        drop = {id(r) for r in letters} if len(letters) >= 5 else set()
        keep += [r for r in line if id(r) not in drop]
    return keep


def is_label(text: str) -> bool:
    return text.replace(" ", "").upper() in LABELS and (text.isupper() or is_spaced_heading(text)
                                                        or text in {"Research", "Off-label"})


def split_bullets(lines: list[str]) -> list[str]:
    """Bullet points: a new bullet starts after a line that ends a sentence."""
    bullets: list[str] = []
    for text in lines:
        # A sentence end may be followed by one stray character the card printed at the line's end.
        if bullets and not re.search(r"[.!?]( \S)?$", bullets[-1]):
            bullets[-1] = _tidy(bullets[-1] + " " + text)
        else:
            bullets.append(text)
    return bullets


def split_references(lines: list[str]) -> list[str]:
    """References: a line starting with a digit, lowercase letter or '(' continues the previous one."""
    refs: list[str] = []
    for text in lines:
        if refs and re.match(r"[0-9a-z(]", text):
            refs[-1] = _tidy(refs[-1] + " " + text)
        else:
            refs.append(text)
    return refs


# ---------------------------------------------------------------- page reading

def page_runs(page):
    """All text runs on a page as (x0, y0, x1, y1, text, bold, size). Flow arrows are kept as '▶'."""
    runs = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = span["text"].strip()
                if text:
                    x0, y0, x1, y1 = span["bbox"]
                    runs.append((x0, y0, x1, y1, text, bool(span["flags"] & 16), span["size"]))
    return runs


def _cy(r) -> float:
    return (r[1] + r[3]) / 2


def inside(runs, box):
    x0, y0, x1, y1 = box
    return [r for r in runs if x0 <= (r[0] + r[2]) / 2 < x1 and y0 <= _cy(r) < y1 and r[4] != "▶"]


def box_lines(runs, box) -> list[str]:
    """Text lines in a box, without headings/labels."""
    out = []
    for line in group_lines(inside(runs, box)):
        text = line_text(line)
        if text and not is_spaced_heading(text) and not is_label(text):
            out.append(text)
    return out


def box_text(runs, box) -> str:
    return _tidy(" ".join(box_lines(runs, box)))


def find(runs, label: str, *, y_min: float = 0, x_min: float = 0):
    """The topmost label run at/below y_min and right of x_min. Labels are printed in capitals (or
    letter-spaced), so ordinary body text that merely starts with the same word never matches."""
    want = label.replace(" ", "").upper()
    hits = [r for r in runs if r[4].replace(" ", "").upper() == want and _cy(r) >= y_min and r[0] >= x_min
            and (r[4].isupper() or r[4] in REGULATORY_KEYS)]
    if not hits:
        raise ValueError(f"label {label!r} not found")
    return min(hits, key=_cy)


def find_heading(runs, label: str, x_min: float = 0) -> float:
    """y of a letter-spaced heading, which may be split into several runs on one line."""
    want = label.replace(" ", "").upper()
    for line in group_lines([r for r in runs if r[0] >= x_min]):
        if "".join(r[4] for r in line).replace(" ", "").upper().startswith(want):
            return _cy(line[0])
    raise ValueError(f"heading {label!r} not found")


def columns(edges: list[float], pad: float = 6):
    """[(x0, x1), ...] between successive left edges; the last column runs to the page edge."""
    xs = [e - pad for e in edges] + [PAGE_RIGHT]
    return list(zip(xs, xs[1:]))


def cluster_starts(xs, gap: float) -> list[float]:
    """Left edges of columns: x positions more than `gap` apart start a new column."""
    starts: list[float] = []
    for x in sorted(xs):
        if not starts or x - starts[-1] > gap:
            starts.append(x)
    return starts


def read_card(page, page_number: int) -> dict | None:
    runs = page_runs(page)
    if "CARD" not in {r[4] for r in runs}:
        return None  # not a card (e.g. an advert page)

    top = [r for r in runs if _cy(r) < 92]  # everything above the four header boxes
    title = max((r for r in top if r[0] > 90), key=lambda r: r[6])
    number = next(int(r[4]) for r in top if r[4].isdigit() and r[0] < 90)
    # Subtitle: the smaller text under the title (it may wrap onto a second line).
    subtitle = join_lines(group_lines([r for r in top if r[0] > 90 and _cy(r) > _cy(title) + 6
                                       and r[6] < title[6] - 4]))

    # Header: four labelled boxes side by side, value under each label.
    labels = {"class": find(runs, "CLASS"), "category": find(runs, "CLINICAL CATEGORY"),
              "evidence_level": find(runs, "EVIDENCE LEVEL"), "status": find(runs, "OVERALL STATUS")}
    order = sorted(labels, key=lambda k: labels[k][0])
    header_y = min(_cy(r) for r in labels.values())
    header = {k: box_text(runs, (x0, header_y - 8, x1, header_y + 45))
              for k, (x0, x1) in zip(order, columns([labels[k][0] for k in order]))}

    apps_y = find_heading(runs, "KEY APPLICATIONS")
    safety_y = find_heading(runs, "EVIDENCE & SAFETY")
    regulatory_y = find_heading(runs, "REGULATORY STATUS")
    read_y = find_heading(runs, "CLINICAL READ")
    refs_y = find_heading(runs, "KEY REFERENCES")
    # "H O W  I T  W O R K S" starts the right-hand column on the same line as KEY APPLICATIONS.
    how_x = min(r[0] for r in runs if abs(_cy(r) - apps_y) < 4 and r[0] > 250)
    # The right-hand column ends at its own next heading, which can sit lower than the left one's.
    quick_y = find_heading(runs, "QUICK PRACTICAL INFO", x_min=how_x - 20)
    runs = strip_spaced_headings(runs)

    # Key applications: tiles of bold title + plain detail. The clinical-use banner sits below them.
    tiles = [r for r in runs if r[0] < how_x - 10 and apps_y + 10 < _cy(r) < apps_y + 92]
    applications = []
    for x0, x1 in columns(cluster_starts([r[0] for r in tiles if r[5]], 45) or [0], pad=12):
        tile = [r for r in tiles if x0 <= (r[0] + r[2]) / 2 < min(x1, how_x - 4)]
        t = join_lines(group_lines([r for r in tile if r[5]]))
        d = join_lines(group_lines([r for r in tile if not r[5]]))
        if t or d:
            applications.append({"title": t, "detail": d})
    clinical_use = box_text(runs, (0, apps_y + 92, how_x - 10, safety_y - 4))

    # How it works: boxes separated by arrows, then bullet points down to the safety heading.
    flow_y1 = apps_y + 80
    arrows = [r[0] for r in runs if r[4] == "▶" and apps_y < _cy(r) < flow_y1]
    flow_edges = [how_x] + sorted(a + 8 for a in arrows)
    flow = [t for t in (box_text(runs, (x0, apps_y + 10, x1, flow_y1)) for x0, x1 in columns(flow_edges)) if t]
    mechanism = split_bullets(box_lines(runs, (how_x - 6, flow_y1, PAGE_RIGHT, quick_y - 4)))

    # Evidence | Clinical caution | Quick practical info (2 x 3 tiles), down to the regulatory heading.
    caution_x = find(runs, "CLINICAL CAUTION", y_min=safety_y)[0]
    routes = find(runs, "ROUTES STUDIED", y_min=safety_y, x_min=caution_x)
    pharma = find(runs, "PHARMACOKINETICS", y_min=safety_y, x_min=caution_x)
    monitoring = find(runs, "MONITORING", y_min=safety_y, x_min=caution_x)
    info_x, right_x = pharma[0], routes[0]
    body_y0, body_y1 = safety_y + 6, regulatory_y - 4

    evidence_lines = box_lines(runs, (0, body_y0, caution_x - 6, body_y1))
    level = []
    while evidence_lines and evidence_lines[0].isupper():
        level.append(evidence_lines.pop(0))
    cautions = split_bullets(box_lines(runs, (caution_x - 6, body_y0, info_x - 8, body_y1)))

    row_tops = [_cy(routes) - 5, _cy(pharma) - 5, _cy(monitoring) - 5, body_y1]
    quick_info = {}
    for (left_key, right_key), y0, y1 in zip(QUICK_INFO_ROWS, row_tops, row_tops[1:]):
        quick_info[left_key] = box_text(runs, (info_x - 8, y0, right_x - 6, y1))
        quick_info[right_key] = box_text(runs, (right_x - 6, y0, PAGE_RIGHT, y1))

    reg_edges = [find(runs, k, y_min=regulatory_y)[0] for k in REGULATORY_KEYS]
    regulatory = {k: box_text(runs, (x0, regulatory_y + 6, x1, read_y - 4))
                  for k, (x0, x1) in zip(REGULATORY_KEYS, columns(reg_edges))}

    read_runs = [r for r in runs if read_y + 6 < _cy(r) < refs_y - 4]
    quick_read = [t for t in (box_text(runs, (x0, read_y + 6, x1, refs_y - 4))
                              for x0, x1 in columns(cluster_starts([r[0] for r in read_runs], 100)))
                  if t]

    # Key references: two columns on the left; the disclaimer box on the right is skipped.
    references = []
    for x0, x1 in ((0, 200), (200, 425)):
        references += split_references(box_lines(runs, (x0, refs_y - 3, x1, 842)))

    return {
        "card_number": number,
        "name": title[4].strip(),
        "subtitle": subtitle,
        **header,
        "applications": applications,
        "mechanism_flow": flow,
        "mechanism": mechanism,
        "clinical_use_note": clinical_use,
        "evidence": {"level": " ".join(level), "points": split_bullets(evidence_lines)},
        "cautions": cautions,
        "quick_info": quick_info,
        "regulatory": regulatory,
        "quick_read": quick_read,
        "references": references,
        "image": f"{number:03d}.jpg",
        "page": page_number,
    }


# ---------------------------------------------------------------- CLI

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--data-dir", type=Path, help="Amide data folder (default: AMIDE_DATA_DIR or ./data)")
    parser.add_argument("--no-load", action="store_true", help="only write cards.json and images")
    parser.add_argument("--show", type=int, metavar="N", help="print card N's extracted JSON and stop")
    args = parser.parse_args()

    import pymupdf  # imported here so the helpers above can be tested without it

    if args.data_dir:
        os.environ["AMIDE_DATA_DIR"] = str(args.data_dir.resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import config  # after AMIDE_DATA_DIR is set

    doc = pymupdf.open(args.pdf)
    cards = []
    for i, page in enumerate(doc):
        card = read_card(page, i + 1)
        if card is None:
            print(f"page {i + 1}: not a card, skipped")
        elif args.show is None:
            cards.append((card, page))
        elif card["card_number"] == args.show:
            print(json.dumps(card, indent=2, ensure_ascii=False))
            return
    if args.show is not None:
        sys.exit(f"card {args.show} not found")

    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    for card, page in cards:
        page.get_pixmap(dpi=110).save(config.CARDS_DIR / card["image"], jpg_quality=82)
    config.CARDS_JSON.write_text(json.dumps([c for c, _ in cards], indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(cards)} cards to {config.CARDS_JSON}")

    if not args.no_load:
        from app.db import SessionLocal
        from app.library.loader import load_cards
        from app.migrate import upgrade_db

        upgrade_db()
        with SessionLocal() as session:
            print(load_cards(session, [c for c, _ in cards]).summary())


if __name__ == "__main__":
    main()

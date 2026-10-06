# app/library/price_lists/ocr.py
"""Read a scanned (picture) price-list page: recognize the words, then rebuild the table from where they sit.

The recognizer only returns words with positions. A price row is anchored on its specification cell
("5mg*10vials"); the code, product name and price that belong to it are found by position, and a product name
centered across several rows is given to the nearest row (the reader's name filling spreads it to the rest)."""

import statistics
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re

_SPEC_WORD = re.compile(r"\d+(?:\.\d+)?\s*(?:mcg|mg|ug|iu|ml)\S*?\s*\d+\s*vials?\b", re.IGNORECASE)
_PRICE_HEADER = re.compile(r"price|usd|cost|\$", re.IGNORECASE)
_SPEC_PARTS = re.compile(r"(\d+(?:\.\d+)?\s*(?:mcg|mg|ug|iu|ml)(?:\s*/\s*ml)?)\s*\S?\s*(\d+)\s*(vials?)", re.IGNORECASE)
_TRAILING_DOLLAR = re.compile(r"^(\d[\d,.]*)\$(/\d*\s*vials?)?$", re.IGNORECASE)
_MAX_PAGES = 30
_RENDER_SCALE = 3  # 3x the PDF's point size: small print stays legible without making huge images
_HEADER = {"code": "Code", "name": "Name", "spec": "Specification", "price": "Price"}


class OcrUnavailable(RuntimeError):
    """The text-recognition engine is not installed."""


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


# ---------------------------------------------------------------- the engine

_engine = None
_engine_lock = threading.Lock()


def engine_available() -> bool:
    try:
        import rapidocr  # noqa: F401
    except ImportError:
        return False
    return True


def recognize(page_image) -> list[Word]:
    """Words, with positions, in a PIL image. The engine loads once; recognition runs one page at a time."""
    global _engine
    try:
        import numpy as np
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise OcrUnavailable("Text recognition (RapidOCR) is not installed.") from exc
    with _engine_lock:
        if _engine is None:
            _engine = RapidOCR()
        result = _engine(np.array(page_image.convert("RGB")))
    if result is None or result.txts is None:
        return []
    words = []
    for box, text in zip(result.boxes, result.txts):
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        if str(text).strip():
            words.append(Word(min(xs), min(ys), max(xs), max(ys), str(text).strip()))
    return words


def page_words(path: Path, index: int, recognizer: Callable[[object], list[Word]] | None = None) -> list[Word]:
    """The words on one page (0-based) of a picture PDF. Pages past the cap are not read."""
    if index >= _MAX_PAGES:
        return []
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        image = pdf[index].render(scale=_RENDER_SCALE).to_pil()
    finally:
        pdf.close()
    return (recognizer or recognize)(image)


# ---------------------------------------------------------------- words to a table

def lines_from_words(words: list[Word]) -> list[str]:
    """Text lines: words whose centers sit at about the same height, left to right."""
    if not words:
        return []
    tolerance = statistics.median(w.height for w in words) * 0.6
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.cy, w.cx)):
        if lines and abs(word.cy - statistics.mean(x.cy for x in lines[-1])) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [" ".join(x.text for x in sorted(line, key=lambda w: w.cx)) for line in lines]


def _clean_spec(text: str) -> str:
    """"50mg+10vials" -> "50mg*10vials": the recognizer sometimes reads the multiplication sign as another mark."""
    m = _SPEC_PARTS.match(text.strip())
    return f"{m.group(1)}*{m.group(2)}{m.group(3)}" if m else text


def _clean_price(text: str) -> str:
    """"88$" -> "$88": the dollar sign is sometimes read after the digits."""
    m = _TRAILING_DOLLAR.match(text.strip())
    return f"${m.group(1)}{m.group(2) or ''}" if m else text


def _nearest(anchors: list[Word], y: float) -> int:
    return min(range(len(anchors)), key=lambda i: abs(anchors[i].cy - y))


def _split_columns(left: list[Word], page_width: float) -> float | None:
    """The x that separates a code column from a name column among words left of the specification column, or None
    when they form one column."""
    xs = sorted({round(w.cx) for w in left})
    gaps = [(b - a, (a + b) / 2) for a, b in zip(xs, xs[1:])]
    if not gaps:
        return None
    gap, middle = max(gaps)
    return middle if gap >= page_width * 0.1 else None


def _join_wrapped_specs(words: list[Word]) -> list[Word]:
    """A specification cell that wraps ("10ml*250mg/ml*" over "2vials") becomes one word."""
    out = list(words)
    for a in sorted(words, key=lambda w: w.cy):
        if a not in out or _SPEC_WORD.search(a.text):
            continue
        for b in out:
            if b is a or not 0 < b.cy - a.cy <= a.height * 1.8 or abs(b.cx - a.cx) > max(a.x1 - a.x0, b.x1 - b.x0):
                continue
            if _SPEC_WORD.search(f"{a.text} {b.text}") and not _SPEC_WORD.search(b.text):
                out.remove(a)
                out.remove(b)
                out.append(Word(min(a.x0, b.x0), a.y0, max(a.x1, b.x1), b.y1, f"{a.text} {b.text}"))
                break
    return out


def _value_columns(words: list[Word]) -> list[list[Word]]:
    """Words right of the specification column grouped into columns by their horizontal centers, left to right."""
    if not words:
        return []
    split = statistics.median(w.x1 - w.x0 for w in words) * 2
    columns: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.cx):
        if columns and word.cx - statistics.mean(x.cx for x in columns[-1]) <= split:
            columns[-1].append(word)
        else:
            columns.append([word])
    return columns


def table_from_words(words: list[Word]) -> list[list[str]] | None:
    """Rows of [code, name, specification, (stock columns,) price] (the code column only when there is one) with a
    header row, or None when the words do not hold at least two priced lines. Columns between the specification and
    the price (warehouse stock counts) are labelled "Stock" so they are never read as prices."""
    words = _join_wrapped_specs(words)
    anchors = sorted((w for w in words if _SPEC_WORD.search(w.text)), key=lambda w: w.cy)
    if len(anchors) < 2:
        return None
    spec_x = statistics.median(a.cx for a in anchors)
    width = statistics.median(a.x1 - a.x0 for a in anchors)
    anchors = [a for a in anchors if abs(a.cx - spec_x) <= width]
    if len(anchors) < 2:
        return None
    pitch = statistics.median(b.cy - a.cy for a, b in zip(anchors, anchors[1:]))
    top, bottom = anchors[0].cy - pitch * 0.6, anchors[-1].cy + pitch * 0.8
    body = [w for w in words if w not in anchors and top <= w.cy <= bottom]
    page_width = max(w.x1 for w in words)
    spec_left = min(a.x0 for a in anchors)

    left = [w for w in body if w.x1 <= spec_left + 1]
    right = [w for w in body if w.x0 >= max(a.x1 for a in anchors) - 1 and w not in left]
    split = _split_columns(left, page_width)
    code_words = [w for w in left if split is not None and w.cx < split]
    name_words = [w for w in left if w not in code_words]

    count = len(anchors)
    codes = [[] for _ in range(count)]
    for word in code_words:
        codes[_nearest(anchors, word.cy)].append(word)

    columns = _value_columns(right)
    headers = [[] for _ in columns]
    if columns:  # words above the first row, under the column they sit over, say what each column holds
        for word in (w for w in words if w.cy < top and w.x0 >= max(a.x1 for a in anchors) - 1):
            headers[min(range(len(columns)), key=lambda i: abs(statistics.mean(c.cx for c in columns[i]) - word.cx))].append(word)
    priced = [i for i, h in enumerate(headers) if _PRICE_HEADER.search(" ".join(w.text for w in h))] or [len(columns) - 1]
    cells = [[[] for _ in range(count)] for _ in columns]
    for k, column in enumerate(columns):
        for word in column:
            cells[k][_nearest(anchors, word.cy)].append(word)

    names = [[] for _ in range(count)]
    for word in name_words:
        names[_nearest(anchors, word.cy)].append(word)

    def text(group: list[Word]) -> str:
        return " ".join(w.text for w in sorted(group, key=lambda w: (round(w.cy / max(pitch * 0.5, 1)), w.cx)))

    with_codes = split is not None
    header = [_HEADER["code"]] if with_codes else []
    header += [_HEADER["name"], _HEADER["spec"]]
    header += [_HEADER["price"] if k in priced else "Stock" for k in range(len(columns))]
    table = [header]
    for i, anchor in enumerate(anchors):
        row = [text(codes[i])] if with_codes else []
        row += [text(names[i]), _clean_spec(anchor.text)]
        row += [_clean_price(text(cells[k][i])) if k in priced else text(cells[k][i]) for k in range(len(columns))]
        table.append(row)
    return table


def rows_from_words(words: list[Word], page_number: int):
    """(text lines, rows) read from the words recognized on one picture page: the table rebuilt from their positions."""
    from app.library.price_lists.reader import rows_from_table
    table = table_from_words(words)
    return lines_from_words(words), (rows_from_table(table, page_number) if table else [])

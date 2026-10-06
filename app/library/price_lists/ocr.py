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

_SPEC_WORD = re.compile(r"\d+(?:\.\d+)?\s*(?:mcg|mg|ug|iu|ml)\S*?\d+\s*vials?\b", re.IGNORECASE)
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


def table_from_words(words: list[Word]) -> list[list[str]] | None:
    """Rows of [code, name, specification, price] (or without the code column) with a header row, or None when the
    words do not hold at least two priced lines."""
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
    codes, prices = [[] for _ in range(count)], [[] for _ in range(count)]
    for word in code_words:
        codes[_nearest(anchors, word.cy)].append(word)
    for word in right:
        prices[_nearest(anchors, word.cy)].append(word)

    names = [[] for _ in range(count)]
    for word in name_words:
        names[_nearest(anchors, word.cy)].append(word)

    def text(group: list[Word]) -> str:
        return " ".join(w.text for w in sorted(group, key=lambda w: (round(w.cy / max(pitch * 0.5, 1)), w.cx)))

    with_codes = split is not None
    header = [_HEADER["code"]] if with_codes else []
    header += [_HEADER["name"], _HEADER["spec"], _HEADER["price"]]
    table = [header]
    for i, anchor in enumerate(anchors):
        row = [text(codes[i])] if with_codes else []
        row += [text(names[i]), _clean_spec(anchor.text), _clean_price(text(prices[i]))]
        table.append(row)
    return table

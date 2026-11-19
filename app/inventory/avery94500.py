"""The periodic-table style round label for Avery 94500 (1 inch round, 6 across by 8 down, 48 to a sheet), as a Word file to print at 100% scale.

AMIDE arches over the top; the element symbol is biggest in the middle with the strength under it; lot (up the left), expiration MM/YY (down the right) and
"Research Use Only" (along the bottom) run round the outer ring. Each label is drawn as an image 0.96 in across, so the Word file cannot be retyped, only
made again. Labels are numbered like the Avery template: 1 is top left, across each row, to 48 at bottom right; a half-used sheet starts at its next free circle.
Pure drawing: no database, no web."""

import io
import math
import os
from dataclasses import dataclass
from datetime import date

import reportlab
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, Twips
from PIL import Image, ImageDraw, ImageFont

PER_SHEET = 48
COLUMNS = 6
FONT = os.path.join(os.path.dirname(reportlab.__file__), "fonts", "VeraBd.ttf")          # ships with reportlab, so it is on every machine Amide runs on

SS = 2                 # supersampling for smooth edges
PX_PER_PT = 12.5       # a 72 pt label is 900 px (900 dpi)
CAP = 0.73             # cap height / em for this font
CX = CY = 36.0
R_OUT, R_IN, R_MID = 35.0, 24.5, 29.75
BLACK = (0, 0, 0, 255)


@dataclass(frozen=True)
class Spec:
    symbol: str
    strength: str          # "10 MG"
    lot: str
    exp: str               # "12/27"
    count: int = 1


def exp_text(d: date | None) -> str:
    return f"{d.month:02d}/{d.year % 100:02d}" if d else ""


def strength_text(amount: float | None, unit: str | None) -> str:
    return f"{amount:g} {unit}".upper() if amount else ""


def _font(size_pt):
    return ImageFont.truetype(FONT, max(1, round(size_pt * PX_PER_PT * SS)))


def _advances(text, size_pt):
    f = _font(size_pt)
    return [f.getlength(c) / (PX_PER_PT * SS) for c in text]


def _glyph(img, ch, size_pt, x_pt, y_pt, rot_deg):
    """One glyph with its baseline-centre at (x_pt, y_pt), turned clockwise by rot_deg."""
    side = int(size_pt * PX_PER_PT * SS * 3)
    tile = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((side / 2, side / 2), ch, font=_font(size_pt), fill=BLACK, anchor="ms")
    tile = tile.rotate(-rot_deg, resample=Image.BICUBIC)
    img.alpha_composite(tile, (int(round(x_pt * PX_PER_PT * SS - side / 2)), int(round(y_pt * PX_PER_PT * SS - side / 2))))


def _arc_text(img, text, size_pt, center_deg, radius_pt, clockwise, track=0.15):
    adv = _advances(text, size_pt)
    total = sum(adv) + track * (len(text) - 1)
    s = 0.0
    for ch, w in zip(text, adv):
        off = (s + w / 2 - total / 2) / radius_pt
        th = math.radians(center_deg) + (off if clockwise else -off)
        if ch != " ":
            _glyph(img, ch, size_pt, CX + radius_pt * math.sin(th), CY - radius_pt * math.cos(th), math.degrees(th) + (0 if clockwise else 180))
        s += w + track


def _flat_text(img, text, size_pt, y_pt, track=0.0):
    adv = _advances(text, size_pt)
    x = CX - (sum(adv) + track * (len(text) - 1)) / 2
    for ch, w in zip(text, adv):
        _glyph(img, ch, size_pt, x + w / 2, y_pt, 0)
        x += w + track


def label_png(spec: Spec) -> bytes:
    n = int(72 * PX_PER_PT * SS)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = PX_PER_PT * SS
    for r, wpt in ((R_OUT, 1.1), (R_IN, 0.6)):
        d.ellipse([(CX - r) * k, (CY - r) * k, (CX + r) * k, (CY + r) * k], outline=BLACK, width=round(wpt * k))
    r_cw = lambda sz: R_MID - CAP * sz / 2
    r_ccw = lambda sz: R_MID + CAP * sz / 2
    # The four ring texts follow one another round the circle, AMIDE at the top, then expiration, "Research Use Only" along the bottom and the lot. Their
    # widths decide where each sits and where the dots go, so a long lot or a wide font never makes two of them run into each other; the small ones
    # shrink together only when the ring is too full to leave a gap for a dot.
    exp, lot = f"EXP {spec.exp}".strip(), f"LOT# {spec.lot}".strip()
    scale = 1.0
    while True:
        parts = [("AMIDE", 8.0 * scale, 1.2, True), (exp, 5.0 * scale, 0.15, True), ("RESEARCH USE ONLY", 5.0 * scale, 0.15, False), (lot, 5.0 * scale, 0.15, True)]
        spans = [math.degrees((sum(_advances(t, sz)) + tr * (len(t) - 1)) / (r_cw(sz) if cw else r_ccw(sz))) for t, sz, tr, cw in parts]
        gap = (360 - sum(spans)) / 4
        if gap >= 9 or scale < 0.5:
            break
        scale -= 0.02
    at = -spans[0] / 2                                       # degrees clockwise from the top: where the next text starts
    dots = []
    for (text, sz, tr, cw), span in zip(parts, spans):
        _arc_text(img, text, sz, at + span / 2, r_cw(sz) if cw else r_ccw(sz), cw, track=tr)
        dots.append(at + span + gap / 2)
        at += span + gap
    for deg in dots:
        t = math.radians(deg)
        x, y = (CX + R_MID * math.sin(t)) * k, (CY - R_MID * math.cos(t)) * k
        d.ellipse([x - 0.75 * k, y - 0.75 * k, x + 0.75 * k, y + 0.75 * k], fill=BLACK)
    sym_pt, str_pt = 28.0, 7.0
    width = sum(_advances(spec.symbol, sym_pt))
    if width > 34.0:
        sym_pt *= 34.0 / width
    str_adv = sum(_advances(spec.strength, str_pt)) if spec.strength else 0
    if str_adv > 34.0:
        str_pt *= 34.0 / str_adv
    desc = 0.22 * sym_pt if any(c in "gjpqy" for c in spec.symbol) else 0.0        # a tail below the line (Bp) must clear the strength
    top = CY - (CAP * sym_pt + desc + 4.5 + CAP * str_pt) / 2
    sym_y = top + CAP * sym_pt
    _flat_text(img, spec.symbol, sym_pt, sym_y)
    if spec.strength:
        _flat_text(img, spec.strength, str_pt, sym_y + desc + 4.5 + CAP * str_pt, track=0.4)
    out = io.BytesIO()
    img.resize((n // SS, n // SS), Image.LANCZOS).save(out, format="PNG", dpi=(900, 900))
    return out.getvalue()


def slots(specs: list[Spec], start: int) -> list[bytes | None]:
    """The sheet positions in order: `start - 1` empty circles first, then each spec's picture once per label."""
    out: list[bytes | None] = [None] * (start - 1)
    for spec in specs:
        png = label_png(spec)
        out += [png] * spec.count
    while len(out) % PER_SHEET:
        out.append(None)
    return out


def _el(tag, **kw):
    e = OxmlElement(tag)
    for key, v in kw.items():
        e.set(qn("w:" + key), v)
    return e


def _tiny_paragraph(doc, page_break=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(1)
    r = p.add_run()
    r.font.size = Pt(1)
    if page_break:
        r.add_break(WD_BREAK.PAGE)


def _sheet(doc, pngs):
    widths = [1440, 360] * 5 + [1440]                    # a 1 in label, a 0.25 in gap, six across
    tbl = doc.add_table(rows=15, cols=11)
    tbl.autofit = False
    pr = tbl._tbl.tblPr
    for ch in list(pr):
        pr.remove(ch)
    pr.append(_el("w:tblW", w="10440", type="dxa"))
    pr.append(_el("w:tblInd", w="0", type="dxa"))
    pr.append(_el("w:tblLayout", type="fixed"))
    cm = _el("w:tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        cm.append(_el("w:" + side, w="0", type="dxa"))
    pr.append(cm)
    pr.append(_el("w:tblLook", val="0000"))
    for gc, w in zip(tbl._tbl.tblGrid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(w))
    k = 0
    for ri, row in enumerate(tbl.rows):
        row._tr.get_or_add_trPr().append(_el("w:trHeight", val="1440" if ri % 2 == 0 else "360", hRule="exact"))
        for ci, cell in enumerate(row.cells):
            cell.width = Twips(widths[ci])
            tcPr = cell._tc.get_or_add_tcPr()
            mar = _el("w:tcMar")
            for side in ("top", "left", "bottom", "right"):
                mar.append(_el("w:" + side, w="0", type="dxa"))
            tcPr.append(mar)
            p = cell.paragraphs[0]
            pf = p.paragraph_format
            pf.space_before = pf.space_after = 0
            pf.left_indent = pf.right_indent = Twips(-120)           # keeps the picture centred even if the app adds cell padding
            pf.line_spacing = 1.0
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if ri % 2 == 0 and ci % 2 == 0:
                tcPr.append(_el("w:vAlign", val="center"))
                if pngs[k]:
                    p.add_run().add_picture(io.BytesIO(pngs[k]), width=Emu(int(0.96 * 914400)), height=Emu(int(0.96 * 914400)))
                k += 1


def word_file(specs: list[Spec], start: int = 1) -> tuple[bytes, int, int]:
    """(the .docx bytes, how many labels, how many sheets). `start` is the first circle to print on, 1 to 48, on the first sheet only."""
    start = min(max(int(start), 1), PER_SHEET)
    placed = slots(specs, start)
    sheets = [placed[i:i + PER_SHEET] for i in range(0, len(placed), PER_SHEET)]
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Twips(12240), Twips(15840)
    sec.left_margin, sec.right_margin = Twips(900), Twips(446)
    sec.top_margin, sec.bottom_margin = Twips(900), Twips(820)
    sec.header_distance = sec.footer_distance = 0
    for p in list(doc.paragraphs):
        doc.element.body.remove(p._p)
    for i, sheet in enumerate(sheets):
        _sheet(doc, sheet)
        _tiny_paragraph(doc, page_break=i < len(sheets) - 1)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue(), sum(s.count for s in specs), len(sheets)

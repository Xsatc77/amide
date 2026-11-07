"""Lays the Protocol Portfolio out as a PDF: the protocol, each peptide's Library page (no dosing chart), then the cost breakdown.

reportlab's built-in fonts cover Western European text, so anything else is written out in plain letters (a Greek beta becomes "beta")
rather than drawn as an empty box."""

from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "κ": "kappa", "λ": "lambda", "μ": "mu", "Δ": "Delta", "Ω": "Omega",
          "→": "->", "←": "<-", "≥": ">=", "≤": "<=", "−": "-", "–": "-", "—": "-", "…": "...", "′": "'", "×": "x", "≈": "~", "•": "-"}
_INK, _MUTED, _RULE, _SOFT = colors.HexColor("#1f2937"), colors.HexColor("#6b7280"), colors.HexColor("#d1d5db"), colors.HexColor("#f3f4f6")
_ACCENT, _CAUTION = colors.HexColor("#0f766e"), colors.HexColor("#b91c1c")


def clean(text) -> str:
    """Text safe for the built-in fonts: smart quotes straightened, symbols and Greek letters spelled out, anything else left out."""
    out = []
    for ch in str(text if text is not None else ""):
        ch = _GREEK.get(ch, ch)
        ch = {"‘": "'", "’": "'", "“": '"', "”": '"', " ": " ", "‑": "-"}.get(ch, ch)
        out.append(ch.encode("cp1252", "ignore").decode("cp1252"))
    return "".join(out)


def _p(text, style) -> Paragraph:
    return Paragraph(escape(clean(text)), style)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("body", parent=base, fontName="Helvetica", fontSize=9.5, leading=13, textColor=_INK)
    return {
        "body": body,
        "small": ParagraphStyle("small", parent=body, fontSize=8, leading=10.5, textColor=_MUTED),
        "lead": ParagraphStyle("lead", parent=body, fontSize=10.5, leading=14.5),
        "title": ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=_ACCENT, spaceBefore=10, spaceAfter=3),
        "h2c": ParagraphStyle("h2c", parent=body, fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=_CAUTION, spaceBefore=10, spaceAfter=3),
        "h3": ParagraphStyle("h3", parent=body, fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=2),
        "cell": ParagraphStyle("cell", parent=body, fontSize=8.5, leading=11),
        "cellb": ParagraphStyle("cellb", parent=body, fontName="Helvetica-Bold", fontSize=8.5, leading=11),
        "right": ParagraphStyle("right", parent=body, fontSize=8.5, leading=11, alignment=TA_RIGHT),
        "rightb": ParagraphStyle("rightb", parent=body, fontName="Helvetica-Bold", fontSize=8.5, leading=11, alignment=TA_RIGHT),
    }


def _money(n) -> str:
    return f"${n:,.2f}"


def _us(d) -> str:
    return d.strftime("%m/%d/%Y") if d else "-"


def _table(rows, widths, head=True, align_right=()) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if head else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, _RULE),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if head:
        style += [("BACKGROUND", (0, 0), (-1, 0), _SOFT)]
    table.setStyle(TableStyle(style))
    return table


def _bullets(items, st) -> ListFlowable:
    return ListFlowable([ListItem(_p(i, st["body"]), leftIndent=12) for i in items], bulletType="bullet", start="-", leftIndent=12, bulletFontSize=9)


def _block(block, st, width) -> list:
    out = []
    heading = block.get("heading")
    head = [_p(heading, st["h2c"] if block.get("caution") else st["h2"])] if heading else []
    body = []
    for text in block.get("paragraphs", []):
        body.append(_p(text, st["small"] if block.get("small") else st["lead"] if block.get("lead") else st["body"]))
    if block.get("numbered"):
        body.append(ListFlowable([ListItem(_p(i, st["body"]), leftIndent=14) for i in block["numbered"]], bulletType="1", leftIndent=14, bulletFontName="Helvetica", bulletFontSize=9))
    if block.get("bullets"):
        body.append(_bullets(block["bullets"], st))
    if block.get("facts"):
        body.append(_table([[_p(k, st["cellb"]), _p(v, st["cell"])] for k, v in block["facts"]], [1.7 * inch, width - 1.7 * inch], head=False))
    if block.get("table"):
        t = block["table"]
        cols = len(t["head"])
        body.append(_table([[_p(h, st["cellb"]) for h in t["head"]]] + [[_p(c, st["cell"]) for c in row] for row in t["rows"]], [width / cols] * cols))
    flow = head + body
    if not flow:
        return out
    if block.get("caution"):                                         # the caution box stays in one piece
        return [KeepTogether(flow)]
    return [KeepTogether(flow[:2])] + flow[2:]                       # a heading never sits alone at the foot of a page


def _protocol_pages(protocol, st, width) -> list:
    out = [_p(protocol["name"], st["title"])]
    meta = f"{protocol['status']} - starts {_us(protocol['start'])}" + (f" - ends {_us(protocol['end'])}" if protocol["end"] else "")
    if protocol["goals"]:
        meta += " - Goals: " + ", ".join(protocol["goals"])
    out += [_p(meta, st["small"]), Spacer(1, 6), HRFlowable(width="100%", color=_RULE, thickness=0.6), Spacer(1, 6)]
    if protocol["notes"]:
        out += [_p(protocol["notes"], st["body"]), Spacer(1, 6)]
    rows = [[_p("Peptide", st["cellb"]), _p("Dose and Schedule", st["cellb"]), _p("Details", st["cellb"])]]
    for it in protocol["items"]:
        details = list(it["steps"]) + list(it["off"]) + ([it["notes"]] if it["notes"] else [])
        rows.append([_p(it["name"], st["cellb"]), _p(it["desc"], st["cell"]), [_p(d, st["cell"]) for d in details] or _p("-", st["cell"])])
    out.append(_table(rows, [1.7 * inch, 2.4 * inch, width - 4.1 * inch]))
    out += [Spacer(1, 10), _p("Research use only. Informational only, not medical advice.", st["small"])]
    return out


def _library_pages(page, st, width) -> list:
    out = [PageBreak(), _p(page["name"], st["title"])]
    if page.get("subtitle"):
        out.append(_p(page["subtitle"], st["small"]))
    tags = list(page["tags"]) + [f"Goal: {g}" for g in page["goals"]]
    if tags:
        out.append(_p("  |  ".join(tags), st["small"]))
    out += [Spacer(1, 4), HRFlowable(width="100%", color=_RULE, thickness=0.6)]
    for block in page["blocks"]:
        out += _block(block, st, width)
    return out


def _cost_pages(prices, protocol, today, st, width) -> list:
    out = [PageBreak(), _p("Cost Breakdown", st["title"]),
           _p(f"{protocol['name']} - prices from each vendor's newest price list, as of {_us(today)}", st["small"]),
           Spacer(1, 4), HRFlowable(width="100%", color=_RULE, thickness=0.6)]
    if prices["status"] == "error":
        out += [Spacer(1, 8), _p("The prices for this protocol could not be worked out this time, so the cost page is left empty. The rest of the portfolio is complete. Check the Shopping plan for this protocol, or try again later.", st["body"])]
        return out
    if prices["status"] == "no_end_date":
        out += [Spacer(1, 8), _p("This protocol has no end date, so there is no course total to price and no BAC water to estimate. Give it an end date and make the portfolio again.", st["body"])]
        return out
    ship = prices["shipping"]
    out += [Spacer(1, 4), _p(f"Each figure is the cost of the whole course from that source, with a 5% buffer on the dose, in whole packs. Shipping is not included: "
                             f"the Shopping plan assumes China {_money(ship['china'])} and US {_money(ship['us'])} per order, shared by everything bought from that vendor.", st["small"])]
    if not prices["peptides"]:
        out += [Spacer(1, 8), _p("There is nothing on this protocol that can be priced from the current price lists.", st["body"])]
    for row in prices["peptides"]:
        title = _p(f"{row['name']} - {row['need']} for the course", st["h3"])
        if not row["options"]:
            out.append(KeepTogether([title, _p("Not on any current price list in a usable size.", st["small"])]))
            continue
        cells = [[_p(h, st["rightb"] if h in ("Per Vial", "Cost") else st["cellb"]) for h in ("#", "Vendor", "Buy", "Per Vial", "Cost")]]
        for i, o in enumerate(row["options"], 1):
            where = "US warehouse" if o["warehouse"] == "us" else "China warehouse"
            cells.append([_p(str(i), st["cell"]), [_p(o["vendor"], st["cellb"]), _p(f"{where}, list {_us(o['list_date'])}", st["small"])],
                          [_p(f"{o['packs']} x {o['pack']} ({o['size_label']})", st["cell"]),
                           _p(f"{o['vials']} vials needed" + (f", {o['leftover_vials']} left over" if o["leftover_vials"] else ""), st["small"])],
                          _p(_money(o["per_vial"]), st["right"]), _p(_money(o["cost"]), st["rightb"])])
        out.append(KeepTogether([title, _table(cells, [0.3 * inch, 2.2 * inch, width - 3.8 * inch, 0.6 * inch, 0.7 * inch])]))
    for u in prices["unpriced"]:
        out.append(_p(f"{u['name']}: not priced. {u['reason']}.", st["small"]))
    bac = prices.get("bac")
    out.append(_p("BAC Water For The Full Protocol", st["h2"]))
    days = (protocol["end"] - protocol["start"]).days + 1 if protocol["end"] else None
    if not bac:
        out.append(_p("This course needs no BAC water.", st["body"]))
    else:
        out.append(_p(f"About {bac['ml']} mL over {days} days: {bac['bottles']} bottle{'s' if bac['bottles'] != 1 else ''} of 30 mL "
                      "(an opened bottle is good for 28 days).", st["body"]))
        if bac.get("assumed"):
            out.append(_p("No vial size is known for " + ", ".join(bac["assumed"]) + ": the water assumes 5 mg vials. "
                          "Link an inventory item or set the Library's usual vial size for a firmer figure.", st["small"]))
        buy = bac.get("buy")
        if buy:
            where = "US warehouse" if buy["warehouse"] == "us" else "China warehouse"
            out.append(_p(f"Cheapest brand you rank: {buy['product']} {buy['size_label']} from {buy['vendor']} ({where}): {buy['packs']} x {buy['pack_label']} "
                          f"({buy['units']} bottle{'s' if buy['units'] != 1 else ''}), {_money(buy['cost'])}"
                          + (f" plus {_money(buy['shipping'])} shipping as its own order" if buy["shipping"] else ", added to an order from the same vendor")
                          + f". Total {_money(buy['extra'])}.", st["body"]))
        else:
            out.append(_p("No BAC water brand you rank is on the current price lists, so there is no price. Buy it separately.", st["small"]))
    out += [Spacer(1, 10), _p("Prices come from each vendor's newest price list. Confirm with the vendor before ordering.", st["small"])]
    return out


def build_portfolio(protocol: dict, pages: list[dict], prices: dict, today: date) -> bytes:
    """`protocol`: {"name", "status", "start", "end", "goals", "notes", "items": [{"name", "desc", "notes", "steps", "off"}]}."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.7 * inch, bottomMargin=0.8 * inch,
                            title=clean(f"Protocol Portfolio - {protocol['name']}"), author="Amide")
    width = letter[0] - 1.6 * inch
    st = _styles()
    story = _protocol_pages(protocol, st, width)
    for page in pages:
        story += _library_pages(page, st, width)
    story += _cost_pages(prices, protocol, today, st, width)

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_MUTED)
        canvas.drawString(0.8 * inch, 0.5 * inch, clean(f"Amide - Protocol Portfolio - {protocol['name']}"))
        canvas.drawRightString(letter[0] - 0.8 * inch, 0.5 * inch, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()

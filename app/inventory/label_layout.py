"""A custom label sheet, from the label maker's own measurements: page, columns and rows, label size, margins, pitch and shape.

Everyone prints on different labels, so Amide does not carry a list of products. A person types the numbers off their label template
(for Avery, the template page lists the page margins, the label size and the pitch) in inches or millimetres, and this turns them into the
print CSS. Pitch is the distance from the left (or top) edge of one label to the same edge of the next, the way label templates give it.
Pure functions: no database, no web."""

from dataclasses import asdict, dataclass

PAGES = {"letter": (8.5, 11.0, "letter"), "a4": (8.27, 11.69, "A4")}
SHAPES = ("rect", "round")
_MM = 25.4
_SLACK = 0.02                      # inches of tolerance when checking that the labels fit on the page


class LayoutError(ValueError):
    """The measurements are missing, out of range or impossible; the message says which."""


@dataclass(frozen=True)
class Layout:
    page: str
    cols: int
    rows: int
    width: float
    height: float
    top: float
    left: float
    h_pitch: float
    v_pitch: float
    shape: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Layout":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})


def per_page(layout: Layout) -> int:
    return layout.cols * layout.rows


def _whole(raw, key: str, label: str, high: int) -> int:
    text = str(raw.get(key) or "").strip()
    if not text.isdigit() or not 1 <= int(text) <= high:
        raise LayoutError(f"Enter the number of {label} as a whole number from 1 to {high}.")
    return int(text)


def _length(raw, key: str, label: str, factor: float, *, allow_zero: bool = False) -> float:
    try:
        value = float(str(raw.get(key) or "").strip()) / factor
    except ValueError:
        raise LayoutError(f"Enter the {label} as a number.") from None
    if value < 0 or (value == 0 and not allow_zero) or value > 15:
        raise LayoutError(f"The {label} is out of range.")
    return round(value, 4)


def parse_layout(raw) -> Layout:
    """The layout from the settings form (`raw` is a mapping of strings), validated; lengths are converted to inches."""
    page = str(raw.get("page") or "")
    if page not in PAGES:
        raise LayoutError("Choose the page size: Letter or A4.")
    unit = str(raw.get("unit") or "in")
    if unit not in ("in", "mm"):
        raise LayoutError("Choose the unit: inches or millimetres.")
    factor = _MM if unit == "mm" else 1.0
    shape = str(raw.get("shape") or "rect")
    if shape not in SHAPES:
        raise LayoutError("Choose the label shape: rectangle or round.")
    cols, rows = _whole(raw, "cols", "columns", 12), _whole(raw, "rows", "rows", 30)
    width, height = _length(raw, "width", "label width", factor), _length(raw, "height", "label height", factor)
    top, left = _length(raw, "top", "top margin", factor, allow_zero=True), _length(raw, "left", "left margin", factor, allow_zero=True)
    h_pitch, v_pitch = _length(raw, "h_pitch", "horizontal pitch", factor), _length(raw, "v_pitch", "vertical pitch", factor)
    if h_pitch + _SLACK < width or v_pitch + _SLACK < height:
        raise LayoutError("The pitch is the distance from one label's edge to the next label's edge, so it cannot be smaller than the label itself.")
    page_w, page_h, _ = PAGES[page]
    if left + (cols - 1) * h_pitch + width > page_w + _SLACK or top + (rows - 1) * v_pitch + height > page_h + _SLACK:
        raise LayoutError(f"These measurements do not fit on a {PAGES[page][2]} page: {cols} across and {rows} down run past the paper. "
                          "Check the unit and the pitch.")
    return Layout(page, cols, rows, width, height, top, left, h_pitch, v_pitch, shape)


def css(layout: Layout) -> str:
    """The print rules for this sheet: one .label-page per sheet of paper, the labels on a grid at the given margins and pitch."""
    page_w, page_h, css_name = PAGES[layout.page]
    gap_x, gap_y = round(layout.h_pitch - layout.width, 4), round(layout.v_pitch - layout.height, 4)
    small = min(layout.width, layout.height)
    base = round(max(5.5, min(10.0, 7.5 * small)), 1)
    out = [
        f"@page {{ size: {css_name}; margin: 0; }}",
        f".label-custom .label-page {{ box-sizing: border-box; width: {page_w}in; height: {page_h}in; padding: {layout.top}in 0 0 {layout.left}in; "
        f"display: grid; grid-template-columns: repeat({layout.cols}, {layout.width}in); grid-auto-rows: {layout.height}in; "
        f"column-gap: {gap_x}in; row-gap: {gap_y}in; align-content: start; page-break-after: always; break-after: page; }}",
        ".label-custom .label-page:last-child { page-break-after: auto; break-after: auto; }",
        f".label-custom .vial-label {{ width: {layout.width}in; height: {layout.height}in; margin: 0; font-size: {base}pt; }}",
        f".label-custom .vial-label .vl-name {{ font-size: {round(base * 1.2, 1)}pt; }}",
        f".label-custom .vial-label .vl-ruo {{ font-size: {round(max(4.5, base * 0.8), 1)}pt; }}",
    ]
    if layout.shape == "round":
        pad = round(small * 0.12, 3)
        out.append(f".label-custom .vial-label {{ border-radius: 50%; padding: {pad}in; text-align: center; display: flex; flex-direction: column; "
                   "justify-content: center; align-items: center; }")
        out.append(".label-custom .vial-label .vl-dates { flex-direction: column; gap: 0; align-items: center; }")
    return "\n".join(out)

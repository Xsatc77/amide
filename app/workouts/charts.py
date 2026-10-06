"""Geometry for the workout charts: stacked bars (daily burn against TDEE, weekly volume by body area) and a ring
(share of burn by equipment). Pure: numbers in, pixel coordinates and SVG path data out; templates draw the SVG.
Bars start at zero, as stacked bars must; line charts (exercise progression) reuse the price chart's
`multi_series_chart`, whose value axis fits the data."""

import math
from datetime import date

_NICE = (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7.5, 8, 10)


def nice_ceiling(value: float, ticks: int = 4) -> float:
    """A round axis maximum at or above `value`, chosen so `ticks` equal steps land on round numbers."""
    if value <= 0:
        return float(ticks)
    raw_step = value / ticks
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for factor in _NICE:
        step = factor * magnitude
        if step * ticks >= value:
            return step * ticks
    return 10 * magnitude * ticks


def stacked_bars(labels: list[date | str], stacks: dict[str, list[float]], *, line: list[float | None] | None = None,
                 width: int = 640, height: int = 240, pad_l: int = 52, pad_r: int = 14, pad_t: int = 14,
                 pad_b: int = 30, gap: float = 0.25) -> dict | None:
    """One bar per label; `stacks` maps a segment name to one value per label, drawn bottom to top in the order
    given. `line` is an optional value per label drawn as a polyline (the goal target). None when there is nothing."""
    if not labels or not stacks:
        return None
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    totals = [sum(max(stack[i], 0) for stack in stacks.values()) for i in range(len(labels))]
    line_values = [v for v in (line or []) if v is not None]
    top = nice_ceiling(max([*totals, *line_values, 0]))
    slot = plot_w / len(labels)
    bar_w = max(slot * (1 - gap), 1.0)

    def y_of(v: float) -> float:
        return pad_t + plot_h - v / top * plot_h

    bars = []
    for i, label in enumerate(labels):
        x = pad_l + i * slot + (slot - bar_w) / 2
        base, segments = 0.0, []
        for name, values in stacks.items():
            value = max(values[i], 0)
            if value <= 0:
                continue
            segments.append({"name": name, "value": value, "x": round(x, 1), "w": round(bar_w, 1),
                             "y": round(y_of(base + value), 1), "h": round(value / top * plot_h, 1)})
            base += value
        bars.append({"label": label, "total": totals[i], "x": round(x, 1), "w": round(bar_w, 1), "segments": segments})
    line_points = []
    if line:
        for i, value in enumerate(line):
            if value is not None:
                line_points.append({"x": round(pad_l + i * slot + slot / 2, 1), "y": round(y_of(value), 1), "value": value})
    steps = 4
    last = len(labels) - 1
    x_ticks = sorted({0, last // 2, last})
    return {
        "width": width, "height": height, "top": top, "bars": bars,
        "line": {"points": line_points, "poly": " ".join(f"{p['x']},{p['y']}" for p in line_points)} if line_points else None,
        "y_ticks": [{"value": top * k / steps, "y": round(y_of(top * k / steps), 1)} for k in range(steps + 1)],
        "x_ticks": [{"label": labels[i], "x": round(pad_l + i * slot + slot / 2, 1)} for i in x_ticks],
        "plot": {"left": pad_l, "right": width - pad_r, "top": pad_t, "bottom": height - pad_b},
    }


def ring(slices: list[tuple[str, float]], *, size: int = 200, outer: float = 90, inner: float = 56) -> dict | None:
    """Donut geometry: each slice with its share, an SVG path for its arc and the angle midpoint for a label.
    Slices with no value are dropped; None when nothing is left."""
    kept = [(name, value) for name, value in slices if value and value > 0]
    total = sum(value for _, value in kept)
    if not kept:
        return None
    cx = cy = size / 2
    out, start = [], -math.pi / 2
    for name, value in kept:
        share = value / total
        sweep = min(share * 2 * math.pi, 2 * math.pi - 1e-4)   # a full circle is not drawable as one arc
        end = start + sweep
        large = 1 if sweep > math.pi else 0
        def point(r, a):
            return f"{cx + r * math.cos(a):.2f},{cy + r * math.sin(a):.2f}"
        path = (f"M {point(outer, start)} A {outer} {outer} 0 {large} 1 {point(outer, end)} "
                f"L {point(inner, end)} A {inner} {inner} 0 {large} 0 {point(inner, start)} Z")
        out.append({"name": name, "value": value, "share": share, "path": path})
        start = end
    return {"size": size, "total": total, "slices": out}

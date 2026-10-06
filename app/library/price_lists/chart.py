"""Geometry for a multi-series line chart (one line per vial size) sharing one date scale and one value scale.
Pure: dates and numbers in, pixel coordinates out; the template draws the SVG."""

from datetime import date

_MARGIN = 0.12        # breathing room above and below the data, as a share of the value span
_MIN_SPAN = 0.06      # the axis never covers less than this share of the top price, so cent-level noise is not magnified


def multi_series_chart(series: dict[str, list[tuple[date, float]]], *, width: int = 560, height: int = 220,
                       pad_l: int = 48, pad_r: int = 14, pad_t: int = 14, pad_b: int = 28) -> dict | None:
    series = {label: sorted(points) for label, points in series.items() if points}
    if not series:
        return None
    every = [p for points in series.values() for p in points]
    dates = [d for d, _ in every]
    values = [v for _, v in every]
    min_d, max_d, min_v, max_v = min(dates), max(dates), min(values), max(values)
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    span_d, span_v = (max_d - min_d).days, max_v - min_v

    def x_of(d: date) -> float:
        return pad_l + plot_w / 2 if span_d == 0 else pad_l + (d - min_d).days / span_d * plot_w

    # The value axis fits the data (it does not start at zero), with a little margin so lines never sit on the
    # frame, and a floor on its height so a few cents of change does not fill the whole chart.
    axis_span = max(span_v, max_v * _MIN_SPAN) * (1 + 2 * _MARGIN)
    axis_top = (max_v + min_v) / 2 + axis_span / 2

    def y_of(v: float) -> float:
        return pad_t + plot_h / 2 if span_v == 0 else pad_t + (axis_top - v) / axis_span * plot_h

    out = {}
    for label, points in series.items():
        coords = [{"x": round(x_of(d), 1), "y": round(y_of(v), 1), "date": d, "value": v} for d, v in points]
        out[label] = {"points": coords, "poly": " ".join(f"{c['x']},{c['y']}" for c in coords)}
    y_values = [max_v] if span_v == 0 else [max_v, (max_v + min_v) / 2, min_v]
    x_dates = [min_d] if span_d == 0 else [min_d, max_d]
    return {
        "width": width, "height": height, "series": out, "min_d": min_d, "max_d": max_d,
        "y_ticks": [{"y": round(y_of(v), 1), "value": v} for v in y_values],
        "x_ticks": [{"x": round(x_of(d), 1), "date": d} for d in x_dates],
        "plot": {"left": pad_l, "right": width - pad_r, "top": pad_t, "bottom": height - pad_b},
    }

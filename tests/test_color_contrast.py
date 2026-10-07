"""Text stays readable in every colour theme: WCAG contrast of the main text pairs (4.5:1 for normal text)."""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
NAMED = {"white": (255, 255, 255), "black": (0, 0, 0)}
MIX = re.compile(r"^color-mix\(in srgb,\s*(\S+)\s+(\d+)%,\s*(\S+)\)$")


def rgb(value: str):
    value = value.strip()
    if value in NAMED:
        return NAMED[value]
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        return tuple(int(c * 2, 16) for c in value[1:])
    m = MIX.match(value)
    if m:
        a, pct, b = rgb(m.group(1)), int(m.group(2)) / 100, rgb(m.group(3))
        return tuple(round(a[i] * pct + b[i] * (1 - pct)) for i in range(3))
    return None


def variables(body: str) -> dict:
    out = {}
    for name, value in re.findall(r"--([a-z-]+):\s*([^;]+);", body):
        color = rgb(value)
        if color is not None:
            out[name] = color
    return out


def themes():
    base = variables(re.search(r"^:root \{(.*?)\n\}", CSS, re.S | re.M).group(1))
    out = {"default": dict(base)}
    for name, body in re.findall(r':root\[data-theme="([a-z_]+)"\] \{(.*?)\n\}', CSS, re.S):
        out[name] = out.get(name, base) | variables(body)
    return out


def luminance(color) -> float:
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


THEMES = themes()
PAIRS = [("text", "bg"), ("text", "surface"), ("muted", "surface"), ("muted", "bg"), ("accent-text", "accent"), ("danger", "surface")]


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_main_text_pairs_are_readable_in_every_theme(theme):
    colors = THEMES[theme]
    low = [(fg, bg, round(contrast(colors[fg], colors[bg]), 2)) for fg, bg in PAIRS if fg in colors and bg in colors and contrast(colors[fg], colors[bg]) < 4.5]
    assert low == [], f"{theme}: {low}"

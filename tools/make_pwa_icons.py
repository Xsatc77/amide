"""Draws the app icons (the two linked dots of the favicon) as PNGs: 192, 512, a maskable 512 and the 180 px touch icon.
Run once; the results are committed in app/static/icons. Needs Pillow (already an Amide dependency)."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
TEAL, WHITE, MINT = "#0f766e", "#ffffff", "#99f6e4"


def draw(size: int, *, maskable: bool = False) -> Image.Image:
    scale = size / 32
    img = Image.new("RGBA", (size, size), TEAL if maskable else (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if not maskable:
        d.rounded_rectangle((0, 0, size - 1, size - 1), radius=8 * scale, fill=TEAL)
    k = 0.72 if maskable else 1.0                          # a maskable icon keeps its art inside the middle safe zone
    c = 16 * scale
    def at(v):
        return c + (v - 16) * scale * k
    d.rounded_rectangle((at(12), at(14.5), at(20), at(17.5)), radius=1.5 * scale * k, fill=MINT)
    for cx in (9, 23):
        d.ellipse((at(cx - 4), at(12), at(cx + 4), at(20)), fill=WHITE)
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192).save(OUT / "icon-192.png")
    draw(512).save(OUT / "icon-512.png")
    draw(512, maskable=True).save(OUT / "icon-maskable-512.png")
    draw(180, maskable=True).save(OUT / "apple-touch-icon.png")

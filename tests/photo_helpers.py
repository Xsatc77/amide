"""Builders for body-photo tests: tiny synthetic images only, never real photos."""

import io
import statistics

from PIL import Image, ImageDraw

try:
    from pillow_heif import from_pillow
except ImportError:  # pragma: no cover
    from_pillow = None


def gradient(size=(300, 200)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], 6):
        draw.line([(x, 0), (x, size[1])], fill=(x * 255 // size[0], 40, 200 - x * 200 // size[0]), width=3)
    for y in range(0, size[1], 10):
        draw.line([(0, y), (size[0], y)], fill=(0, 0, 0), width=1)
    return image


def jpeg(size=(300, 200), exif=False, orientation=None):
    buffer = io.BytesIO()
    kwargs = {}
    if exif or orientation:
        tags = Image.Exif()
        tags[0x010F] = "TestMake"                    # camera make
        tags[0x8825] = {1: "N", 2: (40.0, 0.0, 0.0)}  # a GPS block
        if orientation:
            tags[0x0112] = orientation
        kwargs["exif"] = tags
    gradient(size).save(buffer, "JPEG", **kwargs)
    return buffer.getvalue()


def png(size=(64, 64), alpha=False):
    buffer = io.BytesIO()
    image = gradient(size).convert("RGBA" if alpha else "RGB")
    if alpha:
        image.putalpha(0)
    image.save(buffer, "PNG")
    return buffer.getvalue()


def heic(size=(64, 64)):
    buffer = io.BytesIO()
    from_pillow(gradient(size)).save(buffer, format="HEIF")
    return buffer.getvalue()


def spread(jpeg_bytes):
    """How much detail an image holds: the standard deviation of its grey levels."""
    grey = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
    return statistics.pstdev(grey.getdata())

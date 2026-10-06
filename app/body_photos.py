"""Body photos: check, clean and store an upload; make the blurred preview. Pure file work, no web or database."""

import io
import re
import secrets
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from app import config

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()  # lets Pillow read iPhone HEIC photos
except ImportError:  # pragma: no cover
    pass

_NAME = re.compile(r"^[0-9a-f]{32}[.]jpg$")
_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF"}
_UNREADABLE = "That file is not a photo this app can read. Use a JPG, PNG, WEBP or iPhone HEIC picture."
_PREVIEW_SIDE = 480


class PhotoError(ValueError):
    """The upload cannot be used; the message is safe to show."""


def process_upload(data: bytes) -> bytes:
    """Clean JPEG bytes from an uploaded photo: orientation applied, metadata removed, no larger than
    config.PHOTO_MAX_SIDE on the longest side. Raises PhotoError for anything that is not a readable photo."""
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise PhotoError(f"Photo is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in _FORMATS:
            raise PhotoError(_UNREADABLE)
        if image.width * image.height > config.PHOTO_MAX_PIXELS:
            raise PhotoError("That photo's dimensions are too large.")
        image = ImageOps.exif_transpose(image)
        image.load()
    except PhotoError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise PhotoError(_UNREADABLE) from None
    if image.mode in ("RGBA", "LA", "P"):
        flat = Image.new("RGB", image.size, "white")
        rgba = image.convert("RGBA")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    else:
        image = image.convert("RGB")
    image.thumbnail((config.PHOTO_MAX_SIDE, config.PHOTO_MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=88, optimize=True)   # no exif= argument: nothing is carried over
    return out.getvalue()


def photo_path(name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError("not a stored photo name")
    return config.BODY_PHOTO_DIR / name


def store(jpeg: bytes) -> str:
    config.ensure_dirs()
    name = secrets.token_hex(16) + ".jpg"
    photo_path(name).write_bytes(jpeg)
    return name


def delete_file(name: str | None) -> None:
    if name:
        try:
            photo_path(name).unlink(missing_ok=True)
        except ValueError:
            pass


def blurred_jpeg(path: Path) -> bytes:
    """A small, heavily blurred copy: faces and bodies cannot be made out and the blur cannot be reversed."""
    with Image.open(path) as image:
        image = image.convert("RGB")
    image.thumbnail((_PREVIEW_SIDE, _PREVIEW_SIDE), Image.LANCZOS)
    image = image.filter(ImageFilter.GaussianBlur(radius=max(12, min(image.size) // 10)))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=60)
    return out.getvalue()

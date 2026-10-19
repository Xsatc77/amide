"""Journal photos: store, serve and delete the image files kept with a journal day. Cleaning an upload (orientation, metadata, size) is the body-photo code."""

import io
import re
import secrets
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app import body_photos, config

_NAME = re.compile(r"^[0-9a-f]{32}[.]jpg$")
MAX_PER_DAY = 12
THUMB_SIDE = 480

PhotoError = body_photos.PhotoError
process_upload = body_photos.process_upload


def photo_path(name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError("not a stored photo name")
    return config.JOURNAL_PHOTO_DIR / name


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


def thumbnail_jpeg(path: Path) -> bytes:
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise PhotoError("That photo could not be read.") from None
    image.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=75)
    return out.getvalue()

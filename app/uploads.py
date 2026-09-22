"""Validation and storage for uploaded COA files."""

import uuid
from pathlib import Path

from fastapi import UploadFile

from app import config

# Extension -> media type. Only these are accepted and served back.
ALLOWED_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
}


class UploadError(ValueError):
    pass


def _sniff_ok(ext: str, head: bytes) -> bool:
    """Check the file's leading bytes actually match its claimed extension."""
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in (".heic", ".heif"):
        return head[4:8] == b"ftyp"
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    return False


async def save_coa(upload: UploadFile) -> str:
    """Validate and store an uploaded COA. Returns the stored filename."""
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_TYPES:
        raise UploadError("COA must be a photo (JPG, PNG, WEBP, HEIC) or a PDF.")

    data = await upload.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError(f"COA file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not _sniff_ok(ext, data[:16]):
        raise UploadError("COA file contents don't match its file type.")

    config.ensure_dirs()
    filename = f"{uuid.uuid4().hex}{ext}"
    (config.COA_DIR / filename).write_bytes(data)
    return filename


def coa_path(filename: str) -> Path:
    return config.COA_DIR / filename


def delete_coa(filename: str | None) -> None:
    if filename:
        coa_path(filename).unlink(missing_ok=True)


def media_type(filename: str) -> str:
    return ALLOWED_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")

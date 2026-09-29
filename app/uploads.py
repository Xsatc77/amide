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


# Same as ALLOWED_TYPES, plus Word documents -- price lists are often sent as .doc/.docx, which
# COAs deliberately don't accept.
PRICE_LIST_ALLOWED_TYPES = {
    **ALLOWED_TYPES,
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _price_list_sniff_ok(ext: str, head: bytes) -> bool:
    """Like `_sniff_ok`, extended with the two Word-document formats."""
    if ext == ".docx":
        return head.startswith(b"PK\x03\x04")
    if ext == ".doc":
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    return _sniff_ok(ext, head)


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


async def save_price_list(upload: UploadFile) -> str:
    """Validate and store an uploaded vendor price list. Returns the stored filename."""
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in PRICE_LIST_ALLOWED_TYPES:
        raise UploadError("Price list must be a photo (JPG, PNG, WEBP, HEIC), a PDF, or a Word document.")

    data = await upload.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError(f"Price list file is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not _price_list_sniff_ok(ext, data[:16]):
        raise UploadError("Price list file contents don't match its file type.")

    config.ensure_dirs()
    filename = f"{uuid.uuid4().hex}{ext}"
    (config.PRICE_LIST_DIR / filename).write_bytes(data)
    return filename


def price_list_path(filename: str) -> Path:
    return config.PRICE_LIST_DIR / filename


def delete_price_list(filename: str | None) -> None:
    if filename:
        price_list_path(filename).unlink(missing_ok=True)


def price_list_media_type(filename: str) -> str:
    return PRICE_LIST_ALLOWED_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


async def save_lab_report(upload: UploadFile) -> str:
    """Validate and store an uploaded lab report. Returns the stored filename."""
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_TYPES:
        raise UploadError("Lab report must be a photo (JPG, PNG, WEBP, HEIC) or a PDF.")

    data = await upload.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError(f"Lab report is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not _sniff_ok(ext, data[:16]):
        raise UploadError("Lab report file contents don't match its file type.")

    config.ensure_dirs()
    filename = f"{uuid.uuid4().hex}{ext}"
    (config.LAB_REPORT_DIR / filename).write_bytes(data)
    return filename


def lab_report_path(filename: str) -> Path:
    return config.LAB_REPORT_DIR / filename


def delete_lab_report(filename: str | None) -> None:
    if filename:
        lab_report_path(filename).unlink(missing_ok=True)


async def save_workout_pdf(upload: UploadFile) -> str:
    """Validate and store an uploaded workout-plan PDF. Returns the stored filename."""
    ext = Path(upload.filename or "").suffix.lower()
    if ext != ".pdf":
        raise UploadError("Workout plan must be a PDF.")

    data = await upload.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise UploadError(f"PDF is larger than {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not _sniff_ok(ext, data[:16]):
        raise UploadError("File contents don't match a PDF.")

    config.ensure_dirs()
    filename = f"{uuid.uuid4().hex}{ext}"
    (config.WORKOUT_PDF_DIR / filename).write_bytes(data)
    return filename

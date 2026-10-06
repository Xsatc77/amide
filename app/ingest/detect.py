"""What a received file is, decided from its bytes (never its name)."""

import io
import zipfile

_PNG = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A, 0x0A])
_JPEG = bytes([0xFF, 0xD8, 0xFF])
_ZIP = b"PK" + bytes([3, 4])


def detect_kind(data: bytes) -> str | None:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == _PNG or data[:3] == _JPEG:
        return "image"
    if data[:4] == _ZIP:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "xlsx" if "xl/workbook.xml" in z.namelist() else None
        except zipfile.BadZipFile:
            return None
    return None

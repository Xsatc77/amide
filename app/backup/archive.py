"""The zip archive inside a backup: a manifest, section JSON files and attached files, each checked by SHA-256.

`write_archive` turns {path: bytes} into one zip; `read_archive` opens one, verifies every entry against the manifest
and refuses anything oversized or oddly named. Pure bytes in, bytes out."""

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass

from app.backup.container import BackupError

FORMAT = 1
MANIFEST = "manifest.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(name: str) -> bool:
    return bool(name) and not name.startswith("/") and ".." not in name.split("/") and "\\" not in name


def write_archive(manifest: dict, entries: dict[str, bytes]) -> bytes:
    """One zip holding `manifest.json` (with a hash per entry) and every entry."""
    for name in entries:
        if not _safe(name) or name == MANIFEST:
            raise BackupError(f"Invalid entry name: {name}")
    full = {**manifest, "format": FORMAT, "entries": {name: _sha(data) for name, data in sorted(entries.items())}}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(full, indent=1, ensure_ascii=False))
        for name, data in sorted(entries.items()):
            z.writestr(name, data)
    return out.getvalue()


@dataclass
class Archive:
    manifest: dict
    _entries: dict[str, bytes]

    def names(self) -> list[str]:
        return sorted(self._entries)

    def has(self, name: str) -> bool:
        return name in self._entries

    def read(self, name: str) -> bytes:
        return self._entries[name]

    def json(self, name: str) -> dict:
        return json.loads(self._entries[name].decode("utf-8"))


def read_archive(data: bytes, *, max_bytes: int) -> Archive:
    """Open and verify an archive, or raise BackupError."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
        if sum(i.file_size for i in infos) > max_bytes:
            raise BackupError("This backup is larger than the allowed size.")
        if any(not _safe(i.filename) for i in infos):
            raise BackupError("This backup file is damaged.")
        manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        if manifest.get("format") != FORMAT:
            raise BackupError("This backup was made by a version of Amide this one cannot read.")
        entries = {}
        for name, digest in manifest.get("entries", {}).items():
            if not _safe(name) or name == MANIFEST:
                raise BackupError("This backup file is damaged.")
            blob = z.read(name)
            if _sha(blob) != digest:
                raise BackupError("This backup file is damaged (a checksum does not match).")
            entries[name] = blob
        if set(entries) != {i.filename for i in infos} - {MANIFEST}:
            raise BackupError("This backup file is damaged (unlisted or missing entries).")
        return Archive(manifest, entries)
    except BackupError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError):
        raise BackupError("This backup file is damaged.") from None

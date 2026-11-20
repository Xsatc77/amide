"""The zip archive inside a backup: a manifest, section JSON files and attached files, each checked by SHA-256.

`write_archive` turns {path: bytes} into one zip; `read_archive` opens one, verifies every entry against the manifest
and refuses anything oversized or oddly named. Pure bytes in, bytes out."""

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass

from app.backup.container import BackupError

FORMAT = 1
MANIFEST = "manifest.json"
MAX_ENTRIES = 20_000        # a backup holds a manifest, a few dozen sections and the attached files
MAX_RATIO = 200             # uncompressed : compressed, per entry; real JSON/photos stay far below this
_CHUNK = 1 << 16


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_PART = re.compile(r"[A-Za-z0-9_()-][A-Za-z0-9._() -]*")
_DEVICE = re.compile(r"(con|prn|aux|nul|com\d|lpt\d)(\..*)?", re.IGNORECASE)


def _safe(name: str) -> bool:
    """A plain relative name: letters, digits and a few separators only, no drive letter, no `..`, no empty or
    device-like parts, nothing Windows treats specially. It is the only gate between an archive entry and a path."""
    if not name or len(name) > 240:
        return False
    for part in name.split("/"):
        if not _PART.fullmatch(part) or part.endswith((".", " ")) or _DEVICE.fullmatch(part):
            return False
    return True


def valid_stored_name(name) -> bool:
    """A file name as stored in a database column: one plain component, no folders."""
    return isinstance(name, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", name)) and ".." not in name


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


def _read_bounded(z: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes:
    """Decompress one entry, counting the bytes actually produced. The size in the zip's directory is only a claim
    (a forged header can understate it), so the real count, not file_size, enforces the limit."""
    out = bytearray()
    with z.open(info) as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            out += chunk
            if len(out) > budget:
                raise BackupError("This backup is larger than the allowed size.")
            if len(out) > MAX_RATIO * max(info.compress_size, 1) + _CHUNK:
                raise BackupError("This backup file is damaged (an entry expands too much).")
    return bytes(out)


def read_archive(data: bytes, *, max_bytes: int) -> Archive:
    """Open and verify an archive, or raise BackupError."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise BackupError("This backup file is damaged (too many entries).")
        if sum(i.file_size for i in infos) > max_bytes:
            raise BackupError("This backup is larger than the allowed size.")
        if any(not _safe(i.filename) for i in infos):
            raise BackupError("This backup file is damaged.")
        by_name = {i.filename: i for i in infos}
        if len(by_name) != len(infos) or MANIFEST not in by_name:
            raise BackupError("This backup file is damaged.")
        remaining = max_bytes
        raw_manifest = _read_bounded(z, by_name[MANIFEST], remaining)
        remaining -= len(raw_manifest)
        manifest = json.loads(raw_manifest.decode("utf-8"))
        if manifest.get("format") != FORMAT:
            raise BackupError("This backup was made by a version of Amide this one cannot read.")
        entries = {}
        for name, digest in manifest.get("entries", {}).items():
            if not _safe(name) or name == MANIFEST:
                raise BackupError("This backup file is damaged.")
            if name not in by_name:
                raise BackupError("This backup file is damaged (unlisted or missing entries).")
            blob = _read_bounded(z, by_name[name], remaining)
            remaining -= len(blob)
            if _sha(blob) != digest:
                raise BackupError("This backup file is damaged (a checksum does not match).")
            entries[name] = blob
        if set(entries) != {i.filename for i in infos} - {MANIFEST}:
            raise BackupError("This backup file is damaged (unlisted or missing entries).")
        return Archive(manifest, entries)
    except BackupError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError, RuntimeError, EOFError, OSError):
        raise BackupError("This backup file is damaged.") from None

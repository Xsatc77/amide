"""Messages waiting to be delivered to Amide, one folder each, written so a crash never leaves a half item."""

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from watcher.ports import Payload
from watcher.safety import safe_filename, write_private

MAX_META_BYTES = 1024 * 1024
MAX_FILES_IN_ITEM = 20
MAX_QUARANTINED = 50                      # the "bad" folder keeps the newest few, so hostile items cannot fill the disk
_META_KEYS = {"chat_id", "message_id", "album_id", "date", "text", "topic_id", "topic_title"}


def _write_file(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


class QueueItem:
    def __init__(self, folder: Path):
        self.folder = folder

    def payload(self) -> Payload:
        meta_path = self.folder / "meta.json"
        if meta_path.stat().st_size > MAX_META_BYTES:
            raise ValueError("meta too large")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        names = meta.pop("file_names", None) if isinstance(meta, dict) else None
        if not isinstance(names, list) or len(names) > MAX_FILES_IN_ITEM or not all(isinstance(n, str) for n in names)                 or not isinstance(meta, dict) or set(meta) - _META_KEYS or not all(v is None or isinstance(v, str) for v in meta.values()):
            raise ValueError("bad meta")
        files = [(safe_filename(name), (self.folder / f"file{n}").read_bytes()) for n, name in enumerate(names)]      # file names never form a path
        return Payload(files=files, **meta)


class DiskQueue:
    def __init__(self, directory: Path, max_items: int = 500, max_bytes: int = 2 * 1024 ** 3):
        self.dir, self.max_items, self.max_bytes = Path(directory), max_items, max_bytes
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for leftover in list(self.dir.glob("*.tmp")) + list(self.dir.glob("*.del")):     # half-written or half-deleted items
            shutil.rmtree(leftover, ignore_errors=True)

    def _folders(self) -> list[Path]:
        return sorted(p for p in self.dir.iterdir() if p.is_dir() and p.name.isdigit())

    def __len__(self) -> int:
        return len(self._folders())

    def _next_number(self) -> int:
        marker = self.dir / "next"
        try:
            number = int(marker.read_text())
        except (OSError, ValueError):
            number = 1 + max((int(p.name) for p in self._folders()), default=0)
        write_private(marker, str(number + 1))
        return number

    def put(self, payload: Payload) -> None:
        number = self._next_number()
        tmp = self.dir / f"{number:012d}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(mode=0o700)
        meta = asdict(payload)
        files = meta.pop("files")
        meta["file_names"] = [name for name, _ in files]
        for n, (_, data) in enumerate(files):
            _write_file(tmp / f"file{n}", data)
        _write_file(tmp / "meta.json", json.dumps(meta).encode("utf-8"))
        os.replace(tmp, self.dir / f"{number:012d}")

    def oldest(self) -> QueueItem | None:
        folders = self._folders()
        return QueueItem(folders[0]) if folders else None

    def remove(self, item: QueueItem) -> None:
        gone = item.folder.with_name(item.folder.name + ".del")          # one atomic rename takes it out of the queue at once
        try:
            os.replace(item.folder, gone)
        except OSError:
            return
        shutil.rmtree(gone, ignore_errors=True)

    def quarantine(self, item: QueueItem) -> None:
        """Set an unreadable item aside so it cannot block the rest."""
        bad = self.dir / "bad"
        bad.mkdir(exist_ok=True, mode=0o700)
        try:
            os.replace(item.folder, bad / item.folder.name)
        except OSError:
            shutil.rmtree(item.folder, ignore_errors=True)
        for old in sorted(p for p in bad.iterdir() if p.is_dir())[:-MAX_QUARANTINED]:
            shutil.rmtree(old, ignore_errors=True)

    def size_bytes(self) -> int:
        return sum(f.stat().st_size for p in self._folders() for f in p.iterdir())

    def full(self) -> bool:
        return len(self) >= self.max_items or self.size_bytes() >= self.max_bytes

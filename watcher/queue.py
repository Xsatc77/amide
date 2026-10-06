"""Messages waiting to be delivered to Amide, one folder each, written so a crash never leaves a half item."""

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from watcher.ports import Payload


class QueueItem:
    def __init__(self, folder: Path):
        self.folder = folder

    def payload(self) -> Payload:
        meta = json.loads((self.folder / "meta.json").read_text(encoding="utf-8"))
        files = [(name, (self.folder / f"file{n}").read_bytes()) for n, name in enumerate(meta.pop("file_names"))]
        return Payload(files=files, **meta)


class DiskQueue:
    def __init__(self, directory: Path, max_items: int = 500, max_bytes: int = 2 * 1024 ** 3):
        self.dir, self.max_items, self.max_bytes = Path(directory), max_items, max_bytes
        self.dir.mkdir(parents=True, exist_ok=True)
        for leftover in self.dir.glob("*.tmp"):                  # an item that was being written when the program stopped
            shutil.rmtree(leftover, ignore_errors=True)

    def _folders(self) -> list[Path]:
        return sorted(p for p in self.dir.iterdir() if p.is_dir() and not p.name.endswith(".tmp"))

    def __len__(self) -> int:
        return len(self._folders())

    def _next_number(self) -> int:
        marker = self.dir / "next"
        try:
            number = int(marker.read_text())
        except (OSError, ValueError):
            number = 1 + max((int(p.name) for p in self._folders()), default=0)
        marker.write_text(str(number + 1))
        return number

    def put(self, payload: Payload) -> None:
        number = self._next_number()
        tmp = self.dir / f"{number:012d}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        meta = asdict(payload)
        files = meta.pop("files")
        meta["file_names"] = [name for name, _ in files]
        for n, (_, data) in enumerate(files):
            (tmp / f"file{n}").write_bytes(data)
        (tmp / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        os.replace(tmp, self.dir / f"{number:012d}")

    def oldest(self) -> QueueItem | None:
        folders = self._folders()
        return QueueItem(folders[0]) if folders else None

    def remove(self, item: QueueItem) -> None:
        shutil.rmtree(item.folder, ignore_errors=True)

    def size_bytes(self) -> int:
        return sum(f.stat().st_size for p in self._folders() for f in p.iterdir())

    def full(self) -> bool:
        return len(self) >= self.max_items or self.size_bytes() >= self.max_bytes

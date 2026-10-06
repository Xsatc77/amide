"""What the watcher remembers between runs, written atomically."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ChatState:
    last_id: int = 0
    started_at: str | None = None
    strikes: int = 0
    gone: bool = False
    title: str | None = None            # the title last registered with Amide
    registered_on: str | None = None    # the day it was registered (refreshed daily)
    failed_id: int = 0                  # a message whose download keeps failing, and how many times
    failed_count: int = 0


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.chats: dict[str, ChatState] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.chats = {k: ChatState(**v) for k, v in raw.get("chats", {}).items()}
            except (ValueError, TypeError, OSError):
                self.chats = {}
                try:
                    os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
                except OSError:
                    pass

    def chat(self, chat_id: str) -> ChatState:
        return self.chats.setdefault(str(chat_id), ChatState())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"chats": {k: asdict(v) for k, v in self.chats.items()}}), encoding="utf-8")
        os.replace(tmp, self.path)

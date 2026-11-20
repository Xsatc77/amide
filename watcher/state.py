"""What the watcher remembers between runs, written atomically."""

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from watcher.safety import write_private

MAX_STATE_BYTES = 5 * 1024 * 1024
_NUMBERS = ("last_id", "strikes", "failed_id", "failed_count")
_TEXTS = ("started_at", "title", "registered_on")


def _checked(value) -> "ChatState":
    """A ChatState from untrusted JSON, or ValueError: wrong types and out-of-range numbers are refused, never coerced."""
    if not isinstance(value, dict) or set(value) - set(ChatState.__dataclass_fields__):
        raise ValueError("bad chat entry")
    for key in _NUMBERS:
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], int) or not 0 <= value[key] < 2 ** 63):
            raise ValueError(key)
    for key in _TEXTS:
        if key in value and value[key] is not None and (not isinstance(value[key], str) or len(value[key]) > 100_000):
            raise ValueError(key)
    if "gone" in value and not isinstance(value["gone"], bool):
        raise ValueError("gone")
    if value.get("started_at") is not None:
        datetime.fromisoformat(value["started_at"])
    return ChatState(**value)


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
                if self.path.stat().st_size > MAX_STATE_BYTES:
                    raise ValueError("state file too large")
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or not isinstance(raw.get("chats", {}), dict):
                    raise ValueError("not a state file")
                self.chats = {str(k): _checked(v) for k, v in raw["chats"].items()} if "chats" in raw else {}
            except (ValueError, TypeError, OSError, KeyError, AttributeError, RecursionError):
                self.chats = {}
                try:
                    os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
                except OSError:
                    pass

    def chat(self, chat_id: str) -> ChatState:
        return self.chats.setdefault(str(chat_id), ChatState())

    def save(self) -> None:
        write_private(self.path, json.dumps({"chats": {k: asdict(v) for k, v in self.chats.items()}}))

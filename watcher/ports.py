"""The shapes passed between the watcher's parts."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Payload:
    chat_id: str
    message_id: str
    album_id: str | None
    date: str                       # ISO 8601, UTC
    text: str
    files: list[tuple[str, bytes]]
    topic_id: str | None = None
    topic_title: str | None = None


@dataclass
class Topic:
    topic_id: str
    title: str


@dataclass
class Group:
    chat_id: str
    title: str
    topics: list[Topic] = field(default_factory=list)       # only forum groups have topics


@dataclass
class Attachment:
    filename: str
    kind: str                       # pdf | image | xlsx
    size: int


@dataclass
class TgMessage:
    chat_id: str
    message_id: int
    date: datetime                  # timezone-aware, UTC
    text: str
    grouped_id: int | None
    attachments: list[Attachment]
    topic_id: str | None = None
    raw: object = field(default=None, repr=False, compare=False)


@dataclass
class Access:
    state: str                      # ok | gone | unknown
    reason: str = ""


class TelegramPort(Protocol):
    async def list_groups(self) -> list[Group]: ...

    async def messages_since(self, chat_id: str, min_id: int, since: datetime | None, topic_id: str | None = None) -> list[TgMessage]: ...

    async def download(self, message: TgMessage, attachment: Attachment) -> bytes: ...

    async def check_access(self, chat_id: str) -> Access: ...

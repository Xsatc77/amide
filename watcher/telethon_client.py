"""The real Telegram connection (Telethon). Thin on purpose: all decisions live in the runner. Read only: it never sends,
reacts, joins, leaves or marks anything read."""

import time
from datetime import datetime, timezone

from telethon import errors, types

from watcher.config import Config
from watcher.ports import Access, Attachment, Group, TgMessage, Topic

_PDF = {"application/pdf"}
_IMAGES = {"image/jpeg", "image/png"}
TOPIC_CACHE_SECONDS, PAGE, MAX_TOPICS = 3600, 100, 1000
_XLSX = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _kind(mime: str | None) -> str | None:
    mime = (mime or "").lower()
    return "pdf" if mime in _PDF else "image" if mime in _IMAGES else "xlsx" if mime in _XLSX else None


class TelethonClient:
    def __init__(self, config: Config):
        from telethon import TelegramClient
        self.config = config
        config.home.mkdir(parents=True, exist_ok=True)
        self._client = TelegramClient(str(config.session_path), config.telegram_api_id, config.telegram_api_hash, flood_sleep_threshold=300,
                                      connection_retries=-1, retry_delay=5)

    async def connect(self) -> None:
        await self._client.connect()

    async def ensure_connected(self) -> None:
        if not self._client.is_connected():                 # after a long outage Telethon stays disconnected until asked
            await self._client.connect()

    async def is_authorized(self) -> bool:
        return await self._client.is_user_authorized()

    async def login_interactive(self) -> None:
        await self._client.start()                          # prompts for the phone, the code and the two-step password

    async def close(self) -> None:
        await self._client.disconnect()

    async def list_groups(self) -> list[Group]:
        groups = []
        async for dialog in self._client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:        # never private chats or bots
                topics = await self._topics(dialog.entity, str(dialog.id)) if getattr(dialog.entity, "forum", False) else []
                groups.append(Group(chat_id=str(dialog.id), title=(dialog.name or "").strip() or str(dialog.id), topics=topics))
        return groups

    async def _topics(self, entity, chat_id: str = "") -> list[Topic]:
        """All topics of a forum group (paged), asked for at most once an hour; trouble keeps the last known list."""
        cache = self.__dict__.setdefault("_topic_cache", {})
        cached = cache.get(chat_id)
        if cached is not None and time.time() - cached[0] < TOPIC_CACHE_SECONDS:
            return cached[1]
        try:
            from telethon.tl.functions.messages import GetForumTopicsRequest
            found: list[Topic] = []
            offset_date, offset_id, offset_topic = None, 0, 0
            while len(found) < MAX_TOPICS:
                page = (await self._client(GetForumTopicsRequest(peer=entity, offset_date=offset_date, offset_id=offset_id,
                                                                 offset_topic=offset_topic, limit=PAGE))).topics
                found.extend(Topic(str(t.id), t.title) for t in page if getattr(t, "title", None))
                last = next((t for t in reversed(page) if getattr(t, "date", None) is not None), None)
                if len(page) < PAGE or last is None:
                    break
                offset_date, offset_id, offset_topic = last.date, last.top_message, last.id
            cache[chat_id] = (time.time(), found)
            return found
        except Exception:
            return cached[1] if cached is not None else []

    async def messages_since(self, chat_id: str, min_id: int, since: datetime | None, topic_id: str | None = None) -> list[TgMessage]:
        entity = await self._client.get_input_entity(int(chat_id))
        kwargs = {"min_id": min_id} if min_id > 0 else {"offset_date": since}
        if topic_id is not None:
            kwargs["reply_to"] = int(topic_id)                       # only that topic's thread
        out = []
        async for m in self._client.iter_messages(entity, reverse=True, **kwargs):
            attachments = []
            media = getattr(m, "media", None)
            if isinstance(m, types.MessageService):          # group photo changes, joins and the like are never price lists
                continue
            if m.photo is not None and isinstance(media, types.MessageMediaPhoto):     # not a link preview's picture
                attachments.append(Attachment(filename=f"photo-{m.id}.jpg", kind="image", size=getattr(m.file, "size", 0) or 0))
            elif m.document is not None and isinstance(media, types.MessageMediaDocument) and _kind(getattr(m.file, "mime_type", None)):
                attachments.append(Attachment(filename=m.file.name or f"file-{m.id}", kind=_kind(m.file.mime_type), size=m.file.size or 0))
            out.append(TgMessage(chat_id=str(chat_id), message_id=m.id, date=m.date.astimezone(timezone.utc), text=m.message or "",
                                 grouped_id=m.grouped_id, attachments=attachments, raw=m,
                                 topic_id=topic_id if topic_id is not None else self._topic_of(m)))
        return out

    @staticmethod
    def _topic_of(m) -> str | None:
        """The topic a message of a whole-group read belongs to, when the group is a forum."""
        reply = getattr(m, "reply_to", None)
        if reply is not None and getattr(reply, "forum_topic", False):
            return str(getattr(reply, "reply_to_top_id", None) or getattr(reply, "reply_to_msg_id", None) or "") or None
        return None

    async def download(self, message: TgMessage, attachment: Attachment) -> bytes:
        data = await self._client.download_media(message.raw, file=bytes)
        if data is None:
            raise OSError("download returned nothing")
        return data

    async def check_access(self, chat_id: str) -> Access:
        try:
            full = await self._client.get_entity(int(chat_id))
            if isinstance(full, (types.ChatForbidden, types.ChannelForbidden)) or (
                    isinstance(full, types.Chat) and (full.deactivated or full.left or getattr(full, "migrated_to", None))):
                return Access("gone", "the group was removed, upgraded to a supergroup, or this account left it")
            entity = await self._client.get_input_entity(int(chat_id))
            await self._client.get_messages(entity, limit=1)
            return Access("ok")
        except (errors.ChannelPrivateError, errors.ChannelInvalidError, errors.ChatIdInvalidError, errors.PeerIdInvalidError):
            return Access("gone", "the group is private, deleted, or this account was removed")
        except Exception:                                    # flood waits, network trouble and anything else: not evidence of removal
            return Access("unknown")

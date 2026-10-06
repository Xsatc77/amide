"""The real Telegram connection (Telethon). Thin on purpose: all decisions live in the runner. Read only: it never sends,
reacts, joins, leaves or marks anything read."""

from datetime import datetime, timezone

from telethon import errors, types

from watcher.config import Config
from watcher.ports import Access, Attachment, Group, TgMessage

_PDF = {"application/pdf"}
_IMAGES = {"image/jpeg", "image/png"}
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
                groups.append(Group(chat_id=str(dialog.id), title=(dialog.name or "").strip() or str(dialog.id)))
        return groups

    async def messages_since(self, chat_id: str, min_id: int, since: datetime | None) -> list[TgMessage]:
        entity = await self._client.get_input_entity(int(chat_id))
        kwargs = {"min_id": min_id} if min_id > 0 else {"offset_date": since}
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
                                 grouped_id=m.grouped_id, attachments=attachments, raw=m))
        return out

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

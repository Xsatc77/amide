"""One poll of the watcher: register groups, read the watched ones into the queue, deliver the queue, check the groups still exist."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from watcher.amide_client import AmideAuthError, AmideUnavailable
from watcher.ports import Payload, TgMessage
from watcher.queue import DiskQueue
from watcher.state import State

ALBUM_SETTLE_SECONDS = 5
BACKOFF = (30, 60, 120, 300, 900)
PAUSE_BETWEEN_GROUPS = 1.0
STRIKES_TO_REPORT = 2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Runner:
    def __init__(self, tg, amide, state: State, queue: DiskQueue, config, *, now=_utc_now, sleep=asyncio.sleep, log=None):
        self.tg, self.amide, self.state, self.queue, self.config = tg, amide, state, queue, config
        self.now, self.sleep, self.log = now, sleep, log or logging.getLogger("watcher")
        self.paused = False
        self._failures = 0
        self._next_attempt: datetime | None = None

    # ------------------------------------------------------------ one poll

    async def poll_once(self) -> None:
        try:
            watch = await self._register_and_list()
        except AmideAuthError:
            self._pause()
            return
        except AmideUnavailable as exc:
            self.log.warning("Amide is not reachable (%s); will try again", exc)
            await self._flush()
            return
        except Exception as exc:
            self.log.error("Telegram problem while listing groups (%s)", type(exc).__name__)
            return
        self.paused = False
        await self._flush()
        for chat_id in watch:
            if self.queue.full():
                self.log.warning("The delivery queue is full; not reading more until it drains")
                break
            try:
                await self._read_group(chat_id)
            except Exception as exc:                                       # one group failing never stops the others
                self.log.error("Could not read group %s (%s); its position was not moved", chat_id, type(exc).__name__)
            await self.sleep(PAUSE_BETWEEN_GROUPS)
        await self._flush()
        for chat_id in watch:
            try:
                await self._check_gone(chat_id)
            except AmideAuthError:
                self._pause()
                return
            except Exception as exc:
                self.log.error("Gone check failed for %s (%s)", chat_id, type(exc).__name__)

    def _pause(self) -> None:
        if not self.paused:
            self.log.error("Amide refused the token. Delivery is paused; create a new token in Amide and update config.toml.")
        self.paused = True

    async def _register_and_list(self) -> list[str]:
        for group in await self.tg.list_groups():
            await self.amide.register(group.chat_id, group.title)
        return await self.amide.list_sources()

    # ------------------------------------------------------------ reading

    async def _read_group(self, chat_id: str) -> None:
        st = self.state.chat(chat_id)
        now = self.now()
        if st.last_id == 0:
            if st.started_at is None:
                st.started_at = (now - timedelta(days=self.config.backfill_days)).isoformat()
                self.state.save()
            messages = await self.tg.messages_since(chat_id, 0, datetime.fromisoformat(st.started_at))
        else:
            messages = await self.tg.messages_since(chat_id, st.last_id, None)
        for batch in self._batches(messages):
            if self._unsettled(batch, now):
                break
            payload = await self._build(batch)
            if payload is not None:
                self.queue.put(payload)
            st.last_id = batch[-1].message_id                              # only after the message is safely in the queue
            self.state.save()
            if self.queue.full():
                break

    @staticmethod
    def _batches(messages: list[TgMessage]) -> list[list[TgMessage]]:
        batches: list[list[TgMessage]] = []
        for message in messages:
            if message.grouped_id and batches and batches[-1][0].grouped_id == message.grouped_id:
                batches[-1].append(message)
            else:
                batches.append([message])
        return batches

    @staticmethod
    def _unsettled(batch: list[TgMessage], now: datetime) -> bool:
        return bool(batch[0].grouped_id) and (now - max(m.date for m in batch)).total_seconds() < ALBUM_SETTLE_SECONDS

    async def _build(self, batch: list[TgMessage]) -> Payload | None:
        limit = self.config.max_file_mb * 1024 * 1024
        files, texts = [], []
        for message in batch:
            if message.text.strip():
                texts.append(message.text.strip())
            for attachment in message.attachments:
                if attachment.size > limit:
                    self.log.info("Skipped a file over the size limit in message %s", message.message_id)
                    continue
                files.append((attachment.filename, await self.tg.download(message, attachment)))
        if not texts and not files:
            return None
        first = batch[0]
        return Payload(chat_id=first.chat_id, message_id=str(first.message_id), album_id=str(first.grouped_id) if first.grouped_id else None,
                       date=first.date.astimezone(timezone.utc).isoformat(), text="\n".join(texts), files=files)

    # ------------------------------------------------------------ delivering

    async def _flush(self) -> None:
        while (item := self.queue.oldest()) is not None:
            if self.paused:
                try:
                    await self.amide.list_sources()                       # a cheap check whether the token works again
                    self.paused = False
                except (AmideAuthError, AmideUnavailable):
                    return
            if self._next_attempt is not None and self.now() < self._next_attempt:
                return
            outcome = await self.amide.send(item.payload())
            if outcome.kind in ("delivered", "drop"):
                if outcome.kind == "drop":
                    self.log.warning("Amide refused message %s (%s); dropped", item.payload().message_id, outcome.detail)
                self.queue.remove(item)
                self._failures, self._next_attempt = 0, None
            elif outcome.kind == "auth":
                self._pause()
                return
            else:
                delay = BACKOFF[min(self._failures, len(BACKOFF) - 1)]
                self._failures += 1
                self._next_attempt = self.now() + timedelta(seconds=delay)
                self.log.warning("Could not deliver to Amide (%s); retrying in %s s", outcome.detail, delay)
                return

    # ------------------------------------------------------------ the gone check

    async def _check_gone(self, chat_id: str) -> None:
        st = self.state.chat(chat_id)
        access = await self.tg.check_access(chat_id)
        if access.state == "unknown":
            return
        if access.state == "ok":
            st.strikes = 0
            if st.gone and await self.amide.report_state(chat_id, "active", None):
                st.gone = False
        else:
            st.strikes += 1
            if st.strikes >= STRIKES_TO_REPORT and not st.gone and await self.amide.report_state(chat_id, "gone", access.reason or None):
                st.gone = True
        self.state.save()

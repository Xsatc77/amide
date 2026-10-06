"""Stand-ins for Telegram and Amide so the runner is tested without a network."""

from watcher.amide_client import AmideAuthError, AmideUnavailable, Delivery, Source
from watcher.ports import Access


class FakeTelegram:
    def __init__(self, groups=(), messages=None, downloads=None, access=None, fail_downloads=False, connect_fails=0):
        self.groups, self.messages, self.downloads = list(groups), dict(messages or {}), dict(downloads or {})
        self.access, self.fail_downloads = {k: iter(v) for k, v in (access or {}).items()}, fail_downloads
        self.calls: list[tuple] = []
        self.detailed: list[tuple] = []
        self.connect_fails, self.connect_checks = connect_fails, 0

    async def ensure_connected(self):
        self.connect_checks += 1
        if self.connect_fails > 0:
            self.connect_fails -= 1
            raise ConnectionError("offline")

    async def list_groups(self):
        self.calls.append(("list_groups",))
        return list(self.groups)

    async def messages_since(self, chat_id, min_id, since, topic_id=None):
        self.calls.append(("messages_since", chat_id) if topic_id is None else ("messages_since", chat_id, topic_id))
        self.detailed.append(("messages_since", chat_id, min_id))
        rows = self.messages.get(chat_id, [])
        if topic_id is not None:
            rows = [m for m in rows if m.topic_id == topic_id]
        if min_id > 0:
            return [m for m in rows if m.message_id > min_id]
        return [m for m in rows if since is None or m.date >= since]

    async def download(self, message, attachment):
        self.calls.append(("download", message.chat_id))
        if self.fail_downloads:
            raise OSError("connection reset")
        return self.downloads.get((message.chat_id, message.message_id, attachment.filename), b"x" * min(attachment.size, 64))

    async def check_access(self, chat_id):
        self.calls.append(("check_access", chat_id))
        return next(self.access.get(chat_id, iter([])), Access("ok"))


class FakeAmide:
    def __init__(self, watch=(), outcomes=(), state_ok=(), register_busy=0):
        self.watch = [w if isinstance(w, Source) else Source(w, None) for w in watch]
        self.outcomes, self.state_ok = iter(outcomes), iter(state_ok)
        self.registered, self.sent, self.states = [], [], []
        self.attempts = self.auth_checks = 0
        self.register_busy = register_busy

    async def register(self, chat_id, title, topics=None):
        if self.register_busy > 0:
            self.register_busy -= 1
            raise AmideUnavailable("HTTP 429")
        self.registered.append((chat_id, title) if not topics else (chat_id, title, [(t.topic_id, t.title) for t in topics]))

    async def list_sources(self):
        self.auth_checks += 1
        return list(self.watch)

    async def report_state(self, chat_id, state, reason=None):
        self.states.append((chat_id, state, reason))
        return next(self.state_ok, True)

    async def send(self, payload):
        self.attempts += 1
        outcome = next(self.outcomes, Delivery("delivered"))
        if outcome.kind == "delivered":
            self.sent.append(payload)
        return outcome

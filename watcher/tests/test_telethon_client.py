import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from telethon import types

from watcher.telethon_client import TelethonClient


def client_with(fake) -> TelethonClient:
    c = TelethonClient.__new__(TelethonClient)
    c._client = fake
    return c


class FakeTg:
    def __init__(self, messages=(), entity=None, error=None):
        self.messages, self.entity, self.error = list(messages), entity, error

    def iter_messages(self, entity, **kwargs):
        async def gen():
            for m in self.messages:
                yield m
        return gen()

    async def get_input_entity(self, chat_id):
        return chat_id

    async def get_entity(self, chat_id):
        if self.error:
            raise self.error
        return self.entity

    async def get_messages(self, entity, limit=1):
        return []


def message(**kw):
    base = dict(id=1, date=datetime(2026, 10, 6, tzinfo=timezone.utc), message="", grouped_id=None, photo=None, document=None, media=None,
                file=SimpleNamespace(name="a.pdf", size=10, mime_type="application/pdf"))
    return SimpleNamespace(**{**base, **kw})


def run(coro):
    return asyncio.run(coro)


def test_link_previews_and_service_messages_are_not_taken_as_price_list_files():
    preview = message(id=1, photo=object(), media=types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=1)))
    service = types.MessageService.__new__(types.MessageService)
    service.__dict__.update(message(id=2, photo=object()).__dict__)
    real = message(id=3, photo=object(), media=types.MessageMediaPhoto(photo=None))
    doc = message(id=4, document=object(), media=types.MessageMediaDocument())
    got = run(client_with(FakeTg([preview, service, real, doc])).messages_since("-100", 0, datetime(2026, 1, 1, tzinfo=timezone.utc)))
    assert [(m.message_id, [a.kind for a in m.attachments]) for m in got] == [(1, []), (3, ["image"]), (4, ["pdf"])]


def test_a_basic_group_that_was_upgraded_or_left_is_gone_and_a_normal_one_is_ok():
    def chat(**kw):
        return types.Chat(id=1, title="x", photo=types.ChatPhotoEmpty(), participants_count=1, date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                          version=1, **kw)
    for entity in (chat(deactivated=True), chat(left=True), types.ChatForbidden(id=1, title="x")):
        assert run(client_with(FakeTg(entity=entity)).check_access("-1")).state == "gone"
    assert run(client_with(FakeTg(entity=chat())).check_access("-1")).state == "ok"
    assert run(client_with(FakeTg(error=ValueError("unknown"))).check_access("-1")).state == "unknown"

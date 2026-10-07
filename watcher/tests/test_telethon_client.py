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


def test_forum_groups_list_their_topics_and_per_topic_reads_pass_the_topic_to_telegram():
    from telethon.tl.types import ForumTopic, ForumTopicDeleted

    class Topics:
        def __init__(self):
            self.topics = [SimpleNamespace(id=7, title="US warehouse"), SimpleNamespace(id=1, title="General")]

    class Fake(FakeTg):
        def __init__(self):
            super().__init__()
            self.kwargs = None

        def iter_dialogs(self):
            async def gen():
                yield SimpleNamespace(id=-100, name="Acme group", is_group=True, is_channel=True, entity=SimpleNamespace(forum=True))
                yield SimpleNamespace(id=-200, name="Plain group", is_group=True, is_channel=False, entity=SimpleNamespace(forum=False))
            return gen()

        async def __call__(self, request):
            return Topics()

        def iter_messages(self, entity, **kwargs):
            self.kwargs = kwargs
            return super().iter_messages(entity, **kwargs)

    fake = Fake()
    client = client_with(fake)
    groups = run(client.list_groups())
    assert [(g.chat_id, [(t.topic_id, t.title) for t in g.topics]) for g in groups] == [("-100", [("7", "US warehouse"), ("1", "General")]), ("-200", [])]
    run(client.messages_since("-100", 5, None, "7"))
    assert fake.kwargs.get("reply_to") == 7 and fake.kwargs.get("min_id") == 5
    run(client.messages_since("-100", 0, datetime(2026, 1, 1, tzinfo=timezone.utc)))
    assert "reply_to" not in fake.kwargs


def test_more_than_a_hundred_topics_are_all_listed_and_the_list_is_cached_and_kept_on_errors():
    class Page:
        def __init__(self, ids):
            self.topics = [SimpleNamespace(id=i, title=f"T{i}", date=datetime(2026, 1, 1, tzinfo=timezone.utc), top_message=i) for i in ids]

    class Fake(FakeTg):
        def __init__(self):
            super().__init__()
            self.requests, self.fail = 0, False

        def iter_dialogs(self):
            async def gen():
                yield SimpleNamespace(id=-100, name="Big forum", is_group=True, is_channel=True, entity=SimpleNamespace(forum=True))
            return gen()

        async def __call__(self, request):
            self.requests += 1
            if self.fail:
                raise ConnectionError("down")
            return Page(range(1, 101)) if request.offset_topic == 0 else Page(range(101, 151)) if request.offset_topic == 100 else Page([])

    fake = Fake()
    client = client_with(fake)
    groups = run(client.list_groups())
    assert len(groups[0].topics) == 150
    used = fake.requests
    run(client.list_groups())
    assert fake.requests == used                                           # cached: not asked again every poll
    client._topic_cache.clear()
    fake.fail = True
    client._topic_cache["-100"] = (0.0, groups[0].topics)                  # an old copy, expired
    again = run(client.list_groups())
    assert len(again[0].topics) == 150                                     # an error keeps the last known list

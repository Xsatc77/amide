import asyncio
import logging

from watcher import cli
from watcher.config import Config


def config(tmp_path):
    return Config(amide_url="http://x", amide_token="test-token", telegram_api_id=1234, telegram_api_hash="fakehash", home=tmp_path)


class FakeClient:
    """Signed out for the first two connections, signed in on the third (as if someone ran `login` in a console meanwhile)."""
    instances: list = []

    def __init__(self, config):
        self.index = len(FakeClient.instances)
        FakeClient.instances.append(self)
        self.closed = False

    async def connect(self):
        pass

    async def is_authorized(self):
        return self.index >= 2

    async def close(self):
        self.closed = True


def test_serve_waits_for_the_login_with_a_fresh_connection_each_time(tmp_path, caplog):
    FakeClient.instances = []
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    log = logging.getLogger("serve-test")
    with caplog.at_level(logging.WARNING, logger="serve-test"):
        tg = asyncio.run(cli._signed_in_client(config(tmp_path), log, make_client=FakeClient, sleep=fake_sleep))
    assert tg is FakeClient.instances[2] and sleeps == [30, 30]
    assert FakeClient.instances[0].closed and FakeClient.instances[1].closed and not tg.closed
    assert sum("python -m watcher login" in r.message for r in caplog.records) == 1       # asked once, not every 30 seconds
    for secret in ("test-token", "fakehash", "1234"):
        assert secret not in caplog.text


class FlakyClient(FakeClient):
    """Telegram unreachable on the first connection, then signed in."""

    async def connect(self):
        if self.index == 0:
            raise OSError("no network")

    async def is_authorized(self):
        return True


def test_serve_keeps_retrying_when_telegram_is_unreachable_and_never_raises(tmp_path):
    FakeClient.instances = []

    async def no_sleep(seconds):
        pass

    tg = asyncio.run(cli._signed_in_client(config(tmp_path), logging.getLogger("serve-test"), make_client=FlakyClient, sleep=no_sleep))
    assert tg is FakeClient.instances[1]


def test_serve_is_a_command():
    import pytest
    with pytest.raises(SystemExit) as exit_:
        cli.main(["serve", "--help"])
    assert exit_.value.code == 0

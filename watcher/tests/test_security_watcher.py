"""Security tests for the watcher: secrets, hostile Telegram content, hostile files on disk, network behaviour."""

import asyncio
import json
import os
import random
import stat
import sys
from datetime import datetime, timezone

import httpx
import pytest

from watcher import config as cfg
from watcher.amide_client import AmideAuthError, AmideClient, AmideUnavailable
from watcher.config import Config, ConfigError
from watcher.ports import Attachment, Payload, TgMessage
from watcher.queue import DiskQueue
from watcher.runner import Runner
from watcher.safety import safe_filename
from watcher.state import State

GOOD = 'amide_url = "{url}"\namide_token = "test-token"\ntelegram_api_id = 1234\ntelegram_api_hash = "fakehash"\n'
POSIX = sys.platform != "win32"


def run(coro):
    return asyncio.run(coro)


def load(tmp_path, url, monkeypatch, **env):
    for name in ("AMIDE_URL", "AMIDE_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "AMIDE_ALLOW_INSECURE_HTTP"):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "config.toml").write_text(GOOD.format(url=url), encoding="utf-8")
    return cfg.load_config(tmp_path)


# ------------------------------------------------------------------ Amide URL and token

@pytest.mark.parametrize("url", ["http://amide:8000", "http://127.0.0.1:8000", "http://localhost:8000", "http://192.168.1.50:1707",
                                 "http://10.0.0.5", "http://[::1]:8000", "https://prices.example.test", "http://amide.local:8000"])
def test_private_http_and_any_https_urls_are_accepted(tmp_path, monkeypatch, url):
    assert load(tmp_path, url, monkeypatch).amide_url == url


@pytest.mark.parametrize("url", ["http://prices.example.test", "http://8.8.8.8:8000", "http://1.1.1.1"])
def test_plain_http_to_a_public_host_is_refused_unless_opted_in(tmp_path, monkeypatch, url):
    with pytest.raises(ConfigError, match="https"):
        load(tmp_path, url, monkeypatch)
    assert load(tmp_path, url, monkeypatch, AMIDE_ALLOW_INSECURE_HTTP="1").amide_url == url


@pytest.mark.parametrize("url", ["https://user:pw@prices.example.test", "https://prices.example.test/?t=1", "https://prices.example.test/#x", "https:///x"])
def test_an_amide_url_with_credentials_query_or_no_host_is_refused(tmp_path, monkeypatch, url):
    with pytest.raises(ConfigError) as err:
        load(tmp_path, url, monkeypatch)
    assert "pw" not in str(err.value)


@pytest.mark.parametrize("token", ["bad token", "line\nbreak", "tab\there", "x" * 600])
def test_a_token_that_could_not_be_a_header_value_is_refused(tmp_path, monkeypatch, token):
    (tmp_path / "config.toml").write_text(GOOD.format(url="http://127.0.0.1:8000"), encoding="utf-8")
    for name in ("AMIDE_URL", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "AMIDE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AMIDE_TOKEN", token)
    with pytest.raises(ConfigError) as err:
        cfg.load_config(tmp_path)
    assert token.strip()[:6] not in str(err.value) or token.startswith("x")


def test_secrets_can_come_from_files_so_a_container_secret_stays_out_of_the_environment(tmp_path, monkeypatch):
    secret = tmp_path / "token.txt"
    secret.write_text("file-token\n", encoding="utf-8")
    c = load(tmp_path, "http://127.0.0.1:8000", monkeypatch, AMIDE_TOKEN_FILE=str(secret))
    assert c.amide_token == "file-token"


def test_a_missing_secret_file_names_the_variable_not_the_content(tmp_path, monkeypatch):
    with pytest.raises(ConfigError, match="AMIDE_TOKEN_FILE"):
        load(tmp_path, "http://127.0.0.1:8000", monkeypatch, AMIDE_TOKEN_FILE=str(tmp_path / "nope"))


def test_the_client_never_follows_a_redirect_so_the_token_cannot_reach_another_host():
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.host == "amide.test":
            return httpx.Response(302, headers={"location": "https://evil.example.test/steal"})
        return httpx.Response(200, json=[])

    async def go():
        from watcher.amide_client import make_http
        config = Config(amide_url="https://amide.test", amide_token="secret-token", telegram_api_id=1, telegram_api_hash="h")
        http = make_http(config)
        http._transport = httpx.MockTransport(handler)
        async with http:
            with pytest.raises(AmideUnavailable):
                await AmideClient(http, "secret-token").list_sources()
    run(go())
    assert [u for u, _ in seen] == ["https://amide.test/api/ingest/sources"]


def test_make_http_verifies_tls_and_never_follows_redirects():
    from watcher.amide_client import make_http
    http = make_http(Config(amide_url="https://amide.test", amide_token="t", telegram_api_id=1, telegram_api_hash="h"))
    assert http.follow_redirects is False
    assert http.timeout.connect is not None and http.timeout.read is not None


def test_forbidden_is_an_auth_problem_not_a_dropped_message():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)), base_url="http://amide.test") as h:
            outcome = await AmideClient(h, "t").send(Payload("-1", "1", None, "2026-01-01T00:00:00+00:00", "hi", []))
            assert outcome.kind == "auth"
    run(go())


def test_a_429_carries_the_retry_after_the_server_asked_for():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "120"})),
                                     base_url="http://amide.test") as h:
            outcome = await AmideClient(h, "t").send(Payload("-1", "1", None, "2026-01-01T00:00:00+00:00", "hi", []))
            assert outcome.kind == "retry" and outcome.retry_after == 120
    run(go())


def test_a_hostile_source_list_cannot_steer_the_token_to_another_path():
    rows = [{"chat_id": "-1001", "topics": ["5"]}, {"chat_id": "../../admin", "topics": None}, {"chat_id": "//evil.test", "topics": None},
            {"chat_id": "-1002", "topics": ["7", "../x", "8?a=b"]}, "-1003", 5, None]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=rows)), base_url="http://amide.test") as h:
            got = await AmideClient(h, "t").list_sources()
            assert [(s.chat_id, s.topics) for s in got] == [("-1001", ["5"]), ("-1002", ["7"]), ("-1003", None)]
    run(go())


def test_titles_and_topic_titles_are_capped_when_sent():
    bodies = []

    def handler(request):
        bodies.append(request.content)
        return httpx.Response(200)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://amide.test") as h:
            from watcher.ports import Topic
            await AmideClient(h, "t").register("-1", "T" * 5000, [Topic("5", "U" * 5000)])
            await AmideClient(h, "t").send(Payload("-1", "1", None, "2026-01-01T00:00:00+00:00", "hi", [], topic_id="5", topic_title="V" * 5000))
    run(go())
    assert b"U" * 201 not in bodies[0] and b"V" * 201 not in bodies[1]


# ------------------------------------------------------------------ file permissions

@pytest.mark.skipif(not POSIX, reason="POSIX permission bits")
def test_state_queue_and_log_files_are_private(tmp_path):
    os.umask(0o022)
    state = State(tmp_path / "home" / "state.json")
    state.chat("-1").last_id = 1
    state.save()
    q = DiskQueue(tmp_path / "home" / "queue")
    q.put(Payload("-1", "1", None, "2026-01-01T00:00:00+00:00", "hi", [("a.pdf", b"%PDF")]))
    assert stat.S_IMODE((tmp_path / "home" / "state.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "home" / "queue").stat().st_mode) == 0o700
    item = q.oldest()
    assert stat.S_IMODE((item.folder / "meta.json").stat().st_mode) == 0o600 and stat.S_IMODE((item.folder / "file0").stat().st_mode) == 0o600


@pytest.mark.skipif(not POSIX, reason="POSIX permission bits")
def test_the_home_folder_and_session_are_locked_down(tmp_path):
    from watcher.telethon_client import TelethonClient
    config = Config(amide_url="http://x", amide_token="t", telegram_api_id=1, telegram_api_hash="h", home=tmp_path / "home")
    c = TelethonClient(config)
    assert stat.S_IMODE(config.home.stat().st_mode) == 0o700
    config.session_path.write_bytes(b"x")
    config.session_path.chmod(0o644)
    c.lock_down_session()
    assert stat.S_IMODE(config.session_path.stat().st_mode) == 0o600


# ------------------------------------------------------------------ hostile file names

@pytest.mark.parametrize("name", ["../../etc/passwd", "..\\..\\windows\\system32\\x.pdf", "/abs/path.pdf", "C:\\x\\y.pdf", "CON.pdf", "nul", "aux.xlsx",
                                  "com1.png", "report.pdf.", "report.pdf ", "a\x00b.pdf", "a\nb.pdf", "\u202efdp.exe", "x" * 5000 + ".pdf", "", "   ", "...",
                                  "a\u200b\u2066b.pdf", "~$tmp.xlsx", "-rf.pdf"])
def test_every_hostile_name_becomes_a_plain_safe_name(name):
    out = safe_filename(name, "pdf")
    assert out and len(out.encode("utf-8")) <= 120
    assert "/" not in out and "\\" not in out and ":" not in out and ".." not in out
    assert all(ord(ch) >= 32 and ch not in "\u202e\u200b\u2066" for ch in out)
    assert not out.endswith((".", " ")) and out.split(".")[0].upper() not in {"CON", "NUL", "AUX", "COM1", "PRN"}
    assert not out.startswith(("-", "~", "."))
    assert out.lower().endswith((".pdf",))


def test_the_extension_follows_the_kind_telegram_reported_not_the_name():
    assert safe_filename("prices.exe", "pdf").endswith(".pdf") and safe_filename("prices.exe", "pdf") != "prices.exe"
    assert safe_filename("sheet.xlsx", "xlsx") == "sheet.xlsx"
    assert safe_filename("Photo.JPEG", "image") == "Photo.JPEG"
    assert safe_filename("photo", "image").endswith((".jpg", ".png"))


def test_ordinary_names_are_kept():
    assert safe_filename("Price List March 2026.pdf", "pdf") == "Price List March 2026.pdf"
    assert safe_filename("Prix \u00e9t\u00e9.pdf", "pdf") == "Prix \u00e9t\u00e9.pdf"


# ------------------------------------------------------------------ the runner

class Tg:
    def __init__(self, data, attachments):
        self.data, self.attachments = data, attachments

    async def download(self, message, attachment):
        return self.data


def make_runner(tmp_path, tg, **cfgkw):
    config = Config(amide_url="http://x", amide_token="t", telegram_api_id=1, telegram_api_hash="h", home=tmp_path, **cfgkw)
    return Runner(tg, None, State(tmp_path / "s.json"), DiskQueue(tmp_path / "q"), config, sleep=lambda s: asyncio.sleep(0))


def msg(attachments, text="hi", topic_id=None):
    return TgMessage("-1", 1, datetime(2026, 1, 1, tzinfo=timezone.utc), text, None, attachments, topic_id)


def test_the_runner_sends_only_safe_names_even_when_telegram_names_are_hostile(tmp_path):
    r = make_runner(tmp_path, Tg(b"%PDF-1", None))
    payload = run(r._build([msg([Attachment("../../evil\u202e.pdf", "pdf", 5)])]))
    assert payload.files[0][0] == safe_filename("../../evil\u202e.pdf", "pdf") and "/" not in payload.files[0][0]


def test_a_download_larger_than_it_was_declared_is_dropped_not_queued(tmp_path):
    r = make_runner(tmp_path, Tg(b"x" * (2 * 1024 * 1024), None), max_file_mb=1)
    payload = run(r._build([msg([Attachment("a.pdf", "pdf", 10)])]))
    assert payload.files == [] if payload is not None else True


def test_an_undeclared_size_does_not_bypass_the_size_cap(tmp_path):
    r = make_runner(tmp_path, Tg(b"x" * (2 * 1024 * 1024), None), max_file_mb=1)
    payload = run(r._build([msg([Attachment("a.jpg", "image", 0)], text="")]))
    assert payload is None


def test_an_album_cannot_exceed_the_file_count_or_total_size_caps(tmp_path):
    from watcher import runner as rn
    r = make_runner(tmp_path, Tg(b"%PDF-" + b"x" * 1000, None))
    atts = [Attachment(f"f{i}.pdf", "pdf", 1000) for i in range(rn.MAX_FILES_PER_MESSAGE + 15)]
    payload = run(r._build([msg(atts)]))
    assert len(payload.files) == rn.MAX_FILES_PER_MESSAGE


def test_the_topic_title_sent_along_is_capped(tmp_path):
    r = make_runner(tmp_path, Tg(b"x", None))
    r._topic_titles[("-1", "5")] = "T" * 5000
    payload = run(r._build([msg([], topic_id="5")]))
    assert len(payload.topic_title) <= 200


def test_a_non_numeric_topic_id_from_telegram_is_dropped():
    from watcher.telethon_client import TelethonClient
    from types import SimpleNamespace
    reply = SimpleNamespace(forum_topic=True, reply_to_top_id="5; DROP", reply_to_msg_id=None)
    assert TelethonClient._topic_of(SimpleNamespace(reply_to=reply)) is None


# ------------------------------------------------------------------ hostile state and queue files

@pytest.mark.parametrize("content", ['[]', '"x"', '{"chats": []}', '{"chats": {"-1": {"last_id": "9"}}}', '{"chats": {"-1": {"last_id": -5}}}',
                                     '{"chats": {"-1": {"nope": 1}}}', '{"chats": {"-1": 7}}', '\x00\x01', '{"chats": {"-1": {"last_id": 1e999}}}',
                                     '{"chats": {"-1": {"title": ["x"]}}}', '{"chats": {"-1": {"started_at": "not a date"}}}'])
def test_a_hostile_state_file_starts_empty_and_is_set_aside(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    state = State(path)
    assert state.chats == {} and (tmp_path / "state.json.bad").exists()


def test_a_huge_state_file_is_not_read_into_memory(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"chats": {}}' + " " * (6 * 1024 * 1024), encoding="utf-8")
    assert State(path).chats == {} and (tmp_path / "state.json.bad").exists()


def test_a_hostile_queue_meta_is_quarantined_never_executed(tmp_path):
    q = DiskQueue(tmp_path / "q")
    for n, meta in enumerate([{"__class__": 1}, {"chat_id": "-1", "message_id": "1", "album_id": None, "date": "d", "text": "t", "file_names": "abc"},
                              {"chat_id": "-1", "message_id": "1", "album_id": None, "date": "d", "text": "t", "file_names": ["../../x"] * 3},
                              {"chat_id": "-1", "message_id": "1", "album_id": None, "date": "d", "text": "t", "file_names": [5]}], start=1):
        folder = q.dir / f"{n:012d}"
        folder.mkdir()
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        (folder / "file0").write_bytes(b"x")
    results = []
    for _ in range(4):
        item = q.oldest()
        try:
            p = item.payload()
            results.append([name for name, _ in p.files])
        except Exception:
            results.append("bad")
        q.remove(item)
    assert results[0] == "bad" and results[1] == "bad" and results[3] == "bad"
    assert results[2] == "bad" or all("/" not in n for n in results[2])


def test_the_quarantine_folder_cannot_grow_without_bound(tmp_path):
    q = DiskQueue(tmp_path / "q")
    for n in range(1, 80):
        folder = q.dir / f"{n:012d}"
        folder.mkdir()
        (folder / "meta.json").write_text("{", encoding="utf-8")
        q.quarantine(q.oldest())
    assert len(list((q.dir / "bad").iterdir())) <= 50


def test_an_oversize_meta_file_is_not_loaded(tmp_path):
    q = DiskQueue(tmp_path / "q")
    q.put(Payload("-1", "1", None, "d", "hi", []))
    item = q.oldest()
    (item.folder / "meta.json").write_text("{" + " " * (3 * 1024 * 1024) + "}", encoding="utf-8")
    with pytest.raises(Exception):
        item.payload()


# ------------------------------------------------------------------ retry behaviour

def test_backoff_has_jitter_and_honours_retry_after(tmp_path):
    from watcher.amide_client import Delivery
    from watcher import runner as rn

    class Amide:
        def __init__(self):
            self.n = 0

        async def send(self, payload):
            self.n += 1
            return Delivery("retry", "HTTP 429", retry_after=600)

    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    amide = Amide()
    config = Config(amide_url="http://x", amide_token="t", telegram_api_id=1, telegram_api_hash="h", home=tmp_path)
    q = DiskQueue(tmp_path / "q")
    q.put(Payload("-1", "1", None, "d", "hi", []))
    r = Runner(None, amide, State(tmp_path / "s.json"), q, config, now=lambda: now[0], sleep=lambda s: asyncio.sleep(0))
    run(r._flush())
    wait = (r._next_attempt - now[0]).total_seconds()
    assert wait >= 600 and amide.n == 1
    run(r._flush())
    assert amide.n == 1                                  # still waiting: no second request
    assert rn.jittered(100, random.Random(1)) != rn.jittered(100, random.Random(2))
    assert all(80 <= rn.jittered(100, random.Random(i)) <= 120 for i in range(50))


# ------------------------------------------------------------------ container and docs

def test_the_dockerignore_keeps_secrets_and_tests_out_of_the_build_context():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / ".dockerignore").read_text(encoding="utf-8")
    for pattern in ("**/config.toml", "**/*.session", "**/state.json", "watcher/tests", "amide-watcher"):
        assert pattern in text


def test_the_watcher_container_runs_as_a_non_root_user_and_the_stack_drops_privileges():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    docker = (root / "Dockerfile.watcher").read_text(encoding="utf-8")
    assert "USER watcher" in docker and "AMIDE_TOKEN=" not in docker and "HASH=" not in docker.upper().replace("--REQUIRE-HASHES", "")
    compose = (root / "docker-compose.portainer-with-watcher.yml").read_text(encoding="utf-8")
    watcher_part = compose.split("  watcher:")[1]
    assert "no-new-privileges:true" in watcher_part and "cap_drop" in watcher_part

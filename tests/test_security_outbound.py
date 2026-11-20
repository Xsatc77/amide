"""Outbound requests: SSRF guard, DNS pinning, the AI "own server" address, ntfy topics. No test touches the network."""

import io
import socket
import urllib.error
import urllib.request

import pytest

from app import ai_summary, links, netguard, reminders
from app.db import SessionLocal
from app.food import usda
from app.models import User


# ---------------------------------------------------------------- fakes

class FakeSock:
    def __init__(self, body=b"<html>hello</html>", status="200 OK", extra=""):
        self.sent = b""
        self._reply = (f"HTTP/1.1 {status}\r\nContent-Type: text/html\r\n{extra}Content-Length: {len(body)}\r\n\r\n").encode() + body

    def sendall(self, data):
        self.sent += data

    def makefile(self, *a, **k):
        return io.BytesIO(self._reply)

    def settimeout(self, *a):
        pass

    def setsockopt(self, *a):
        pass

    def close(self):
        pass


@pytest.fixture
def net(monkeypatch):
    """dns maps a host to a list of addresses; connections records the address actually dialled."""
    state = {"dns": {}, "dialled": [], "lookups": [], "sock": None}

    def getaddrinfo(host, port, *a, **k):
        state["lookups"].append(host)
        ips = state["dns"].get(host)
        if ips is None:
            try:
                socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
                ips = [host]
            except OSError:
                raise socket.gaierror("no such host")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    def create_connection(address, timeout=None, source_address=None):
        state["dialled"].append(address)
        state["sock"] = state["sock"] or FakeSock()
        return state["sock"]

    class FakeContext:
        def wrap_socket(self, sock, server_hostname=None, **k):
            state["sni"] = server_hostname
            return sock

    monkeypatch.setattr(netguard.ssl, "create_default_context", lambda *a, **k: FakeContext())
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    return state


# ---------------------------------------------------------------- netguard

@pytest.mark.parametrize("url", [
    "ftp://example.test/", "file:///etc/passwd", "http://user:pw@example.test/", "http://user@example.test/", "https://",
    "http://127.0.0.1/", "http://[::1]/", "http://0.0.0.0/", "http://169.254.169.254/latest/meta-data/", "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:169.254.169.254]/", "http://224.0.0.1/", "http://10.0.0.5/", "http://192.168.1.2/", "http://[fd00:ec2::254]/",
    "http://[64:ff9b::7f00:1]/",
])
def test_check_url_refuses_unsafe_addresses(net, url):
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url(url)


def test_private_is_allowed_only_when_asked_but_metadata_and_unspecified_never(net):
    assert netguard.check_url("http://192.168.1.5:11434/v1", allow_private=True).ip == "192.168.1.5"
    for bad in ("http://169.254.169.254/", "http://0.0.0.0/", "http://[::ffff:169.254.169.254]/", "http://224.0.0.1/"):
        with pytest.raises(netguard.UnsafeURL):
            netguard.check_url(bad, allow_private=True)
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url("http://127.0.0.1/", allow_private=True)
    assert netguard.check_url("http://127.0.0.1/", allow_private=True, allow_loopback=True).ip == "127.0.0.1"


def test_a_name_that_is_public_first_and_private_second_is_refused(net):
    net["dns"]["mixed.example.test"] = ["93.184.216.34", "10.0.0.7"]
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url("https://mixed.example.test/")


def test_safe_open_dials_the_resolved_address_once_and_keeps_the_host_header(net):
    net["dns"]["ok.example.test"] = ["93.184.216.34"]
    with netguard.safe_open(urllib.request.Request("http://ok.example.test/page?a=1"), 5) as r:
        assert r.read() == b"<html>hello</html>"
    assert net["dialled"] == [("93.184.216.34", 80)]
    assert net["lookups"].count("ok.example.test") == 1
    assert b"GET /page?a=1 HTTP/1.1" in net["sock"].sent and b"Host: ok.example.test" in net["sock"].sent


def test_a_rebinding_name_cannot_change_the_dialled_address(net):
    """The first answer is the one used even if the name answers differently later."""
    calls = []
    real = socket.getaddrinfo

    def rebinding(host, port, *a, **k):
        calls.append(host)
        ip = "93.184.216.34" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    socket.getaddrinfo = rebinding
    try:
        with netguard.safe_open(urllib.request.Request("http://rebind.example.test/"), 5) as r:
            r.read()
    finally:
        socket.getaddrinfo = real
    assert net["dialled"] == [("93.184.216.34", 80)] and len(calls) == 1


def test_redirects_are_not_followed_and_an_oversize_answer_is_cut(net):
    net["dns"]["r.example.test"] = ["93.184.216.34"]
    net["sock"] = FakeSock(b"", "302 Found", "Location: http://169.254.169.254/\r\n")
    with pytest.raises(urllib.error.HTTPError) as err:
        netguard.safe_open(urllib.request.Request("http://r.example.test/"), 5)
    assert err.value.headers["Location"] == "http://169.254.169.254/"
    net["sock"] = FakeSock(b"x" * 100)
    with netguard.safe_open(urllib.request.Request("http://r.example.test/"), 5, max_bytes=10) as r:
        with pytest.raises(netguard.UnsafeURL):
            r.read()


def test_plain_http_to_a_public_host_is_refused_when_https_is_required(net):
    net["dns"]["pub.example.test"] = ["93.184.216.34"]
    with pytest.raises(netguard.UnsafeURL):
        netguard.safe_open(urllib.request.Request("http://pub.example.test/t"), 5, allow_private=True, https_for_public=True)
    net["dns"]["lan.example.test"] = ["192.168.1.9"]
    netguard.safe_open(urllib.request.Request("http://lan.example.test/t"), 5, allow_private=True, https_for_public=True).close()


# ---------------------------------------------------------------- links (S-05)

def test_fetch_page_uses_the_first_resolved_address(net):
    net["dns"]["site.example.test"] = ["93.184.216.34"]
    assert links.fetch_page("http://site.example.test/") == "<html>hello</html>"
    assert net["dialled"] == [("93.184.216.34", 80)]


def test_fetch_page_refuses_a_host_that_resolves_public_then_private(net):
    net["dns"]["mixed.example.test"] = ["93.184.216.34", "192.168.1.5"]
    assert links.fetch_page("http://mixed.example.test/") is None
    assert net["dialled"] == []


def test_fetch_page_refuses_userinfo_and_a_redirect_to_a_private_address(net):
    net["dns"]["site.example.test"] = ["93.184.216.34"]
    assert links.fetch_page("http://user:pw@site.example.test/") is None
    net["sock"] = FakeSock(b"", "302 Found", "Location: http://10.0.0.5/admin\r\n")
    assert links.fetch_page("http://site.example.test/") is None
    assert net["dialled"] == [("93.184.216.34", 80)]


# ---------------------------------------------------------------- AI agent (S-04, N-01)

@pytest.mark.parametrize("base", [
    "http://192.168.1.5:11434/v1?x=", "http://192.168.1.5/v1#frag", "http://user:pw@192.168.1.5/v1", "http://192.168.1.5/v1?", "ftp://x.test/v1",
])
def test_ai_base_url_error_refuses_tricks(base):
    assert ai_summary.base_url_error(base)


def test_ai_base_url_error_accepts_a_normal_address():
    assert ai_summary.base_url_error("http://192.168.1.5:11434/v1") is None
    assert ai_summary.base_url_error("https://ollama.example.test/v1") is None


def _choose(client, **form):
    return client.post("/settings/ai", data=form, follow_redirects=False)


@pytest.fixture
def clean_ai(client, me):
    yield
    with SessionLocal() as s:
        u = s.get(User, me)
        u.ai_provider = u.ai_model = u.ai_base_url = u.ai_api_key = None
        s.commit()


def test_saving_a_base_with_a_query_or_userinfo_is_refused(client, clean_ai):
    for base in ("http://192.168.1.5:11434/v1?x=", "http://192.168.1.5/v1#a", "http://me:pw@192.168.1.5/v1"):
        assert _choose(client, ai_provider="local", ai_model="m", ai_base_url=base).status_code == 422
    assert _choose(client, ai_provider="local", ai_model="m", ai_base_url="http://192.168.1.5:11434/v1").status_code == 303


def test_a_saved_base_ending_in_a_query_is_refused_at_send_time(client, me, clean_ai, monkeypatch):
    sent = []
    monkeypatch.setattr(ai_summary, "post_json", lambda *a: sent.append(a) or {})
    with SessionLocal() as s:
        u = s.get(User, me)
        u.ai_provider, u.ai_model, u.ai_base_url = "local", "m", "http://192.168.1.5:11434/v1?x="
        s.commit()
        with pytest.raises(ai_summary.AiError):
            ai_summary.ask(u, "hello")
    assert sent == []


def test_the_local_allowed_hosts_list_pins_the_address(client, me, clean_ai, monkeypatch):
    seen = []
    monkeypatch.setattr(ai_summary, "post_json", lambda url, h, b: seen.append(url) or {"choices": [{"message": {"content": "ok"}}]})
    with SessionLocal() as s:
        u = s.get(User, me)
        u.ai_provider, u.ai_model, u.ai_base_url = "local", "m", "http://192.168.1.5:11434/v1"
        s.commit()
        monkeypatch.setenv("AMIDE_AI_LOCAL_ALLOWED_HOSTS", "ollama.example.test, 10.0.0.0/8")
        with pytest.raises(ai_summary.AiError):
            ai_summary.ask(u, "hello")
        monkeypatch.setenv("AMIDE_AI_LOCAL_ALLOWED_HOSTS", "ollama.example.test, 192.168.1.0/24")
        assert ai_summary.ask(u, "hello") == "ok"
        monkeypatch.delenv("AMIDE_AI_LOCAL_ALLOWED_HOSTS")
        assert ai_summary.ask(u, "hello") == "ok"
    assert seen == ["http://192.168.1.5:11434/v1/chat/completions"] * 2


@pytest.mark.parametrize("url,loopback_env,ok", [
    ("http://169.254.169.254/v1/chat/completions", None, False),
    ("http://127.0.0.1:11434/v1/chat/completions", None, False),
    ("http://127.0.0.1:11434/v1/chat/completions", "1", True),
    ("http://10.1.2.3:11434/v1/chat/completions", None, True),
    ("http://0.0.0.0:11434/v1/chat/completions", "1", False),
])
def test_post_json_applies_the_guard_for_a_local_server(net, monkeypatch, url, loopback_env, ok):
    if loopback_env:
        monkeypatch.setenv("AMIDE_AI_ALLOW_LOOPBACK", loopback_env)
    else:
        monkeypatch.delenv("AMIDE_AI_ALLOW_LOOPBACK", raising=False)
    net["sock"] = FakeSock(b'{"choices": []}')
    if ok:
        assert ai_summary.post_json(url, {}, {"a": 1}) == {"choices": []}
    else:
        with pytest.raises(ai_summary.AiError):
            ai_summary.post_json(url, {}, {"a": 1})
        assert net["dialled"] == []


def test_hosted_providers_may_not_reach_a_private_address(net):
    net["dns"]["api.openai.com"] = ["10.0.0.9"]
    with pytest.raises(ai_summary.AiError):
        ai_summary.post_json("https://api.openai.com/v1/chat/completions", {}, {})
    assert net["dialled"] == []


def test_the_summary_html_escapes_the_models_answer():
    out = str(ai_summary.to_html("## <script>alert(1)</script>\n- <img src=x onerror=alert(1)> **bold**\nplain <b>x</b>"))
    assert "<script>" not in out and "<img" not in out and "<b>x</b>" not in out
    assert "&lt;script&gt;" in out and "<strong>bold</strong>" in out


# ---------------------------------------------------------------- ntfy (N-07)

def test_a_new_topic_needs_twelve_characters(client, me, db):
    assert client.post("/settings/reminders", data={"ntfy_enabled": "1", "ntfy_topic": "short-one"}).status_code == 422
    assert client.post("/settings/reminders", data={"ntfy_enabled": "1", "ntfy_topic": "has spaces here!"}).status_code == 422
    r = client.post("/settings/reminders", data={"ntfy_enabled": "1", "ntfy_topic": "long-enough-topic-1"}, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        s.get(User, me).ntfy_enabled, s.get(User, me).ntfy_topic = False, None
        s.commit()


def test_an_old_short_topic_keeps_working_and_the_page_warns(client, me, db):
    with SessionLocal() as s:
        u = s.get(User, me)
        u.ntfy_enabled, u.ntfy_topic = True, "oldtopic1"
        s.commit()
    page = client.get("/settings").text
    assert "oldtopic1" in page and "too short" in page.lower()
    assert client.post("/settings/reminders", data={"ntfy_enabled": "1", "ntfy_topic": "oldtopic1"}, follow_redirects=False).status_code == 303
    with SessionLocal() as s:
        u = s.get(User, me)
        u.ntfy_enabled, u.ntfy_topic = False, None
        s.commit()
    assert "too short" not in client.get("/settings").text.lower()


def test_the_page_offers_a_random_suggestion_and_explains_public_topics(client, db):
    import re
    page = client.get("/settings").text
    m = re.search(r'data-ntfy-suggestion="([A-Za-z0-9_-]{24})"', page)
    assert m
    assert "anyone who knows the name" in page
    assert m.group(1) not in client.get("/settings").text      # a fresh suggestion every time


def test_send_ntfy_refuses_unsafe_servers_and_never_crashes_the_loop(net):
    for server in ("http://user:pw@ntfy.example.test", "http://169.254.169.254", "ftp://x.test", "http://93.184.216.34"):
        with pytest.raises(OSError):
            reminders.send_ntfy(server, "topic-twelve-chars", "t", "m")
    assert net["dialled"] == []
    net["dns"]["ntfy.example.test"] = ["93.184.216.34"]
    net["sock"] = FakeSock(b"{}")
    reminders.send_ntfy("https://ntfy.example.test", "topic-twelve-chars", "t", "m")
    assert net["dialled"] == [("93.184.216.34", 443)] and net["sni"] == "ntfy.example.test"


def test_send_ntfy_posts_to_a_lan_server_over_http(net):
    net["sock"] = FakeSock(b"{}")
    reminders.send_ntfy("http://192.168.1.9:8080", "topic-twelve-chars", "Hi", "msg")
    assert net["dialled"] == [("192.168.1.9", 8080)] and b"POST /topic-twelve-chars HTTP/1.1" in net["sock"].sent


def test_run_once_survives_a_bad_server(db, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(reminders.config, "NTFY_SERVER", "http://user:pw@ntfy.example.test")
    reminders.run_once(SessionLocal, datetime.now(timezone.utc))


# ---------------------------------------------------------------- USDA

def test_usda_fetch_goes_through_the_guard_and_never_leaks_the_key(net):
    net["dns"]["api.nal.usda.gov"] = ["10.0.0.5"]
    with pytest.raises(OSError) as err:
        usda.fetch_json("https://api.nal.usda.gov/fdc/v1/foods/search?api_key=SECRETKEY123")
    assert "SECRETKEY123" not in str(err.value) and net["dialled"] == []
    net["dns"]["api.nal.usda.gov"] = ["93.184.216.34"]
    net["sock"] = FakeSock(b'{"foods": []}')
    assert usda.fetch_json("https://api.nal.usda.gov/fdc/v1/foods/search?api_key=SECRETKEY123") == {"foods": []}
    assert net["dialled"] == [("93.184.216.34", 443)] and net["sni"] == "api.nal.usda.gov"

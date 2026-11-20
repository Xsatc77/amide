"""Real-socket checks against a real uvicorn process (the way the Dockerfile starts it). Skipped unless AMIDE_REALHTTP=1:

    AMIDE_REALHTTP=1 .venv/Scripts/python.exe -m pytest tests/test_realhttp.py -q -p no:warnings

The in-process TestClient never opens a socket, so request-smuggling shapes, the first-account race and a second process starting
on the same data folder are only visible here."""

import http.client
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("AMIDE_REALHTTP") != "1", reason="set AMIDE_REALHTTP=1 to start a real server")

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "Correct-Horse-9battery!"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(data):
    env = {k: v for k, v in os.environ.items() if k not in ("FORWARDED_ALLOW_IPS", "AMIDE_REGISTRATION", "AMIDE_SECRET_KEY", "AMIDE_PUBLIC_URL")}
    env.update(AMIDE_DATA_DIR=str(data), AMIDE_INGEST_WORKER="0", AMIDE_REMINDERS="0", AMIDE_BASE_LIBRARY="0")
    return env


@pytest.fixture(scope="module")
def server():
    data = tempfile.mkdtemp(prefix="amide-realhttp-")
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--proxy-headers",
                             "--forwarded-allow-ips=*"],            # trusted here only so each test client can have its own address
                            cwd=ROOT, env=_env(data), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(400):
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                c.request("GET", "/healthz")
                if c.getresponse().status == 200:
                    break
            except OSError:
                time.sleep(0.3)
        else:
            raise RuntimeError("the server did not start")
        yield port, data
    finally:
        proc.kill()
        proc.wait()


def _raw(port, data, timeout=4):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(data)
        out = b""
        try:
            while chunk := s.recv(65536):
                out += chunk
        except OSError:
            pass
        return out


@pytest.mark.parametrize("headers", [
    b"Content-Length: 4\r\nTransfer-Encoding: chunked\r\n",             # CL.TE
    b"Content-Length: 3\r\nContent-Length: 40\r\n",                      # two different lengths
    b"Transfer-Encoding: xchunked\r\n",                                  # an obfuscated encoding
    b"Content-Length: +3\r\n",
])
def test_smuggling_shapes_are_refused(server, headers):
    port, _ = server
    out = _raw(port, b"POST /login HTTP/1.1\r\nHost: x\r\n" + headers + b"Connection: close\r\n\r\n0\r\n\r\nGET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 400"), out[:80]
    assert out.count(b"HTTP/1.1") == 1                                   # nothing was parsed from the smuggled tail


def test_error_pages_carry_the_security_headers(server):
    port, _ = server
    out = _raw(port, b"GET /nope HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").lower()
    for name in (b"content-security-policy:", b"x-frame-options:", b"x-content-type-options:"):
        assert name in out


def test_an_oversized_declared_body_is_refused_before_it_is_read(server):
    port, _ = server
    out = _raw(port, b"POST /login HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999\r\nConnection: close\r\n\r\n")
    assert out.startswith(b"HTTP/1.1 413")


class _Browser:
    """A cookie-keeping client with its own made-up address (forwarded headers are trusted by the fixture)."""
    _n = 0
    _lock = threading.Lock()

    def __init__(self, port):
        with _Browser._lock:
            _Browser._n += 1
            self.ip = f"10.7.{_Browser._n // 250}.{_Browser._n % 250 + 1}"
        self.port, self.cookie = port, ""

    def post(self, path, form):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        c.request("POST", path, urllib.parse.urlencode(form), {"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": self.ip,
                                                              "Cookie": self.cookie})
        r = c.getresponse()
        r.read()
        for key, value in r.getheaders():
            if key.lower() == "set-cookie":
                self.cookie = value.split(";", 1)[0]
        c.close()
        return r.status


def test_twenty_simultaneous_registrations_make_exactly_one_admin(server):
    import sqlite3
    port, data = server
    people = [_Browser(port) for _ in range(20)]
    for p in people:
        assert p.post("/notice", {"understand": "1"}) == 303
    answers, gate = [], threading.Barrier(len(people))

    def go(i, p):
        gate.wait()
        answers.append(p.post("/register", {"username": f"racer{i:02d}", "password": PASSWORD, "confirm": PASSWORD}))

    threads = [threading.Thread(target=go, args=(i, p)) for i, p in enumerate(people)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert 500 not in answers, answers
    rows = sqlite3.connect(os.path.join(data, "amide.db")).execute("select is_admin from users").fetchall()
    assert len(rows) == 1 and rows[0][0] == 1, (len(rows), answers)      # closed after the first account, and that one is the admin


def test_two_processes_migrating_one_new_data_folder_both_succeed():
    data = tempfile.mkdtemp(prefix="amide-realhttp-mig-")
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); from app.migrate import upgrade_db; upgrade_db()"
    procs = [subprocess.Popen([sys.executable, "-c", code], env=_env(data), stderr=subprocess.PIPE) for _ in range(2)]
    results = [(p.wait(), p.stderr.read()[-300:]) for p in procs]
    assert [r[0] for r in results] == [0, 0], results


def test_two_processes_creating_the_secret_key_end_with_one_key_both_can_read():
    data = tempfile.mkdtemp(prefix="amide-realhttp-key-")
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); from app import secretbox; print(secretbox.encrypt('x' + sys.argv[1]))")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(i)], env=_env(data), stdout=subprocess.PIPE, text=True) for i in range(6)]
    sealed = [p.communicate()[0].strip() for p in procs]
    assert os.listdir(data) == ["secret.key"]
    check = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); from app import secretbox; print([secretbox.decrypt(v) for v in sys.argv[1:]])", *sealed],
                           env=_env(data), capture_output=True, text=True)
    assert all(f"'x{i}'" in check.stdout for i in range(6)), check.stdout

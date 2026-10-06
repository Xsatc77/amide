# Price List Watcher (Part B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A program on the owner's computer that watches the Telegram groups mapped in Amide, hands every new price-list message to Amide's token API, and reports groups that go away.

**Architecture:** A `watcher/` package, separate from the Amide app. Everything above a thin `TelegramPort` interface is plain async Python tested with a fake Telegram and a fake Amide. A poll loop registers groups (titles only), reads the groups Amide lists as enabled and mapped, writes each message to a crash-safe disk queue, and delivers the queue in order to Amide. A gone check reports a group only after two polls in a row agree. `TelethonClient` is the one real Telegram implementation; Windows startup uses Task Scheduler.

**Tech Stack:** Python 3.14, asyncio, Telethon 1.45 (new, watcher only), httpx 0.28 (already installed), tomllib. No pytest plugins: async code is driven with `asyncio.run` inside plain tests.

**Spec:** `docs/superpowers/specs/2026-10-06-price-list-watcher-design.md`

## Global Constraints

- Test command: `.venv/Scripts/python.exe -m pytest watcher/tests tests/test_watcher_guard.py tests/test_watcher_amide_client.py -q -p no:warnings`; the whole suite stays green (`.venv/Scripts/python.exe -m pytest -q -p no:warnings`, baseline 1774; run long suites with `run_in_background`).
- **Secrets never enter the repo, logs, errors, tests or commit messages:** the Amide token, Telegram `api_id`/`api_hash` and the session. Tests use obvious fakes (`test-token`, `1234`, `fakehash`). The repo contains only `watcher/config.example.toml` with placeholders.
- **No vendor, group name or price-list data anywhere in tracked files or commit messages.** Tests use invented names (Acme, Zephyr, Borealis, Quillamine). Before every push run `.venv/Scripts/python.exe /c/tmp/amide-scrub/denylist_scan.py` and read the whole output (the only accepted hit is the old phrase in `docs/superpowers/specs/2026-09-29-library-redesign-design.md`).
- Logging records only group titles, message ids, counts and error class names, never message text, file contents, the phone number, tokens or session data.
- The watcher is read only toward Telegram: it never sends, reacts, joins, leaves or marks read.
- Telethon is imported only in `watcher/telethon_client.py`; no test imports it.
- Write Python with the Write tool, not shell heredocs (backslashes are mangled).

## Review Focus

1. A secret leaking into a log line, error message, example config, test or commit. (Tasks 1, 4, 6)
2. A group read that should not be: unmapped or disabled groups, private chats; title-only registration. (Task 4)
3. A hiccup reported as "gone", and a real removal never reported. (Task 4)
4. A list lost or duplicated: crash between download and delivery, Amide down for hours, queue growth, an album split because its last photo was slow. (Tasks 2, 4)
5. Hammering Telegram or Amide: retry loops, a 401 crash loop, a huge backfill. (Task 4)

## Rulings made while planning (record in the ledger)

- v1 **polls** (default every 300 s) instead of also subscribing to live updates: simpler, cannot drop messages, and a price list arriving within five minutes is fast enough. Messages younger than 5 seconds that belong to an album are left for the next poll so an album is never split.
- All new messages go **through the disk queue**, even when Amide is up: one code path, strict ordering, crash safety.
- The gone check runs over the groups Amide lists as enabled and mapped (a gone group stays enabled there, so recovery is detected too).

## File Structure

- Create `watcher/__init__.py`, `watcher/__main__.py`, `watcher/config.py`, `watcher/state.py`, `watcher/queue.py`, `watcher/ports.py`, `watcher/amide_client.py`, `watcher/runner.py`, `watcher/telethon_client.py`, `watcher/cli.py`, `watcher/startup.py`, `watcher/logs.py`, `watcher/config.example.toml`, `watcher/README.md`.
- Create `watcher/tests/__init__.py`, `watcher/tests/fakes.py`, `watcher/tests/test_config.py`, `test_state_queue.py`, `test_runner.py`, `test_cli.py`.
- Create `tests/test_watcher_guard.py`, `tests/test_watcher_amide_client.py` (use Amide's own fixtures).
- Create `requirements-watcher.txt`; modify `.gitignore`, `docs/ROADMAP.md`.

---

### Task 1: Scaffold, configuration and the secrets guard

**Files:**
- Create: `watcher/__init__.py`, `watcher/__main__.py`, `watcher/config.py`, `watcher/config.example.toml`, `requirements-watcher.txt`, `watcher/tests/__init__.py`, `watcher/tests/test_config.py`, `tests/test_watcher_guard.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces in `watcher/config.py`: `class ConfigError(ValueError)`; `@dataclass(frozen=True) class Config` with `amide_url: str`, `amide_token: str`, `telegram_api_id: int`, `telegram_api_hash: str`, `backfill_days: int = 7`, `poll_seconds: int = 300`, `max_file_mb: int = 25`, `home: Path`; `Config.__repr__` never shows secrets; `default_home() -> Path` (env `AMIDE_WATCHER_HOME`, else `%APPDATA%/amide-watcher`, else `~/.amide-watcher`); `load_config(home: Path | None = None) -> Config`; properties `session_path`, `state_path`, `queue_dir`, `log_path`.

- [ ] **Step 1: Write the failing tests**

`watcher/tests/test_config.py`:
```python
from pathlib import Path

import pytest

from watcher import config as cfg

GOOD = """
amide_url = "http://127.0.0.1:8000"
amide_token = "test-token"
telegram_api_id = 1234
telegram_api_hash = "fakehash"
"""


def write(tmp_path, text):
    (tmp_path / "config.toml").write_text(text, encoding="utf-8")
    return tmp_path


def test_a_complete_config_loads_with_defaults(tmp_path):
    c = cfg.load_config(write(tmp_path, GOOD))
    assert (c.amide_url, c.telegram_api_id, c.backfill_days, c.poll_seconds, c.max_file_mb) == ("http://127.0.0.1:8000", 1234, 7, 300, 25)
    assert c.session_path == tmp_path / "telegram.session" and c.state_path == tmp_path / "state.json"
    assert c.queue_dir == tmp_path / "queue" and c.log_path == tmp_path / "watcher.log"


def test_the_amide_url_may_not_end_with_a_slash_and_defaults_apply(tmp_path):
    c = cfg.load_config(write(tmp_path, GOOD.replace("8000", "8000/").replace('amide_url = "http://127.0.0.1:8000/"', 'amide_url = "http://127.0.0.1:8000/"')))
    assert c.amide_url == "http://127.0.0.1:8000"


@pytest.mark.parametrize("missing", ["amide_token", "telegram_api_id", "telegram_api_hash"])
def test_a_missing_required_value_is_a_clear_error_naming_the_key_only(tmp_path, missing):
    text = "\n".join(line for line in GOOD.splitlines() if not line.startswith(missing))
    with pytest.raises(cfg.ConfigError) as err:
        cfg.load_config(write(tmp_path, text))
    assert missing in str(err.value) and "test-token" not in str(err.value) and "fakehash" not in str(err.value)


@pytest.mark.parametrize("bad", ['telegram_api_id = "abc"', "backfill_days = 0", "poll_seconds = 5", "max_file_mb = 100", 'amide_url = "ftp://x"'])
def test_invalid_values_are_refused(tmp_path, bad):
    key = bad.split(" =")[0]
    lines = [line for line in GOOD.splitlines() if not line.startswith(key)] + [bad]
    with pytest.raises(cfg.ConfigError):
        cfg.load_config(write(tmp_path, "\n".join(lines)))


def test_a_missing_file_says_where_to_put_it(tmp_path):
    with pytest.raises(cfg.ConfigError, match="config.toml"):
        cfg.load_config(tmp_path)


def test_the_repr_and_str_never_show_secrets(tmp_path):
    c = cfg.load_config(write(tmp_path, GOOD))
    for shown in (repr(c), str(c)):
        assert "test-token" not in shown and "fakehash" not in shown


def test_the_home_folder_can_be_overridden_by_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AMIDE_WATCHER_HOME", str(tmp_path))
    assert cfg.default_home() == tmp_path
```

`tests/test_watcher_guard.py`:
```python
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tracked():
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()


def test_no_secret_or_session_or_state_file_is_tracked():
    for name in tracked():
        base = name.rsplit("/", 1)[-1]
        assert base != "config.toml" and not base.endswith(".session") and not base.endswith(".session-journal")
        assert base != "state.json" and not name.startswith("watcher/queue/"), name


def test_gitignore_covers_the_watcher_secrets():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("config.toml", "*.session", "state.json", "queue/"):
        assert pattern in text


def test_the_example_config_holds_only_placeholders():
    text = (ROOT / "watcher" / "config.example.toml").read_text(encoding="utf-8")
    assert "YOUR_" in text and "amide_ing_" not in text
    for line in text.splitlines():
        if line.startswith("telegram_api_id"):
            assert line.split("=")[1].strip() == "0"
```

- [ ] **Step 2: Run to verify it fails** (`ModuleNotFoundError: watcher`).

- [ ] **Step 3: Implement**

`.gitignore`: append `watcher/config.toml`, `config.toml`, `*.session`, `*.session-journal`, `state.json`, `queue/`.

`requirements-watcher.txt`: `telethon==1.45.0` and `httpx==0.28.1`. Install telethon: `.venv/Scripts/python.exe -m pip install telethon==1.45.0` (tests never import it, so a missing install cannot break the Amide suite).

`watcher/__init__.py`: docstring only. `watcher/__main__.py`: `from watcher.cli import main\nraise SystemExit(main())` (`cli` arrives in Task 6; until then guard with a comment-free try is not allowed: create `__main__.py` in Task 6).

`watcher/config.example.toml`:
```toml
# Copy to config.toml in the watcher's home folder (default: %APPDATA%\amide-watcher). Never commit the real file.
amide_url = "http://127.0.0.1:8000"
amide_token = "YOUR_AMIDE_INGEST_TOKEN"        # created in Amide: Settings, Admin, Price list inbox
telegram_api_id = 0                            # from your own registration at my.telegram.org
telegram_api_hash = "YOUR_TELEGRAM_API_HASH"
backfill_days = 7
poll_seconds = 300
max_file_mb = 25
```

`watcher/config.py`:
```python
"""The watcher's settings, read from config.toml in its home folder. Secrets never appear in repr, str or error text."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """The configuration is missing or wrong; the message names the key, never its value."""


@dataclass(frozen=True)
class Config:
    amide_url: str
    amide_token: str = field(repr=False)
    telegram_api_id: int = field(repr=False)
    telegram_api_hash: str = field(repr=False)
    home: Path = Path(".")
    backfill_days: int = 7
    poll_seconds: int = 300
    max_file_mb: int = 25

    @property
    def session_path(self) -> Path:
        return self.home / "telegram.session"

    @property
    def state_path(self) -> Path:
        return self.home / "state.json"

    @property
    def queue_dir(self) -> Path:
        return self.home / "queue"

    @property
    def log_path(self) -> Path:
        return self.home / "watcher.log"

    def __str__(self) -> str:
        return repr(self)


def default_home() -> Path:
    if os.environ.get("AMIDE_WATCHER_HOME"):
        return Path(os.environ["AMIDE_WATCHER_HOME"])
    if os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "amide-watcher"
    return Path.home() / ".amide-watcher"


def _int(raw: dict, key: str, default: int | None, low: int, high: int) -> int:
    value = raw.get(key, default)
    if value is None:
        raise ConfigError(f"{key} is missing in config.toml")
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{key} must be a whole number from {low} to {high}")
    return value


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} is missing in config.toml")
    return value.strip()


def load_config(home: Path | None = None) -> Config:
    home = Path(home) if home is not None else default_home()
    path = home / "config.toml"
    if not path.is_file():
        raise ConfigError(f"No config.toml found in {home}. Copy watcher/config.example.toml there and fill it in.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        raise ConfigError("config.toml is not valid TOML") from None
    url = _text(raw, "amide_url").rstrip("/") if "amide_url" in raw else "http://127.0.0.1:8000"
    if not url.startswith(("http://", "https://")):
        raise ConfigError("amide_url must start with http:// or https://")
    return Config(amide_url=url, amide_token=_text(raw, "amide_token"), telegram_api_id=_int(raw, "telegram_api_id", None, 1, 2**31),
                  telegram_api_hash=_text(raw, "telegram_api_hash"), home=home, backfill_days=_int(raw, "backfill_days", 7, 1, 60),
                  poll_seconds=_int(raw, "poll_seconds", 300, 30, 3600), max_file_mb=_int(raw, "max_file_mb", 25, 1, 25))
```
(`telegram_api_id = 0` in the example is not valid on purpose: the owner must fill it in.)

- [ ] **Step 4: Run to verify it passes**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add .gitignore requirements-watcher.txt watcher tests/test_watcher_guard.py
git commit -m "feat: watcher scaffold, configuration and secrets guard"
```

---

### Task 2: Atomic state and the disk queue

**Files:**
- Create: `watcher/state.py`, `watcher/queue.py`, `watcher/ports.py` (payload type only now), `watcher/tests/test_state_queue.py`

**Interfaces:**
- Produces in `watcher/ports.py`: `@dataclass class Payload` with `chat_id: str`, `message_id: str`, `album_id: str | None`, `date: str` (ISO 8601 UTC), `text: str`, `files: list[tuple[str, bytes]]`.
- Produces in `watcher/state.py`: `@dataclass class ChatState` with `last_id: int = 0`, `started_at: str | None = None`, `strikes: int = 0`, `gone: bool = False`; `class State(path: Path)` with `chat(chat_id) -> ChatState` (creates default), `save()` (atomic: write temp, `os.replace`), `__init__` loads or starts empty, an unreadable file starts empty and keeps a `state.json.bad` copy.
- Produces in `watcher/queue.py`: `class DiskQueue(directory: Path, max_items=500, max_bytes=2*1024**3)` with `put(payload) -> None`, `oldest() -> QueueItem | None`, `remove(item) -> None`, `__len__`, `size_bytes()`, `full() -> bool`; `QueueItem.payload() -> Payload`.

- [ ] **Step 1: Write the failing tests** (`watcher/tests/test_state_queue.py`)

```python
import json

from watcher.ports import Payload
from watcher.queue import DiskQueue
from watcher.state import State


def payload(n=1, files=None, text="hi"):
    return Payload(chat_id="-100", message_id=str(n), album_id=None, date="2026-10-06T10:00:00+00:00", text=text,
                   files=files if files is not None else [("a.pdf", b"%PDF-" + bytes([n]))])


# ---------------------------------------------------------------- state

def test_state_defaults_save_atomically_and_reload(tmp_path):
    path = tmp_path / "state.json"
    state = State(path)
    assert state.chat("-100").last_id == 0
    state.chat("-100").last_id = 42
    state.chat("-100").gone = True
    state.save()
    assert not list(tmp_path.glob("*.tmp"))
    again = State(path)
    assert again.chat("-100").last_id == 42 and again.chat("-100").gone is True


def test_a_corrupt_state_file_starts_empty_and_keeps_a_copy(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = State(path)
    assert state.chat("-1").last_id == 0 and (tmp_path / "state.json.bad").exists()


def test_a_crash_before_the_rename_leaves_the_old_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    state = State(path)
    state.chat("-1").last_id = 5
    state.save()
    state.chat("-1").last_id = 9
    monkeypatch.setattr("os.replace", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    try:
        state.save()
    except OSError:
        pass
    assert json.loads(path.read_text(encoding="utf-8"))["chats"]["-1"]["last_id"] == 5


# ---------------------------------------------------------------- queue

def test_the_queue_keeps_order_files_and_survives_a_restart(tmp_path):
    q = DiskQueue(tmp_path / "queue")
    q.put(payload(1))
    q.put(payload(2, text="second"))
    assert len(q) == 2
    reopened = DiskQueue(tmp_path / "queue")
    first = reopened.oldest()
    assert first.payload() == payload(1)
    reopened.remove(first)
    assert reopened.oldest().payload().text == "second" and len(reopened) == 1


def test_a_half_written_item_is_never_delivered(tmp_path):
    q = DiskQueue(tmp_path / "queue")
    (tmp_path / "queue").mkdir(exist_ok=True)
    (tmp_path / "queue" / "000000000001.tmp").mkdir()
    (tmp_path / "queue" / "000000000001.tmp" / "meta.json").write_text("{", encoding="utf-8")
    assert q.oldest() is None and len(q) == 0
    q.put(payload(3))
    assert len(q) == 1 and q.oldest().payload().message_id == "3"


def test_the_queue_reports_full_by_items_or_bytes(tmp_path):
    q = DiskQueue(tmp_path / "queue", max_items=2, max_bytes=10_000)
    q.put(payload(1))
    assert not q.full()
    q.put(payload(2))
    assert q.full()
    small = DiskQueue(tmp_path / "other", max_items=500, max_bytes=50)
    small.put(payload(1, files=[("big.pdf", b"%PDF-" + b"x" * 100)]))
    assert small.full() and small.size_bytes() >= 100


def test_sequence_numbers_keep_growing_after_removals(tmp_path):
    q = DiskQueue(tmp_path / "queue")
    q.put(payload(1))
    q.remove(q.oldest())
    q.put(payload(2))
    assert q.oldest().payload().message_id == "2"
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`watcher/ports.py` (Task 4 extends it):
```python
"""The shapes passed between the watcher's parts."""

from dataclasses import dataclass


@dataclass
class Payload:
    chat_id: str
    message_id: str
    album_id: str | None
    date: str                       # ISO 8601, UTC
    text: str
    files: list[tuple[str, bytes]]
```

`watcher/state.py`:
```python
"""What the watcher remembers between runs, written atomically."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ChatState:
    last_id: int = 0
    started_at: str | None = None
    strikes: int = 0
    gone: bool = False


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.chats: dict[str, ChatState] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.chats = {k: ChatState(**v) for k, v in raw.get("chats", {}).items()}
            except (ValueError, TypeError, OSError):
                self.chats = {}
                try:
                    os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
                except OSError:
                    pass

    def chat(self, chat_id: str) -> ChatState:
        return self.chats.setdefault(str(chat_id), ChatState())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"chats": {k: asdict(v) for k, v in self.chats.items()}}), encoding="utf-8")
        os.replace(tmp, self.path)
```

`watcher/queue.py`:
```python
"""Messages waiting to be delivered to Amide, one folder each, written so a crash never leaves a half item."""

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from watcher.ports import Payload


class QueueItem:
    def __init__(self, folder: Path):
        self.folder = folder

    def payload(self) -> Payload:
        meta = json.loads((self.folder / "meta.json").read_text(encoding="utf-8"))
        files = [(name, (self.folder / f"file{n}").read_bytes()) for n, name in enumerate(meta.pop("file_names"))]
        return Payload(files=files, **meta)


class DiskQueue:
    def __init__(self, directory: Path, max_items: int = 500, max_bytes: int = 2 * 1024 ** 3):
        self.dir, self.max_items, self.max_bytes = Path(directory), max_items, max_bytes
        self.dir.mkdir(parents=True, exist_ok=True)
        for leftover in self.dir.glob("*.tmp"):                  # an item that was being written when the program stopped
            shutil.rmtree(leftover, ignore_errors=True)

    def _folders(self) -> list[Path]:
        return sorted(p for p in self.dir.iterdir() if p.is_dir() and not p.name.endswith(".tmp"))

    def __len__(self) -> int:
        return len(self._folders())

    def _next_number(self) -> int:
        marker = self.dir / "next"
        try:
            number = int(marker.read_text())
        except (OSError, ValueError):
            number = 1 + max((int(p.name) for p in self._folders()), default=0)
        marker.write_text(str(number + 1))
        return number

    def put(self, payload: Payload) -> None:
        number = self._next_number()
        tmp = self.dir / f"{number:012d}.tmp"
        tmp.mkdir()
        meta = asdict(payload)
        files = meta.pop("files")
        meta["file_names"] = [name for name, _ in files]
        for n, (_, data) in enumerate(files):
            (tmp / f"file{n}").write_bytes(data)
        (tmp / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        os.replace(tmp, self.dir / f"{number:012d}")

    def oldest(self) -> QueueItem | None:
        folders = self._folders()
        return QueueItem(folders[0]) if folders else None

    def remove(self, item: QueueItem) -> None:
        shutil.rmtree(item.folder, ignore_errors=True)

    def size_bytes(self) -> int:
        return sum(f.stat().st_size for p in self._folders() for f in p.iterdir())

    def full(self) -> bool:
        return len(self) >= self.max_items or self.size_bytes() >= self.max_bytes
```

- [ ] **Step 4: Run to verify it passes**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add watcher/state.py watcher/queue.py watcher/ports.py watcher/tests/__init__.py watcher/tests/test_state_queue.py
git commit -m "feat: watcher state file and disk queue (atomic, ordered, capped)"
```

---

### Task 3: The Amide client

**Files:**
- Create: `watcher/amide_client.py`, `tests/test_watcher_amide_client.py`

**Interfaces:**
- Produces in `watcher/amide_client.py`: exceptions `AmideAuthError`, `AmideUnavailable`; `@dataclass class Delivery` with `kind: str` (`"delivered" | "retry" | "drop" | "auth"`) and `detail: str`; `class AmideClient(http: httpx.AsyncClient, token: str)` with `async register(chat_id: str, title: str) -> None`, `async list_sources() -> list[str]` (chat ids), `async report_state(chat_id: str, state: str, reason: str | None = None) -> bool`, `async send(payload: Payload) -> Delivery`. `http` is built by `make_http(config) -> httpx.AsyncClient` (base_url, 60 s timeout).
- `register`, `list_sources` raise `AmideAuthError` on 401 and `AmideUnavailable` on network errors, 429 or 5xx; `report_state` returns False on any failure (caller retries later) and raises `AmideAuthError` on 401.
- Consumes the Part A contract: `PUT /api/ingest/sources/{chat_id}` json `{title}`; `GET /api/ingest/sources` → `[{chat_id, title}]`; `POST /api/ingest/sources/{chat_id}/state` json `{state, reason}`; `POST /api/ingest/messages` multipart (`chat_id`, `message_id`, `album_id`, `date`, `text`, `files`).

- [ ] **Step 1: Write the failing tests** (`tests/test_watcher_amide_client.py`; uses Amide's real app through an ASGI transport, with Amide's fixtures `db`, `me`, `make_token`, `make_source`)

```python
import asyncio

import httpx

from app.main import app
from app.models import IngestItem, IngestSource, Vendor
from ingest_helpers import make_source, make_token, pdf_bytes
from watcher.amide_client import AmideAuthError, AmideClient, AmideUnavailable
from watcher.ports import Payload


def http():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://amide.test")


def run(coro):
    return asyncio.run(coro)


def payload(**kw):
    return Payload(**{**dict(chat_id="-100123", message_id="10", album_id=None, date="2026-10-06T14:30:00+00:00", text="New prices",
                             files=[("list.pdf", pdf_bytes())]), **kw})


def test_register_list_send_and_state_against_the_real_api(client, db, me):
    secret = make_token(db, me)

    async def go():
        async with http() as h:
            amide = AmideClient(h, secret)
            await amide.register("-100123", "Acme group")
            assert await amide.list_sources() == []                         # registered but not mapped or enabled yet
            source = db.query(IngestSource).one()
            vendor = Vendor(name="Acme Labs")
            db.add(vendor)
            db.commit()
            source.vendor_id, source.enabled = vendor.id, True
            db.commit()
            assert await amide.list_sources() == ["-100123"]
            first = await amide.send(payload())
            again = await amide.send(payload())
            assert (first.kind, again.kind) == ("delivered", "delivered")   # a resend is harmless (Amide answers duplicate)
            assert await amide.report_state("-100123", "gone", "removed from the group") is True
    run(go())
    db.expire_all()
    assert db.query(IngestItem).count() == 1 and db.query(IngestSource).one().state == "gone"


def test_an_album_goes_as_one_message_with_its_album_id(client, db, me):
    from ingest_helpers import png_bytes
    secret = make_token(db, me)
    make_source(db)

    async def go():
        async with http() as h:
            return await AmideClient(h, secret).send(payload(album_id="777", text="", files=[("1.png", png_bytes(1)), ("2.png", png_bytes(2))]))
    assert run(go()).kind == "delivered"
    assert {i.group_key for i in db.query(IngestItem)} == {f"{db.query(IngestSource).one().id}:a:777"}


def test_a_bad_token_is_an_auth_error_and_a_disabled_group_is_dropped_not_retried(client, db, me):
    secret = make_token(db, me)
    make_source(db, enabled=False)

    async def go():
        async with http() as h:
            bad = AmideClient(h, "amide_ing_wrong")
            try:
                await bad.list_sources()
                raise AssertionError("should have raised")
            except AmideAuthError:
                pass
            assert (await bad.send(payload())).kind == "auth"
            assert (await AmideClient(h, secret).send(payload())).kind == "drop"            # 409: the group is disabled
    run(go())


def test_network_errors_and_server_errors_mean_retry_later():
    def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(503)
        if request.url.path.endswith("/sources"):
            return httpx.Response(429)
        raise httpx.ConnectError("down")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://amide.test") as h:
            amide = AmideClient(h, "t")
            assert (await amide.send(payload())).kind == "retry"
            for call in (amide.list_sources(), amide.register("-1", "x")):
                try:
                    await call
                    raise AssertionError("should have raised")
                except AmideUnavailable:
                    pass
            assert await amide.report_state("-1", "gone") is False
    run(go())
    assert run(_unreachable()).kind == "retry"


async def _unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused")
    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://amide.test") as h:
        return await AmideClient(h, "t").send(payload())
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** `watcher/amide_client.py`:
```python
"""Talking to Amide's token API. Never logs or raises with the token, message text or file contents."""

from dataclasses import dataclass

import httpx

from watcher.ports import Payload


class AmideAuthError(RuntimeError):
    """Amide refused the token (wrong, revoked, or its owner is no longer an administrator)."""


class AmideUnavailable(RuntimeError):
    """Amide could not be reached or is busy; try again later."""


@dataclass
class Delivery:
    kind: str                       # delivered | retry | drop | auth
    detail: str = ""


def make_http(config) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=config.amide_url, timeout=60.0)


class AmideClient:
    def __init__(self, http: httpx.AsyncClient, token: str):
        self.http = http
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _call(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            response = await self.http.request(method, url, headers=self._headers, **kw)
        except httpx.HTTPError as exc:
            raise AmideUnavailable(type(exc).__name__) from None
        if response.status_code == 401:
            raise AmideAuthError("Amide did not accept the token")
        if response.status_code == 429 or response.status_code >= 500:
            raise AmideUnavailable(f"HTTP {response.status_code}")
        return response

    async def register(self, chat_id: str, title: str) -> None:
        await self._call("PUT", f"/api/ingest/sources/{chat_id}", json={"title": title[:200]})

    async def list_sources(self) -> list[str]:
        response = await self._call("GET", "/api/ingest/sources")
        response.raise_for_status()
        return [row["chat_id"] for row in response.json()]

    async def report_state(self, chat_id: str, state: str, reason: str | None = None) -> bool:
        try:
            response = await self._call("POST", f"/api/ingest/sources/{chat_id}/state", json={"state": state, "reason": reason})
        except AmideUnavailable:
            return False
        return response.status_code == 200

    async def send(self, payload: Payload) -> Delivery:
        data = {"chat_id": payload.chat_id, "message_id": payload.message_id, "date": payload.date, "text": payload.text}
        if payload.album_id:
            data["album_id"] = payload.album_id
        files = [("files", (name, content, "application/octet-stream")) for name, content in payload.files] or None
        try:
            response = await self._call("POST", "/api/ingest/messages", data=data, files=files)
        except AmideAuthError:
            return Delivery("auth", "token refused")
        except AmideUnavailable as exc:
            return Delivery("retry", str(exc))
        if response.status_code == 200:
            return Delivery("delivered")
        return Delivery("drop", f"HTTP {response.status_code}")
```
(Note `report_state` on 401 raises `AmideAuthError`, by design; the runner handles it.)

- [ ] **Step 4: Run to verify it passes**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add watcher/amide_client.py tests/test_watcher_amide_client.py
git commit -m "feat: watcher client for Amide's token API (delivered, retry, drop, auth)"
```

---

### Task 4: The Telegram port and the runner

**Files:**
- Modify: `watcher/ports.py`
- Create: `watcher/runner.py`, `watcher/logs.py`, `watcher/tests/fakes.py`, `watcher/tests/test_runner.py`

**Interfaces:**
- Produces in `watcher/ports.py` (added): `@dataclass class Group` (`chat_id: str`, `title: str`); `@dataclass class Attachment` (`filename: str`, `kind: str` (`pdf|image|xlsx`), `size: int`); `@dataclass class TgMessage` (`chat_id: str`, `message_id: int`, `date: datetime` (timezone-aware UTC), `text: str`, `grouped_id: int | None`, `attachments: list[Attachment]`, `raw: object = field(default=None, repr=False, compare=False)`); `@dataclass class Access` (`state: str` (`ok|gone|unknown`), `reason: str = ""`); `class TelegramPort(Protocol)` with `async list_groups() -> list[Group]`, `async messages_since(chat_id: str, min_id: int, since: datetime | None) -> list[TgMessage]` (oldest first; ids greater than `min_id` when `min_id > 0`, else messages from `since`), `async download(message: TgMessage, attachment: Attachment) -> bytes`, `async check_access(chat_id: str) -> Access`.
- Produces in `watcher/logs.py`: `get_logger(path: Path | None) -> logging.Logger` (rotating file 1 MB x 3, plus console; name `watcher`).
- Produces in `watcher/runner.py`: constants `ALBUM_SETTLE_SECONDS = 5`, `BACKOFF = (30, 60, 120, 300, 900)`; `class Runner(tg, amide, state, queue, config, *, now=..., sleep=..., log=...)` with `async poll_once() -> None`, attribute `paused: bool`.
- `watcher/tests/fakes.py`: `FakeTelegram` (scripted groups, messages per chat, downloads, access results, a `calls` list recording every method name so tests can prove nothing else was touched) and `FakeAmide` (records `registered`, `sent`, `states`; `watch` list; `outcomes` iterator for `send`; can raise `AmideAuthError`/`AmideUnavailable`).

- [ ] **Step 1: Write the failing tests** (`watcher/tests/test_runner.py`)

```python
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from watcher.amide_client import AmideAuthError, AmideUnavailable, Delivery
from watcher.config import Config
from watcher.ports import Access, Attachment, Group, TgMessage
from watcher.queue import DiskQueue
from watcher.runner import ALBUM_SETTLE_SECONDS, Runner
from watcher.state import State
from watcher.tests.fakes import FakeAmide, FakeTelegram

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
SECRET_TEXT = "Zorvex ZX10 10mg*10vials $60 SECRET-PRICE-TEXT"


def msg(chat="-100", mid=1, text="", minutes_ago=60, grouped=None, files=()):
    return TgMessage(chat_id=chat, message_id=mid, date=NOW - timedelta(minutes=minutes_ago), text=text, grouped_id=grouped,
                     attachments=[Attachment(*f) for f in files])


def build(tmp_path, tg=None, amide=None, **cfg):
    config = Config(amide_url="http://x", amide_token="test-token", telegram_api_id=1234, telegram_api_hash="fakehash", home=tmp_path, **cfg)
    tg = tg or FakeTelegram()
    amide = amide or FakeAmide(watch=["-100"])
    state, queue = State(tmp_path / "state.json"), DiskQueue(tmp_path / "queue")
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
    runner = Runner(tg, amide, state, queue, config, now=lambda: NOW, sleep=sleep, log=logging.getLogger("watcher-test"))
    return runner, tg, amide, state, queue, slept


def poll(runner):
    asyncio.run(runner.poll_once())


# ---------------------------------------------------------------- registering and what is read

def test_every_group_is_registered_by_title_but_only_listed_groups_are_read(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "Acme group"), Group("-200", "Unmapped group")],
                      messages={"-100": [msg("-100", 1, "hello")], "-200": [msg("-200", 1, "secret chatter")]})
    runner, tg, amide, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert amide.registered == [("-100", "Acme group"), ("-200", "Unmapped group")]
    assert [c for c in tg.calls if c[0] == "messages_since"] == [("messages_since", "-100")]          # the unmapped group is never read
    assert {name for name, *_ in tg.calls} <= {"list_groups", "messages_since", "download", "check_access"}


def test_a_group_that_amide_stops_listing_stops_being_read(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "Acme group")], messages={"-100": [msg("-100", 1, "hello")]})
    runner, tg, amide, *_ = build(tmp_path, tg=tg, amide=FakeAmide(watch=[]))
    poll(runner)
    assert not [c for c in tg.calls if c[0] == "messages_since"] and amide.sent == []


# ---------------------------------------------------------------- messages

def test_a_text_message_and_a_file_message_are_delivered_in_order_with_their_context(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "Acme group")], messages={"-100": [
        msg("-100", 5, SECRET_TEXT, 50), msg("-100", 6, "USA warehouse", 40, files=[("list.pdf", "pdf", 100)])]},
        downloads={("-100", 6, "list.pdf"): b"%PDF-1.4 data"})
    runner, tg, amide, state, queue, _ = build(tmp_path, tg=tg)
    poll(runner)
    assert [(p.message_id, p.text, [n for n, _ in p.files], p.date) for p in amide.sent] == [
        ("5", SECRET_TEXT, [], (NOW - timedelta(minutes=50)).isoformat()), ("6", "USA warehouse", ["list.pdf"], (NOW - timedelta(minutes=40)).isoformat())]
    assert amide.sent[1].files[0][1] == b"%PDF-1.4 data" and len(queue) == 0 and state.chat("-100").last_id == 6


def test_unwanted_and_oversized_attachments_and_empty_messages_are_skipped(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [
        msg("-100", 1, "", 50, files=[("big.pdf", "pdf", 26 * 1024 * 1024)]), msg("-100", 2, "", 40), msg("-100", 3, "ok", 30)]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["3"] and not [c for c in tg.calls if c[0] == "download"]
    assert state.chat("-100").last_id == 3


def test_the_first_read_goes_back_backfill_days_and_later_reads_continue_from_the_last_id(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [msg("-100", 1, "old", 60 * 24 * 10), msg("-100", 2, "recent", 60 * 24 * 2)]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["2"]                                  # 7 days back by default: the 10-day-old one is not read
    tg.messages["-100"].append(msg("-100", 3, "new", 5))
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["2", "3"]
    assert ("messages_since", "-100", 2) in tg.detailed


def test_an_album_is_sent_as_one_message_once_it_has_settled(tmp_path):
    files = [("1.jpg", "image", 10), ("2.jpg", "image", 10)]
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [
        msg("-100", 7, "caption", 1, grouped=99, files=files[:1]), msg("-100", 8, "", 0, grouped=99, files=files[1:])]},
        downloads={("-100", 7, "1.jpg"): b"a", ("-100", 8, "2.jpg"): b"b"})
    tg.messages["-100"][1].date = NOW - timedelta(seconds=2)                              # the last photo arrived 2 s ago: not settled
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert amide.sent == [] and state.chat("-100").last_id == 0                          # held back, nothing lost
    tg.messages["-100"][1].date = NOW - timedelta(seconds=ALBUM_SETTLE_SECONDS + 1)
    poll(runner)
    assert len(amide.sent) == 1 and amide.sent[0].album_id == "99" and amide.sent[0].message_id == "7"
    assert [n for n, _ in amide.sent[0].files] == ["1.jpg", "2.jpg"] and amide.sent[0].text == "caption"


# ---------------------------------------------------------------- delivery

def test_when_amide_is_down_messages_wait_in_order_then_deliver_with_backoff(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [msg("-100", 1, "one", 50), msg("-100", 2, "two", 40)]})
    amide = FakeAmide(watch=["-100"], outcomes=[Delivery("retry", "HTTP 503")])
    runner, tg, amide, state, queue, _ = build(tmp_path, tg=tg, amide=amide)
    poll(runner)
    assert len(queue) == 2 and amide.sent == [] and state.chat("-100").last_id == 2        # read and safely queued, not lost
    poll(runner)                                                                            # still inside the first backoff wait
    assert amide.attempts == 1
    runner.now = lambda: NOW + timedelta(seconds=31)
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["1", "2"] and len(queue) == 0


def test_a_rejected_message_is_dropped_and_a_refused_token_pauses_without_a_crash_loop(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [msg("-100", 1, "one", 50), msg("-100", 2, "two", 40)]})
    amide = FakeAmide(watch=["-100"], outcomes=[Delivery("drop", "HTTP 422"), Delivery("auth", "token refused")])
    runner, tg, amide, state, queue, _ = build(tmp_path, tg=tg, amide=amide)
    poll(runner)
    assert len(queue) == 1 and runner.paused is True                                        # the first was dropped, the second waits
    before = amide.attempts
    poll(runner)
    assert amide.attempts == before or amide.auth_checks >= 1                               # only the cheap token check is retried


def test_a_full_queue_stops_reading_but_loses_nothing(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], messages={"-100": [msg("-100", n, f"m{n}", 100 - n) for n in range(1, 6)]})
    amide = FakeAmide(watch=["-100"], outcomes=[Delivery("retry")] * 50)
    runner, tg, amide, state, queue, _ = build(tmp_path, tg=tg, amide=amide)
    queue.max_items = 3
    poll(runner)
    assert len(queue) == 3 and state.chat("-100").last_id == 3                              # stopped at the cap; 4 and 5 are still in Telegram
    queue.max_items = 500
    amide.outcomes = iter([])
    runner.now = lambda: NOW + timedelta(hours=1)
    poll(runner)
    assert [p.message_id for p in amide.sent] == ["1", "2", "3", "4", "5"]


def test_nothing_secret_is_ever_logged(tmp_path, caplog):
    tg = FakeTelegram(groups=[Group("-100", "Acme group")], messages={"-100": [msg("-100", 1, SECRET_TEXT, 50)]})
    runner, *_ = build(tmp_path, tg=tg, amide=FakeAmide(watch=["-100"], outcomes=[Delivery("retry", "HTTP 503")]))
    with caplog.at_level(logging.DEBUG):
        poll(runner)
    text = caplog.text + "".join(r.getMessage() for r in caplog.records)
    assert "SECRET-PRICE-TEXT" not in text and "test-token" not in text and "fakehash" not in text


def test_a_telegram_failure_in_one_group_does_not_stop_the_others_or_move_its_position(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "a"), Group("-200", "b")],
                      messages={"-100": [msg("-100", 1, "x", 30, files=[("a.pdf", "pdf", 5)])], "-200": [msg("-200", 1, "y", 30)]},
                      fail_downloads=True)
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg, amide=FakeAmide(watch=["-100", "-200"]))
    poll(runner)
    assert [p.chat_id for p in amide.sent] == ["-200"] and state.chat("-100").last_id == 0


# ---------------------------------------------------------------- the gone check

def test_a_quiet_group_is_not_gone_and_one_bad_poll_is_not_enough(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], access={"-100": [Access("gone", "removed from the group"), Access("ok")]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    poll(runner)
    assert amide.states == [] and state.chat("-100").strikes == 1
    poll(runner)
    assert amide.states == [] and state.chat("-100").strikes == 0


def test_two_polls_in_a_row_report_gone_once_and_recovery_reports_active(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], access={"-100": [Access("gone", "removed from the group")] * 3 + [Access("ok")]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    for _ in range(3):
        poll(runner)
    assert amide.states == [("-100", "gone", "removed from the group")]
    poll(runner)
    assert amide.states[-1] == ("-100", "active", None) and state.chat("-100").gone is False


def test_an_unknown_answer_such_as_a_network_error_changes_nothing(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], access={"-100": [Access("gone", "x"), Access("unknown"), Access("unknown"), Access("gone", "x")]})
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg)
    for _ in range(4):
        poll(runner)
    assert state.chat("-100").strikes == 2 and len(amide.states) == 1                       # gone, unknown, unknown, gone: reported on the second strike


def test_a_gone_report_that_could_not_be_delivered_is_retried_next_poll(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "g")], access={"-100": [Access("gone", "x")] * 4})
    amide = FakeAmide(watch=["-100"], state_ok=[False, True])
    runner, tg, amide, state, *_ = build(tmp_path, tg=tg, amide=amide)
    for _ in range(3):
        poll(runner)
    assert state.chat("-100").gone is True and len(amide.states) == 2


def test_pauses_between_groups_are_taken_so_telegram_is_not_hammered(tmp_path):
    tg = FakeTelegram(groups=[Group("-100", "a"), Group("-200", "b")], messages={})
    runner, tg, amide, state, queue, slept = build(tmp_path, tg=tg, amide=FakeAmide(watch=["-100", "-200"]))
    poll(runner)
    assert slept and all(s > 0 for s in slept)
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`watcher/ports.py` additions: the dataclasses and `TelegramPort` Protocol exactly as in Interfaces (imports `datetime`, `field`, `Protocol`).

`watcher/tests/fakes.py`:
```python
"""Stand-ins for Telegram and Amide so the runner is tested without a network."""

from watcher.amide_client import AmideAuthError, AmideUnavailable, Delivery
from watcher.ports import Access


class FakeTelegram:
    def __init__(self, groups=(), messages=None, downloads=None, access=None, fail_downloads=False):
        self.groups, self.messages, self.downloads = list(groups), dict(messages or {}), dict(downloads or {})
        self.access, self.fail_downloads = {k: iter(v) for k, v in (access or {}).items()}, fail_downloads
        self.calls: list[tuple] = []
        self.detailed: list[tuple] = []

    async def list_groups(self):
        self.calls.append(("list_groups",))
        return list(self.groups)

    async def messages_since(self, chat_id, min_id, since):
        self.calls.append(("messages_since", chat_id))
        self.detailed.append(("messages_since", chat_id, min_id))
        rows = self.messages.get(chat_id, [])
        if min_id > 0:
            return [m for m in rows if m.message_id > min_id]
        return [m for m in rows if since is None or m.date >= since]

    async def download(self, message, attachment):
        self.calls.append(("download", message.chat_id))
        if self.fail_downloads:
            raise OSError("connection reset")
        return self.downloads.get((message.chat_id, message.message_id, attachment.filename), b"x" * attachment.size)

    async def check_access(self, chat_id):
        self.calls.append(("check_access", chat_id))
        return next(self.access.get(chat_id, iter([])), Access("ok"))


class FakeAmide:
    def __init__(self, watch=(), outcomes=(), state_ok=()):
        self.watch, self.outcomes, self.state_ok = list(watch), iter(outcomes), iter(state_ok)
        self.registered, self.sent, self.states = [], [], []
        self.attempts = self.auth_checks = 0
        self.token_ok = True

    async def register(self, chat_id, title):
        self.registered.append((chat_id, title))

    async def list_sources(self):
        self.auth_checks += 1
        if not self.token_ok:
            raise AmideAuthError("refused")
        return list(self.watch)

    async def report_state(self, chat_id, state, reason=None):
        self.states.append((chat_id, state, reason))
        return next(self.state_ok, True)

    async def send(self, payload):
        self.attempts += 1
        outcome = next(self.outcomes, Delivery("delivered"))
        if outcome.kind in ("delivered",):
            self.sent.append(payload)
        return outcome
```

`watcher/logs.py`:
```python
"""Logging to a rotating file and the console. Callers log only titles, ids, counts and error class names."""

import logging
import logging.handlers
from pathlib import Path


def get_logger(path: Path | None) -> logging.Logger:
    log = logging.getLogger("watcher")
    if not log.handlers:
        log.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        log.addHandler(console)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(formatter)
            log.addHandler(handler)
    return log
```

`watcher/runner.py`:
```python
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
                except AmideAuthError:
                    return
                except AmideUnavailable:
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
```
Adjust as the tests demand (for example the "unknown" test expects strikes to stay at the earlier value; the paused test only needs `auth_checks` to rise). Run the tests, fix the code to match the spec'd behavior, not the tests, unless a test contradicts the spec.

- [ ] **Step 4: Run to verify it passes**, then the full suite.

- [ ] **Step 5: Commit**
```bash
git add watcher/ports.py watcher/runner.py watcher/logs.py watcher/tests/fakes.py watcher/tests/test_runner.py
git commit -m "feat: watcher runner (register, read, queue, deliver with backoff, two-poll gone check)"
```

---

### Task 5: The Telethon implementation

**Files:**
- Create: `watcher/telethon_client.py`

**Interfaces:**
- Consumes: `TelegramPort` shapes from `watcher/ports.py`; `Config`.
- Produces: `class TelethonClient(config: Config)` implementing `TelegramPort` plus `async connect()`, `async login_interactive()`, `async close()`, `async is_authorized() -> bool`. Imports Telethon lazily inside the methods so importing the module never needs Telethon.

This class is the one part not unit tested (it needs a real account); it is kept thin and reviewed by reading, then exercised by the owner's manual check (Task 6).

- [ ] **Step 1: Write the code** (no test): `watcher/telethon_client.py`

```python
"""The real Telegram connection (Telethon). Thin on purpose: all decisions live in the runner. Read only: it never sends,
reacts, joins, leaves or marks anything read."""

from datetime import datetime, timezone

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
        self._client = TelegramClient(str(config.session_path), config.telegram_api_id, config.telegram_api_hash, flood_sleep_threshold=300)

    async def connect(self) -> None:
        await self._client.connect()

    async def is_authorized(self) -> bool:
        return await self._client.is_user_authorized()

    async def login_interactive(self) -> None:
        await self._client.start()                          # prompts for the phone, the code and the two-step password; prints nothing secret

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
            if m.photo is not None:
                attachments.append(Attachment(filename=f"photo-{m.id}.jpg", kind="image", size=getattr(m.file, "size", 0) or 0))
            elif m.document is not None and _kind(getattr(m.file, "mime_type", None)):
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
        from telethon import errors
        try:
            entity = await self._client.get_input_entity(int(chat_id))
            await self._client.get_messages(entity, limit=1)
            return Access("ok")
        except (errors.ChannelPrivateError, errors.ChannelInvalidError, errors.ChatIdInvalidError, errors.PeerIdInvalidError,
                errors.UserBannedInChannelError):
            return Access("gone", "the group is private, deleted, or this account was removed")
        except Exception:                                    # flood waits, network trouble and anything else: not evidence of removal
            return Access("unknown")
```
Review notes for the reviewer: `iter_messages(..., reverse=True, offset_date=since)` returns messages from that date oldest first; `min_id` with `reverse=True` returns ids above it oldest first. A group whose id Telethon can no longer resolve raises `ValueError` from `get_input_entity`; that case is treated as `unknown` unless it persists, which is a known limit noted in the README.

- [ ] **Step 2: Verify it imports without Telethon being used:** `.venv/Scripts/python.exe -c "import watcher.telethon_client"` and, after installing Telethon, constructing the class with a fake config in a temp folder (`TelethonClient(config)`) does not connect or print anything.

- [ ] **Step 3: Commit**
```bash
git add watcher/telethon_client.py
git commit -m "feat: Telethon implementation of the watcher's Telegram port (read only)"
```

---

### Task 6: Command line, Windows startup, README

**Files:**
- Create: `watcher/cli.py`, `watcher/startup.py`, `watcher/__main__.py`, `watcher/README.md`, `watcher/tests/test_cli.py`
- Modify: `docs/ROADMAP.md`

**Interfaces:**
- Produces in `watcher/cli.py`: `main(argv: list[str] | None = None) -> int`; commands `login`, `run`, `once [--days N]`, `status`, `install-startup`, `remove-startup`; `format_status(config, state, queue, amide_state: str) -> str` (never includes secrets).
- Produces in `watcher/startup.py`: `startup_command(repo_root: Path, python: Path) -> list[str]` (the `schtasks /Create` argument list), `remove_command() -> list[str]`, `install()`, `remove()` (run the commands with `subprocess.run`).
- `once --days N` runs one `poll_once` with `backfill_days` overridden to N for groups never read before.
- `run` loops `poll_once` every `poll_seconds`, catching every exception (logging only its class name), until interrupted (Ctrl+C exits 0). With a missing session it logs "run `python -m watcher login`" and exits 2.

- [ ] **Step 1: Write the failing tests** (`watcher/tests/test_cli.py`)

```python
from pathlib import Path

from watcher import cli, startup
from watcher.config import Config
from watcher.queue import DiskQueue
from watcher.ports import Payload
from watcher.state import State


def config(tmp_path):
    return Config(amide_url="http://x", amide_token="test-token", telegram_api_id=1234, telegram_api_hash="fakehash", home=tmp_path)


def test_status_shows_counts_and_never_a_secret(tmp_path):
    state = State(tmp_path / "state.json")
    state.chat("-100").last_id = 42
    state.chat("-100").gone = True
    queue = DiskQueue(tmp_path / "queue")
    queue.put(Payload("-100", "1", None, "2026-10-06T10:00:00+00:00", "hi", []))
    (tmp_path / "telegram.session").write_bytes(b"x")
    text = cli.format_status(config(tmp_path), state, queue, "token accepted")
    assert "42" in text and "1 item" in text and "token accepted" in text and "session: present" in text
    for secret in ("test-token", "fakehash", "1234"):
        assert secret not in text


def test_status_says_when_the_session_is_missing(tmp_path):
    text = cli.format_status(config(tmp_path), State(tmp_path / "state.json"), DiskQueue(tmp_path / "queue"), "not checked")
    assert "session: missing" in text


def test_a_missing_config_exits_with_a_message_not_a_traceback(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AMIDE_WATCHER_HOME", str(tmp_path))
    assert cli.main(["status"]) == 2
    assert "config.toml" in capsys.readouterr().err


def test_unknown_commands_are_refused(capsys):
    try:
        cli.main(["frobnicate"])
    except SystemExit as exit_:
        assert exit_.code == 2


def test_the_startup_command_runs_the_watcher_at_logon_from_the_repo_folder(tmp_path):
    command = startup.startup_command(Path("C:/tmp/amide"), Path("C:/py/python.exe"))
    joined = " ".join(command)
    assert command[0].lower() == "schtasks" and "/Create" in command and "ONLOGON" in command and "AmideWatcher" in command
    assert "watcher run" in joined and "C:/tmp/amide" in joined.replace("\\", "/") and "/F" in command
    assert startup.remove_command() == ["schtasks", "/Delete", "/TN", "AmideWatcher", "/F"]
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

`watcher/startup.py`:
```python
"""Start the watcher when the owner signs in to Windows, using Task Scheduler (no admin rights needed)."""

import subprocess
from pathlib import Path

TASK = "AmideWatcher"


def startup_command(repo_root: Path, python: Path) -> list[str]:
    inner = f'cd /d "{repo_root}" && "{python}" -m watcher run'
    return ["schtasks", "/Create", "/SC", "ONLOGON", "/TN", TASK, "/TR", f'cmd /c "{inner}"', "/RL", "LIMITED", "/F"]


def remove_command() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK, "/F"]


def install() -> int:
    import sys
    return subprocess.run(startup_command(Path(__file__).resolve().parent.parent, Path(sys.executable))).returncode


def remove() -> int:
    return subprocess.run(remove_command()).returncode
```

`watcher/cli.py`: `argparse` with the six subcommands. `main` loads config (`ConfigError` → print `str(error)` to stderr, return 2) except for `install-startup`/`remove-startup` which need none. `format_status` returns lines: `home: <path>`, `session: present|missing`, `amide: <amide_state>`, `queue: N item(s), M bytes`, one line per chat (`<chat_id>: last message <id>, gone: yes|no`). `status` command checks the token with a short `list_sources()` call (`token accepted` / `token refused` / `Amide not reachable`), never contacting Telegram. `login`: constructs `TelethonClient`, `asyncio.run(login_interactive())`, prints "Signed in. The session is saved in <home>." (path only). `run`: builds `TelethonClient`, requires `is_authorized()` else logs the login hint and returns 2; builds `AmideClient(make_http(config), config.amide_token)`, `State`, `DiskQueue`, `Runner`; `while True: await runner.poll_once(); await asyncio.sleep(config.poll_seconds)` with a try/except that logs `type(exc).__name__`; closes the client in `finally`. `once`: same but a single `poll_once`, honoring `--days` by building `Config` via `dataclasses.replace(config, backfill_days=N)`.

`watcher/__main__.py`:
```python
from watcher.cli import main

raise SystemExit(main())
```

`watcher/README.md`: what it is; one-time setup (register at my.telegram.org, create a token in Amide's Price list inbox, copy `config.example.toml` to `%APPDATA%\amide-watcher\config.toml`, `pip install -r requirements-watcher.txt`, `python -m watcher login`, `python -m watcher status`, `python -m watcher once --days 2`, `python -m watcher install-startup`); what it reads and never does; where secrets live; known limits (v1 ignores edits and deletions, polls every 5 minutes, a group Telethon can no longer resolve is reported as unknown rather than gone). No vendor names.

`docs/ROADMAP.md`: add a note after the Part A entry: "Price list watcher, part B (built <date>): `watcher/` ... Not tested against a live account until the owner runs the manual check."

- [ ] **Step 4: Run to verify it passes**, then the full suite and the secrets guard.

- [ ] **Step 5: Commit**
```bash
git add watcher docs/ROADMAP.md
git commit -m "feat: watcher command line, Windows startup task and README"
```

---

### Task 7: Review, scan and push

- [ ] **Step 1:** Run the full suite in the background and read the tail (`Expected: all passed`).
- [ ] **Step 2:** Run the vendor-name scan and read the whole output (`Expected: only the one accepted old-spec hit`). Also grep the diff for the token prefix, `api_hash`-looking values and any real phone number: `git diff origin/main..HEAD | grep -nE "amide_ing_[A-Za-z0-9_-]{20,}|[0-9a-f]{32}"` (`Expected: nothing`).
- [ ] **Step 3:** One independent opus review of the branch (`review.diff` from `origin/main..HEAD` for `watcher`, `tests/test_watcher_*`, `.gitignore`), with the plan's Review Focus verbatim and the rulings listed. One fix pass, each fix with a failing test first; minors to the ledger.
- [ ] **Step 4:** Push (plain push) after the scan is clean; delete the ledger directory.

---

## Self-review against the spec

- Commands `login`, `run`, `once`, `status`, `install-startup`, `remove-startup`: Task 6. Local files, secrets rules, example config, gitignore guard: Tasks 1 and 6.
- Register every group by title, read only mapped and enabled ones, backfill once then from the last id, text/PDF/image/xlsx, albums as one message, oversized and unwanted skipped: Task 4 tests (`registered`, `calls`, backfill, album, skip tests).
- Poll-based catch-up, gone check with two consecutive polls, quiet is not gone, unknown changes nothing, recovery reports active, undelivered report retried: Task 4.
- Idempotent resend, retry queue with growing waits, 4xx dropped, 401 pauses, queue cap that stops reading and loses nothing, atomic state written after queueing, secret-free logging: Tasks 2, 3 and 4.
- Telethon read only, flood sleeps: Task 5. Windows startup: Task 6.
- Spec items intentionally adjusted: no live update subscription (ruling above); `status` contacts Amide (to check the token) but never Telegram, matching the spec's wording that it reads local state; the spec's "banned" case is covered by `UserBannedInChannelError`.
- Type consistency: `Payload`, `Delivery.kind` values, `Access.state` values, `ChatState` fields and `Runner` constructor arguments are used identically in Tasks 2 to 6.

# Telegram watcher container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Telegram watcher as an optional container that runs beside Amide in the same stack, configured by environment variables, with one interactive Telegram login.

**Architecture:** `watcher/config.py` learns to read settings from environment variables (winning over `config.toml`); a new `serve` command waits for the Telegram login instead of exiting; `Dockerfile.watcher` packages only the `watcher/` package; CI builds, smoke-tests and (on tags) publishes `ghcr.io/<owner>/amide-watcher`; a second stack file adds the watcher service without changing the Amide service.

**Tech Stack:** Python 3.13, telethon, httpx, Docker, GitHub Actions, Compose.

**Spec:** `docs/superpowers/specs/2026-10-09-watcher-container-design.md`

## Global Constraints

- The watcher only reads: it never posts, reacts, joins, leaves or marks anything read.
- Secrets (token, API id, API hash) never appear in logs, `status` output, `repr` or error text; errors name the key, never its value.
- Existing `config.toml` users are unaffected; environment variables only add a second source and win when both are set.
- The Amide service block in the two stack files must be identical, and the existing Amide-only stack file must not change.
- No vendor names or price data anywhere (repo rule). No real tokens or Telegram credentials in any file.
- Test command: `.venv/Scripts/python.exe -m pytest -q -p no:warnings` (about 2 minutes; do not edit app files while it runs).
- Commit means commit and push (plain push, never force), only when the owner says "commit". Plan commit steps are for the executor to run only after that instruction.

## Review Focus

- Environment variable present but empty or whitespace: treated as unset, never as an empty secret.
- `TELEGRAM_API_ID` not a whole number: a clear error naming the variable, no value echoed.
- `serve` when Telegram is unreachable (not just signed out): it keeps retrying, never crashes the container into a restart loop.
- Login done in another process while `serve` waits: picked up by a fresh connection without restarting the container.
- A half-set environment (token set, API hash missing): the error names the missing key.

---

### Task 1: Settings from environment variables

**Files:**
- Modify: `watcher/config.py`
- Test: `watcher/tests/test_config_env.py`

**Interfaces:**
- Consumes: existing `load_config(home: Path | None = None) -> Config`, `ConfigError`, `_int`, `_text`
- Produces: `load_config` now also reads `AMIDE_URL`, `AMIDE_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `WATCHER_BACKFILL_DAYS`, `WATCHER_POLL_SECONDS`, `WATCHER_MAX_FILE_MB` (env wins over `config.toml`; no `config.toml` is needed when the required ones are set).

- [ ] **Step 1: Write the failing tests** (`watcher/tests/test_config_env.py`)

```python
import pytest

from watcher import config as cfg

ENV = {"AMIDE_URL": "http://amide:8000", "AMIDE_TOKEN": "env-token", "TELEGRAM_API_ID": "4321", "TELEGRAM_API_HASH": "envhash"}


def set_env(monkeypatch, **extra):
    for key in ("AMIDE_URL", "AMIDE_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "WATCHER_BACKFILL_DAYS", "WATCHER_POLL_SECONDS", "WATCHER_MAX_FILE_MB"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**ENV, **extra}.items():
        monkeypatch.setenv(key, value)


def test_a_container_needs_no_config_file(tmp_path, monkeypatch):
    set_env(monkeypatch)
    c = cfg.load_config(tmp_path)
    assert (c.amide_url, c.amide_token, c.telegram_api_id, c.telegram_api_hash, c.home) == ("http://amide:8000", "env-token", 4321, "envhash", tmp_path)
    assert (c.backfill_days, c.poll_seconds, c.max_file_mb) == (7, 300, 25)


def test_optional_numbers_come_from_the_environment(tmp_path, monkeypatch):
    set_env(monkeypatch, WATCHER_BACKFILL_DAYS="14", WATCHER_POLL_SECONDS="120", WATCHER_MAX_FILE_MB="10")
    c = cfg.load_config(tmp_path)
    assert (c.backfill_days, c.poll_seconds, c.max_file_mb) == (14, 120, 10)


def test_the_environment_wins_over_config_toml(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('amide_token = "file-token"\ntelegram_api_id = 1\ntelegram_api_hash = "filehash"\n', encoding="utf-8")
    set_env(monkeypatch, AMIDE_TOKEN="env-token")
    c = cfg.load_config(tmp_path)
    assert c.amide_token == "env-token" and c.telegram_api_hash == "envhash"


def test_a_blank_variable_counts_as_unset(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('amide_token = "file-token"\ntelegram_api_id = 1\ntelegram_api_hash = "filehash"\n', encoding="utf-8")
    set_env(monkeypatch, AMIDE_TOKEN="   ", TELEGRAM_API_ID="", TELEGRAM_API_HASH=" ")
    c = cfg.load_config(tmp_path)
    assert (c.amide_token, c.telegram_api_id, c.telegram_api_hash) == ("file-token", 1, "filehash")


def test_a_bad_number_names_the_variable_and_never_echoes_the_value(tmp_path, monkeypatch):
    set_env(monkeypatch, TELEGRAM_API_ID="12ab34")
    with pytest.raises(cfg.ConfigError) as err:
        cfg.load_config(tmp_path)
    assert "TELEGRAM_API_ID" in str(err.value) and "12ab34" not in str(err.value)


def test_a_half_set_environment_names_the_missing_key(tmp_path, monkeypatch):
    set_env(monkeypatch)
    monkeypatch.delenv("TELEGRAM_API_HASH")
    with pytest.raises(cfg.ConfigError) as err:
        cfg.load_config(tmp_path)
    assert "telegram_api_hash" in str(err.value) and "env-token" not in str(err.value)


def test_no_file_and_no_environment_still_says_what_to_do(tmp_path, monkeypatch):
    set_env(monkeypatch)
    for key in ENV:
        monkeypatch.delenv(key)
    with pytest.raises(cfg.ConfigError) as err:
        cfg.load_config(tmp_path)
    assert "config.toml" in str(err.value) and "AMIDE_TOKEN" in str(err.value)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings watcher/tests/test_config_env.py`
Expected: FAIL (the first tests fail with `No config.toml found`).

- [ ] **Step 3: Implement** in `watcher/config.py`: add after `_text`

```python
_ENV = {"amide_url": "AMIDE_URL", "amide_token": "AMIDE_TOKEN", "telegram_api_id": "TELEGRAM_API_ID", "telegram_api_hash": "TELEGRAM_API_HASH",
        "backfill_days": "WATCHER_BACKFILL_DAYS", "poll_seconds": "WATCHER_POLL_SECONDS", "max_file_mb": "WATCHER_MAX_FILE_MB"}
_ENV_INTS = {"telegram_api_id", "backfill_days", "poll_seconds", "max_file_mb"}


def _with_environment(raw: dict) -> dict:
    """Settings from environment variables, which win over config.toml so a container needs no file. A blank variable counts as unset."""
    out = dict(raw)
    for key, name in _ENV.items():
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        if key in _ENV_INTS:
            try:
                value = int(value)
            except ValueError:
                raise ConfigError(f"{name} must be a whole number") from None
        out[key] = value
    return out
```

and rewrite the top of `load_config`:

```python
def load_config(home: Path | None = None) -> Config:
    home = Path(home) if home is not None else default_home()
    path = home / "config.toml"
    raw: dict = {}
    if path.is_file():
        data = path.read_bytes()
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):          # saved as UTF-16 by PowerShell or an editor
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig", errors="replace")      # also drops the hidden marker some editors add
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            where = str(exc).split("(at ")[-1].rstrip(")") if "(at " in str(exc) else "unknown position"
            raise ConfigError(f"config.toml is not valid TOML (at {where}). Text values need double quotes, like amide_token = \"...\"") from None
    raw = _with_environment(raw)
    if not path.is_file() and not any(os.environ.get(name, "").strip() for name in ("AMIDE_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH")):
        raise ConfigError(f"No config.toml found in {home}. Copy watcher/config.example.toml there and fill it in, "
                          "or set AMIDE_TOKEN, TELEGRAM_API_ID and TELEGRAM_API_HASH as environment variables.")
```

keeping the rest of the function (url check and `Config(...)` construction) unchanged, and change the two helper messages from `"... is missing in config.toml"` to `"... is missing (set it in config.toml or as an environment variable)"`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings watcher/tests`
Expected: PASS (all watcher tests, old and new).

- [ ] **Step 5: Commit (only after the owner says "commit")**

```bash
git add watcher/config.py watcher/tests/test_config_env.py
git commit -m "feat: watcher settings can come from environment variables"
```

---

### Task 2: The `serve` command

**Files:**
- Modify: `watcher/cli.py`
- Test: `watcher/tests/test_serve.py`

**Interfaces:**
- Consumes: `TelethonClient(config)` with `connect()`, `is_authorized()`, `close()`; `_poll(config, *, forever)`
- Produces: `async _signed_in_client(config, log, *, make_client, sleep=asyncio.sleep, interval=30)`; `_poll(config, *, forever, wait_for_login=False)`; CLI command `serve`.

- [ ] **Step 1: Write the failing tests** (`watcher/tests/test_serve.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings watcher/tests/test_serve.py`
Expected: FAIL (`_signed_in_client` is not defined; `serve` is not a command).

- [ ] **Step 3: Implement** in `watcher/cli.py`: add before `_poll`

```python
async def _signed_in_client(config: Config, log, *, make_client, sleep=asyncio.sleep, interval: int = 30):
    """A connected, signed-in Telegram client. Used by `serve` in a container: until someone runs `login` in a console it waits, checking
    every `interval` seconds on a fresh connection (a login made in another process is only visible to a new connection) instead of
    exiting, so the container does not restart in a loop."""
    warned = False
    while True:
        tg = make_client(config)
        try:
            await tg.connect()
            if await tg.is_authorized():
                return tg
            if not warned:
                log.warning("Not signed in to Telegram yet. Open a console in this container and run: python -m watcher login")
                warned = True
        except Exception as exc:                           # never print message text or secrets: only the error's class
            log.error("Could not reach Telegram (%s)", type(exc).__name__)
        try:
            await tg.close()
        except Exception:
            pass
        await sleep(interval)
```

change `_poll`'s signature to `async def _poll(config: Config, *, forever: bool, wait_for_login: bool = False) -> int:` and its connection preamble to:

```python
    from watcher.telethon_client import TelethonClient
    log = get_logger(config.log_path)
    if wait_for_login:
        tg = await _signed_in_client(config, log, make_client=TelethonClient)
    else:
        tg = TelethonClient(config)
        while True:                                        # at sign-in the network may not be up yet
            try:
                await tg.connect()
                break
            except Exception as exc:
                log.error("Could not reach Telegram (%s)", type(exc).__name__)
                if not forever:
                    return 1
                await asyncio.sleep(30)
```

(the rest of `_poll` is unchanged), add `"serve"` to the tuple of simple subcommands in `main`, and replace the final run block with:

```python
    try:
        return asyncio.run(_poll(config, forever=True, wait_for_login=args.command == "serve"))
    except KeyboardInterrupt:
        return 0
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings watcher/tests`
Expected: PASS

- [ ] **Step 5: Commit (only after the owner says "commit")**

```bash
git add watcher/cli.py watcher/tests/test_serve.py
git commit -m "feat: watcher serve command waits for the Telegram login"
```

---

### Task 3: Image, CI and the stack file

**Files:**
- Create: `Dockerfile.watcher`, `docker-compose.portainer-with-watcher.yml`, `tests/test_stack_files.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `python -m watcher serve` and `status` (Tasks 1 and 2)
- Produces: image `ghcr.io/<owner>/amide-watcher`; stack file whose `amide` service equals the one in `docker-compose.portainer.yml`.

- [ ] **Step 1: Write the failing test** (`tests/test_stack_files.py`)

```python
"""The two Portainer stack files: both parse, and the Amide service is identical in each, so adding the watcher never changes it."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent


def load(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_the_amide_service_is_identical_in_both_stack_files():
    plain, with_watcher = load("docker-compose.portainer.yml"), load("docker-compose.portainer-with-watcher.yml")
    assert with_watcher["services"]["amide"] == plain["services"]["amide"]
    assert plain["volumes"].keys() <= with_watcher["volumes"].keys()


def test_the_watcher_service_is_set_up_from_the_environment_and_keeps_its_session():
    watcher = load("docker-compose.portainer-with-watcher.yml")["services"]["watcher"]
    assert watcher["image"].startswith("ghcr.io/xsatc77/amide-watcher")
    assert watcher["environment"]["AMIDE_URL"] == "http://amide:8000"
    assert {"AMIDE_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH"} <= set(watcher["environment"])
    assert "watcher-data:/watcher-data" in watcher["volumes"] and "ports" not in watcher and watcher["restart"] == "unless-stopped"
    assert "amide" in watcher["depends_on"]


def test_no_real_credentials_are_in_the_stack_or_the_dockerfile():
    for name in ("docker-compose.portainer-with-watcher.yml", "Dockerfile.watcher"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "amide_ing_" not in text and "api_hash =" not in text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_stack_files.py`
Expected: FAIL (the with-watcher stack file does not exist).

- [ ] **Step 3: Create the files**

`Dockerfile.watcher`:

```dockerfile
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AMIDE_WATCHER_HOME=/watcher-data

WORKDIR /app
COPY requirements-watcher.txt .
RUN pip install --no-cache-dir -r requirements-watcher.txt

# Only the watcher package: it imports nothing from the Amide app.
COPY watcher ./watcher

RUN useradd --create-home --uid 1000 watcher && mkdir -p /watcher-data && chown watcher:watcher /watcher-data
USER watcher
VOLUME ["/watcher-data"]

CMD ["python", "-m", "watcher", "serve"]
```

`docker-compose.portainer-with-watcher.yml`: copy `docker-compose.portainer.yml` exactly (its header comment, the `amide` service and the `amide-data` volume), update the header comment to say it also runs the Telegram watcher, and add the service and volume:

```yaml
  # The Telegram price-list watcher (optional). Fill in the three values below BEFORE you deploy, then follow the steps in the README
  # ("Telegram watcher"): create a token in Amide (Settings, Admin, Price list inbox), and run the one-time login in this container's console.
  watcher:
    image: ghcr.io/xsatc77/amide-watcher:latest
    container_name: amide-watcher
    depends_on:
      - amide
    environment:
      AMIDE_URL: "http://amide:8000"
      AMIDE_TOKEN: "paste-the-token-from-Amide-here"
      TELEGRAM_API_ID: "0"
      TELEGRAM_API_HASH: "paste-your-api-hash-here"
    volumes:
      - watcher-data:/watcher-data
    restart: unless-stopped

# (under the existing volumes: key)
  watcher-data:
```

`.github/workflows/ci.yml`, in the `docker` job after the existing Amide steps add:

```yaml
      # The watcher image: build it on every push, prove it starts and reads its environment (status never contacts Telegram), publish on a tag.
      - id: meta_watcher
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}-watcher
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest,enable=${{ startsWith(github.ref, 'refs/tags/v') }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.watcher
          load: true
          tags: amide-watcher-ci
          cache-from: type=gha,scope=watcher
          cache-to: type=gha,mode=max,scope=watcher
      - run: >
          docker run --rm -e AMIDE_URL=http://127.0.0.1:9 -e AMIDE_TOKEN=ci -e TELEGRAM_API_ID=1 -e TELEGRAM_API_HASH=ci
          amide-watcher-ci python -m watcher status | tee status.txt && grep -q "session: missing" status.txt
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile.watcher
          push: ${{ startsWith(github.ref, 'refs/tags/v') }}
          tags: ${{ steps.meta_watcher.outputs.tags }}
          labels: ${{ steps.meta_watcher.outputs.labels }}
          cache-from: type=gha,scope=watcher
```

(The existing Amide `cache-from` and `cache-to` lines get `scope=amide` so the two images do not overwrite each other's cache.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest -q -p no:warnings tests/test_stack_files.py`
Expected: PASS. CI itself proves the image build and the smoke run on the next push; there is no Docker on the development machine.

- [ ] **Step 5: Commit (only after the owner says "commit")**

```bash
git add Dockerfile.watcher docker-compose.portainer-with-watcher.yml tests/test_stack_files.py .github/workflows/ci.yml
git commit -m "feat: watcher container image, CI build and smoke test, stack file with the watcher"
```

---

### Task 4: Docs and release

**Files:**
- Modify: `README.md`, `watcher/README.md`, `docs/DEPLOYING.md`, `CHANGELOG.md`

- [ ] **Step 1:** Replace the "not part of the Docker image" notice at the top of `watcher/README.md` with a "Run it as a container (Docker, Portainer, Dockhand)" section: use `docker-compose.portainer-with-watcher.yml`; to add it to an existing stack, paste the `watcher` service and the `watcher-data` volume into the current stack and redeploy **without** "re-pull image" so the Amide container is left alone; fill in `AMIDE_TOKEN` (Settings, Admin, Price list inbox), `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` first; then open the watcher container's console and run `python -m watcher login` once (phone number, the code Telegram sends, the two-step password if set); the watcher starts by itself within 30 seconds; stop any watcher running on a PC; to remove it, delete the `watcher` service. Keep the existing PC instructions below it. Update `README.md` and `docs/DEPLOYING.md` section 7 to point to it. Add a `CHANGELOG.md` line.
- [ ] **Step 2:** Run the whole suite: `.venv/Scripts/python.exe -m pytest -q -p no:warnings` (expected: all pass).
- [ ] **Step 3: Commit and release (only after the owner says "commit")**

```bash
git add README.md watcher/README.md docs/DEPLOYING.md CHANGELOG.md
git commit -m "docs: run the Telegram watcher as a container"
git push
```

Then wait for the CI run to pass, tag `v1.0.1` (`git tag -a v1.0.1 -m "Amide v1.0.1"` and `git push origin v1.0.1`), and tell the owner to set the new `amide-watcher` package to Public on GitHub.

---

## Self-review

- **Spec coverage:** environment settings (Task 1), `serve` waiting for the login (Task 2), image, CI smoke test and publish, stack file, unchanged Amide service (Task 3), docs and upgrade steps for an existing install, release as v1.0.1 (Task 4).
- **Placeholders:** none; the stack file's three secret fields are deliberate placeholders for the user to fill in, and a test asserts no real credentials are present.
- **Type consistency:** `_signed_in_client`, `_poll(..., wait_for_login=)`, `load_config`, and the environment variable names are identical across tasks and the spec.
- **Review Focus coverage:** blank variables, bad numbers and half-set environments (Task 1 tests); unreachable Telegram and a login made in another process (Task 2 tests).

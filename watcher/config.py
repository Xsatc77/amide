"""The watcher's settings, read from config.toml in its home folder. Secrets never appear in repr, str or error text."""

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from watcher.safety import private_host


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
        raise ConfigError(f"{key} is missing (set it in config.toml or as an environment variable)")
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{key} must be a whole number from {low} to {high}")
    return value


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} is missing (set it in config.toml or as an environment variable)")
    return value.strip()


_ENV = {"amide_url": "AMIDE_URL", "amide_token": "AMIDE_TOKEN", "telegram_api_id": "TELEGRAM_API_ID", "telegram_api_hash": "TELEGRAM_API_HASH",
        "backfill_days": "WATCHER_BACKFILL_DAYS", "poll_seconds": "WATCHER_POLL_SECONDS", "max_file_mb": "WATCHER_MAX_FILE_MB"}
_ENV_INTS = {"telegram_api_id", "backfill_days", "poll_seconds", "max_file_mb"}


_SECRET_FILE_KEYS = ("amide_token", "telegram_api_id", "telegram_api_hash")
_MAX_SECRET_FILE = 4096
_TOKEN = re.compile(r"[!-~]{1,512}")


def _from_file(name: str) -> str:
    """The value of NAME_FILE (a Docker or Kubernetes secret file); the message names the variable, never the content."""
    path = os.environ.get(f"{name}_FILE", "").strip()
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            data = handle.read(_MAX_SECRET_FILE + 1)
    except OSError:
        raise ConfigError(f"{name}_FILE cannot be read") from None
    if len(data) > _MAX_SECRET_FILE:
        raise ConfigError(f"{name}_FILE is too large to be a secret")
    return data.decode("utf-8", errors="replace").strip()


def _with_environment(raw: dict) -> dict:
    """Settings from environment variables (or, for the three secrets, NAME_FILE), which win over config.toml so a container needs no file.
    NAME wins over NAME_FILE. A blank variable counts as unset."""
    out = dict(raw)
    for key, name in _ENV.items():
        value = os.environ.get(name, "").strip()
        if not value and key in _SECRET_FILE_KEYS:
            value = _from_file(name)
        if not value:
            continue
        if key in _ENV_INTS:
            try:
                value = int(value)
            except ValueError:
                raise ConfigError(f"{name} must be a whole number") from None
        out[key] = value
    return out


def _check_url(url: str, raw: dict) -> None:
    """The token travels to this address: it must be https, unless the host is private (a bare container name, a LAN address, loopback)
    or the owner opted in. No credentials, query or fragment in it (they would end up in logs and error text)."""
    try:
        parts = urlsplit(url)
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise ConfigError("amide_url is not a valid address") from None
    if parts.scheme not in ("http", "https"):
        raise ConfigError("amide_url must start with http:// or https://")
    if not host:
        raise ConfigError("amide_url needs a host name")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ConfigError("amide_url must be just the address of Amide, without a user name, password, query or fragment")
    if parts.scheme == "http" and not private_host(host):
        opted_in = raw.get("allow_insecure_http") is True or os.environ.get("AMIDE_ALLOW_INSECURE_HTTP", "").strip().lower() in ("1", "true", "yes")
        if not opted_in:
            raise ConfigError("amide_url uses plain http to a public address, so the token would cross the internet unencrypted. Use https://, "
                              "or set AMIDE_ALLOW_INSECURE_HTTP=1 if you accept that")
        logging.getLogger("watcher").warning("amide_url is plain http to a public address; the token is sent unencrypted")


def _warn_if_readable_by_others(path: Path) -> None:
    try:
        if os.name == "posix" and path.stat().st_mode & 0o077:
            logging.getLogger("watcher").warning("config.toml holds secrets and is readable by other users; run: chmod 600 config.toml")
    except OSError:
        pass


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
    if not path.is_file() and not any(os.environ.get(name + suffix, "").strip() for name in ("AMIDE_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH")
                                      for suffix in ("", "_FILE")):
        raise ConfigError(f"No config.toml found in {home}. Copy watcher/config.example.toml there and fill it in, "
                          "or set AMIDE_TOKEN, TELEGRAM_API_ID and TELEGRAM_API_HASH as environment variables.")
    url = _text(raw, "amide_url").rstrip("/") if "amide_url" in raw else "http://127.0.0.1:8000"
    _check_url(url, raw)
    if path.is_file():
        _warn_if_readable_by_others(path)
    token = _text(raw, "amide_token")
    if not _TOKEN.fullmatch(token):
        raise ConfigError("amide_token must be 1 to 512 visible characters with no spaces or line breaks")
    return Config(amide_url=url, amide_token=token, telegram_api_id=_int(raw, "telegram_api_id", None, 1, 2**31),
                  telegram_api_hash=_text(raw, "telegram_api_hash"), home=home, backfill_days=_int(raw, "backfill_days", 7, 1, 60),
                  poll_seconds=_int(raw, "poll_seconds", 300, 30, 3600), max_file_mb=_int(raw, "max_file_mb", 25, 1, 25))

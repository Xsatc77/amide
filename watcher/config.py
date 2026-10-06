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

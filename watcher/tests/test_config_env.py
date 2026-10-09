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

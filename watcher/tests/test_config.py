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


def test_a_trailing_slash_on_the_amide_url_is_removed(tmp_path):
    c = cfg.load_config(write(tmp_path, GOOD.replace("8000", "8000/")))
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


def test_a_file_saved_by_notepad_or_powershell_with_a_bom_or_utf16_still_loads(tmp_path):
    (tmp_path / "config.toml").write_bytes(b"\xef\xbb\xbf" + GOOD.encode("utf-8"))
    assert cfg.load_config(tmp_path).telegram_api_id == 1234
    (tmp_path / "config.toml").write_bytes(GOOD.encode("utf-16"))
    assert cfg.load_config(tmp_path).telegram_api_id == 1234


def test_a_toml_mistake_names_the_line_but_never_shows_the_text(tmp_path):
    (tmp_path / "config.toml").write_text('amide_token = test-token\n', encoding="utf-8")
    with pytest.raises(cfg.ConfigError) as err:
        cfg.load_config(tmp_path)
    assert "line 1" in str(err.value) and "test-token" not in str(err.value) and "quotes" in str(err.value)

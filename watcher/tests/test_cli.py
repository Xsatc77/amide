from pathlib import Path

import pytest

from watcher import cli, startup
from watcher.config import Config
from watcher.ports import Payload
from watcher.queue import DiskQueue
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
    assert "42" in text and "1 item," in text and "token accepted" in text and "session: present" in text
    for secret in ("test-token", "fakehash", "1234"):
        assert secret not in text


def test_status_says_when_the_session_is_missing(tmp_path):
    text = cli.format_status(config(tmp_path), State(tmp_path / "state.json"), DiskQueue(tmp_path / "queue"), "not checked")
    assert "session: missing" in text


def test_a_missing_config_exits_with_a_message_not_a_traceback(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AMIDE_WATCHER_HOME", str(tmp_path))
    assert cli.main(["status"]) == 2
    assert "config.toml" in capsys.readouterr().err


def test_unknown_commands_are_refused():
    with pytest.raises(SystemExit) as exit_:
        cli.main(["frobnicate"])
    assert exit_.value.code == 2


def test_the_startup_command_runs_the_watcher_at_logon_from_the_repo_folder():
    command = startup.startup_command(Path("C:/tmp/amide"), Path("C:/py/python.exe"))
    joined = " ".join(command).replace("\\", "/")
    assert command[0].lower() == "schtasks" and "/Create" in command and "ONLOGON" in command and "AmideWatcher" in command
    assert "-m watcher run" in joined and "C:/tmp/amide" in joined and "/F" in command
    assert startup.remove_command() == ["schtasks", "/Delete", "/TN", "AmideWatcher", "/F"]

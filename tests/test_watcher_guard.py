import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tracked():
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()


def test_no_secret_or_session_or_state_file_is_tracked():
    for name in tracked():
        base = name.rsplit("/", 1)[-1]
        assert base != "config.toml" and not base.endswith(".session") and not base.endswith(".session-journal"), name
        assert base != "state.json" and "/queue/" not in name, name


def test_gitignore_covers_the_watcher_secrets():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("config.toml", "*.session", "state.json", "queue/"):
        assert pattern in text


def test_the_example_config_holds_only_placeholders():
    text = (ROOT / "watcher" / "config.example.toml").read_text(encoding="utf-8")
    assert "YOUR_" in text and "amide_ing_" not in text
    for line in text.splitlines():
        if line.startswith("telegram_api_id"):
            assert line.split("=")[1].split("#")[0].strip() == "0"

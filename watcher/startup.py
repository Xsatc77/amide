"""Start the watcher when the owner signs in to Windows, using Task Scheduler (no admin rights needed)."""

import subprocess
import sys
from pathlib import Path

TASK = "AmideWatcher"


def startup_command(repo_root: Path, python: Path) -> list[str]:
    inner = f'cd /d "{repo_root}" && "{python}" -m watcher run'
    return ["schtasks", "/Create", "/SC", "ONLOGON", "/TN", TASK, "/TR", f'cmd /c "{inner}"', "/RL", "LIMITED", "/F"]


def remove_command() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK, "/F"]


def install() -> int:
    return subprocess.run(startup_command(Path(__file__).resolve().parent.parent, Path(sys.executable))).returncode


def remove() -> int:
    return subprocess.run(remove_command()).returncode

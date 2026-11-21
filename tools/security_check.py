"""The automated half of the All-In-One Security Check (see docs/SECURITY_CHECK.md).

    python tools/security_check.py            # everything, including the full test suite (about 6 minutes)
    python tools/security_check.py --quick    # skip the test suite

It runs from the project root with whatever Python runs it, calls pip-audit and bandit only when they are installed
(pip install pip-audit bandit), and prints one line per check. Exit status 0 means every check passed or was skipped for a stated
reason; 1 means at least one failed. It reads files and runs tools; it changes nothing and sends nothing anywhere except what
pip-audit itself looks up (the public vulnerability database).

Standard library only.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Shapes of real credentials. Each is built from pieces so this file does not match its own patterns when a scanner reads it.
SECRET_PATTERNS = {
    "AWS access key": re.compile("AK" + r"IA[0-9A-Z]{16}"),
    "GitHub token": re.compile(r"gh[pousr]_" + r"[A-Za-z0-9]{30,}"),
    "GitHub fine-grained token": re.compile("github_" + r"pat_[A-Za-z0-9_]{30,}"),
    "Anthropic key": re.compile("sk-" + r"ant-[A-Za-z0-9_-]{20,}"),
    "API secret key": re.compile("sk-" + r"[A-Za-z0-9]{32,}"),
    "Slack token": re.compile("xo" + r"x[baprs]-[A-Za-z0-9-]{10,}"),
    "Private key block": re.compile("-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----"),
    "Amide ingest token": re.compile("amide_" + r"ing_[A-Za-z0-9_-]{20,}"),
    "Amide health token": re.compile("amide_" + r"hlt_[A-Za-z0-9_-]{20,}"),
}
FORBIDDEN_TRACKED = re.compile(r"(^|/)(\.env|.*\.pem|.*\.key|.*\.session|.*\.session-journal|config\.toml|.*\.amidebackup|.*\.sqlite3?|amide\.db)$")
ALLOWED_TRACKED = {".env.example", "watcher/config.example.toml"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".pdf", ".zip", ".docx", ".xlsx", ".lock", ".gz"}
NOTE_WORDS = re.compile(r"\b(TODO|FIXME|HACK)\b")


def scan_text(text: str) -> list[str]:
    """The names of the secret shapes found in `text` (empty when it is clean)."""
    return [name for name, rx in SECRET_PATTERNS.items() if rx.search(text)]


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [line for line in out.stdout.splitlines() if line]


def check_tracked_hygiene() -> tuple[bool, str]:
    bad = [f for f in tracked_files() if FORBIDDEN_TRACKED.search(f) and f not in ALLOWED_TRACKED]
    return (not bad, "no secret-bearing file types are tracked" if not bad else "tracked, and should not be: " + ", ".join(bad[:8]))


def check_secret_patterns() -> tuple[bool, str]:
    hits = []
    for name in tracked_files():
        path = ROOT / name
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            found = scan_text(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        hits += [f"{name} ({kind})" for kind in found]
    return (not hits, "no credential-shaped strings in tracked files" if not hits else "found: " + "; ".join(hits[:8]))


def check_note_words() -> tuple[bool, str]:
    hits = []
    for name in tracked_files():
        if not name.startswith(("app/", "watcher/", "tools/", "migrations/")) or not name.endswith(".py") or name == "tools/security_check.py":
            continue
        for number, line in enumerate((ROOT / name).read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if NOTE_WORDS.search(line):
                hits.append(f"{name}:{number}")
    return (not hits, "no TODO, FIXME or HACK comments in the code" if not hits else "open notes at: " + ", ".join(hits[:8]))


def check_required_docs() -> tuple[bool, str]:
    needed = {"SECURITY.md": None, "LICENSE": None, ".github/CODEOWNERS": None, "docs/ARCHITECTURE.md": None,
              "docs/DEPLOYING.md": ("Rolling Back", "Logs And Monitoring", "Secrets At Rest"), "docs/SECURITY_CHECK.md": None}
    missing = []
    for name, words in needed.items():
        path = ROOT / name
        if not path.is_file():
            missing.append(name)
        elif words:
            text = path.read_text(encoding="utf-8", errors="ignore")
            missing += [f"{name} lacks '{word}'" for word in words if word not in text]
    return (not missing, "the handoff and security documents exist" if not missing else "missing: " + "; ".join(missing))


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def module_available(name: str) -> bool:
    return subprocess.run([sys.executable, "-c", f"import {name}"], capture_output=True).returncode == 0


def check_pip_audit() -> tuple[bool | None, str]:
    if not module_available("pip_audit") and not shutil.which("pip-audit"):
        return None, "skipped: pip install pip-audit"
    cmd = [sys.executable, "-m", "pip_audit", "--require-hashes", "--no-deps", "--progress-spinner", "off"]
    results = []
    for lock in ("requirements.lock", "requirements-watcher.lock"):
        done = run(cmd + ["-r", lock], timeout=600)
        results.append((lock, done.returncode == 0, (done.stdout + done.stderr).strip().splitlines()[-1:] or [""]))
    ok = all(r[1] for r in results)
    return ok, "both lock files have no known vulnerabilities" if ok else "; ".join(f"{n}: {t[0]}" for n, good, t in results if not good)


def check_bandit() -> tuple[bool | None, str]:
    if not module_available("bandit"):
        return None, "skipped: pip install bandit"
    done = run([sys.executable, "-m", "bandit", "-q", "-r", "app", "watcher", "-x", "app/static,watcher/tests", "-lll"], timeout=600)
    return done.returncode == 0, "no high-severity findings" if done.returncode == 0 else "high-severity findings: run bandit -r app -lll"


def check_tests() -> tuple[bool, str]:
    done = run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings"], timeout=1800)
    tail = (done.stdout.strip().splitlines() or [""])[-1]
    return done.returncode == 0, tail


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quick", action="store_true", help="skip the test suite")
    args = ap.parse_args()
    checks = [("Tracked file types", check_tracked_hygiene), ("Credential patterns", check_secret_patterns),
              ("Open TODO notes", check_note_words), ("Handoff documents", check_required_docs),
              ("Dependency audit", check_pip_audit), ("Static analysis", check_bandit)]
    if not args.quick:
        checks.append(("Full test suite", check_tests))
    failed = 0
    for title, fn in checks:
        ok, message = fn()
        mark = "SKIP" if ok is None else "PASS" if ok else "FAIL"
        failed += ok is False
        print(f"[{mark}] {title}: {message}")
    print("\nAutomated checks:", "all passed or skipped" if not failed else f"{failed} failed")
    print("Still manual: the browser and device checks, the scheduled scan workflow, and the review prompts (docs/SECURITY_CHECK.md).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""The All-In-One Security Check: its scanner must find a planted positive (a scan that finds nothing proves nothing), and its inputs must exist."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("security_check", ROOT / "tools" / "security_check.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def test_the_secret_scan_finds_planted_credentials_and_ignores_ordinary_text():
    planted = {
        "AWS access key": "AK" + "IAABCDEFGHIJKLMNOP12",
        "GitHub token": "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
        "Private key block": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "Anthropic key": "sk-" + "ant-" + "abcdefghijklmnopqrstuvwxyz012345",
        "Amide ingest token": "amide_" + "ing_" + "abcdefghijklmnopqrstuvwxyz0123456789",
    }
    for kind, text in planted.items():
        assert kind in sc.scan_text(f"token = '{text}'"), kind
    assert sc.scan_text("a normal sentence about keys, tokens and sk-short, with amide_ing_x") == []


def test_secret_bearing_file_types_are_recognised_and_the_examples_are_allowed():
    for name in (".env", "data/amide.db", "watcher/config.toml", "x.session", "backup.amidebackup", "certs/site.pem"):
        assert sc.FORBIDDEN_TRACKED.search(name), name
    for name in (".env.example", "watcher/config.example.toml", "app/main.py"):
        assert name in sc.ALLOWED_TRACKED or not sc.FORBIDDEN_TRACKED.search(name), name


def test_the_repository_passes_its_own_file_checks():
    for check in (sc.check_tracked_hygiene, sc.check_secret_patterns, sc.check_note_words, sc.check_required_docs):
        ok, message = check()
        assert ok, message


def test_the_document_carries_all_six_review_prompts_and_the_release_gate():
    text = (ROOT / "docs" / "SECURITY_CHECK.md").read_text(encoding="utf-8")
    for heading in ("### 3.1 Security Review", "### 3.2 Hostile Re-Review", "### 3.3 Supply-Chain Review", "### 3.4 Maintainability Review",
                    "### 3.5 Patch", "### 3.6 Release Gate", "## 6. The Release Gate", "## 5. Manual Checks"):
        assert heading in text, heading

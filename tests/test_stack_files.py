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

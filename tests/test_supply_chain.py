"""Guards for the supply chain: pinned requirements, hash-pinned lock files, digest-pinned base images, a locked-down CI workflow."""
import io
import re
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
LOCKS = ("requirements.lock", "requirements-watcher.lock")
INPUTS = ("requirements.txt", "requirements-dev.txt", "requirements-watcher.txt", "tools/requirements.txt")


def requirement_lines(name):
    for raw in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith(("#", "-r ")):
            yield line


def test_every_input_requirement_is_pinned_exactly():
    for name in INPUTS:
        for line in requirement_lines(name):
            assert re.match(r"^[A-Za-z0-9_.\-]+(\[[a-z,]+\])?==[0-9][^=<>~!\s]*$", line), f"{name}: {line!r} is not an exact == pin"


@pytest.mark.parametrize("lock", LOCKS)
def test_lock_file_has_a_hash_for_every_package(lock):
    text = (ROOT / lock).read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", "\n".join(l for l in text.splitlines() if not l.startswith("#")).strip())
    assert len(blocks) > 5
    for block in blocks:
        assert re.match(r"^[A-Za-z0-9_.\-]+==[0-9]\S*", block), block[:60]
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})\b", block)
        assert hashes, f"{lock}: no sha256 for {block.splitlines()[0]}"
    # No package is listed without a hash and none is a range.
    assert all("--hash" in b for b in blocks)


def test_lock_files_cover_the_direct_requirements_at_the_same_version():
    for source, lock in (("requirements.txt", "requirements.lock"), ("requirements-watcher.txt", "requirements-watcher.lock")):
        pinned = {}
        for line in (ROOT / lock).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)", line)
            if m:
                pinned[m.group(1).lower().replace("_", "-")] = m.group(2)
        for line in requirement_lines(source):
            name, version = re.sub(r"\[.*\]", "", line).split("==")
            assert pinned.get(name.lower().replace("_", "-")) == version, f"{lock} does not pin {line}"


def test_lock_uses_the_headless_opencv_only():
    names = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert re.search(r"^opencv-python-headless==", names, re.M)
    assert not re.search(r"^opencv-python==", names, re.M)


@pytest.mark.parametrize("dockerfile,lock", [("Dockerfile", "requirements.lock"), ("Dockerfile.watcher", "requirements-watcher.lock")])
def test_dockerfiles_install_from_the_hashed_lock_on_a_pinned_base(dockerfile, lock):
    text = (ROOT / dockerfile).read_text(encoding="utf-8")
    assert re.search(r"^FROM python:3\.13-slim@sha256:[0-9a-f]{64}$", text, re.M)
    assert re.search(rf"^RUN pip install .*--require-hashes .*-r {re.escape(lock)}( && pip uninstall -y pip setuptools wheel)?$", text, re.M)
    assert "pip uninstall -y pip setuptools wheel" in text          # the build tools (and the libraries pip bundles) are not shipped in the image
    assert "zlib1g" in text
    assert "PIP_DISABLE_PIP_VERSION_CHECK=1" in text
    assert "libgl1" not in text


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_workflow_actions_are_pinned_to_commit_shas():
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", text, re.M)
    assert uses
    for ref, rest in uses:
        assert re.match(r"^[\w.\-]+/[\w.\-]+@[0-9a-f]{40}$", ref), f"{ref} is not pinned to a full commit SHA"
        assert re.match(r"^\s*# v\d+\.\d+\.\d+\s*$", rest), f"{ref} lacks a version comment"


def test_workflow_permissions_are_least_privilege():
    data = workflow()
    assert data["permissions"] == {"contents": "read"}
    for name, job in data["jobs"].items():
        wants_packages = "packages" in (job.get("permissions") or {})
        assert wants_packages == (name == "publish"), f"only the publish job may write packages ({name})"
    publish = data["jobs"]["publish"]
    assert publish["if"] == "startsWith(github.ref, 'refs/tags/v')"
    steps = [s for s in publish["steps"] if str(s.get("uses", "")).startswith("docker/build-push-action")]
    assert steps and all(s["with"]["push"] is True and s["with"]["sbom"] is True and s["with"]["provenance"] == "mode=max" for s in steps)


def test_workflow_checkouts_do_not_persist_credentials_and_the_audit_runs():
    data = workflow()
    for job in data["jobs"].values():
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False
    audit = "\n".join(str(s.get("run", "")) for s in data["jobs"]["audit"]["steps"])
    assert "pip-audit" in audit and "--require-hashes" in audit and "requirements.lock" in audit
    assert "audit" in data["jobs"]["docker"]["needs"]
    ignore = (ROOT / ".github/pip-audit-ignore.txt").read_text(encoding="utf-8")
    assert not [l for l in ignore.splitlines() if l.strip() and not l.strip().startswith("#")]


def test_dependabot_watches_packages_images_and_actions():
    data = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    assert {u["package-ecosystem"] for u in data["updates"]} == {"pip", "docker", "github-actions"}


def test_openpyxl_uses_defusedxml():
    from openpyxl.xml import DEFUSEDXML
    assert DEFUSEDXML is True


def _xlsx_with_entity() -> bytes:
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["a", 1])
    buffer = io.BytesIO()
    book.save(buffer)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                body = b'<!DOCTYPE worksheet [<!ENTITY lol "lol">]>' + body
            dst.writestr(info, body)
    return out.getvalue()


def test_a_spreadsheet_declaring_an_xml_entity_is_refused_by_openpyxl_and_by_the_ingest_reader():
    from defusedxml import EntitiesForbidden
    from openpyxl import load_workbook
    from app.ingest import readers

    data = _xlsx_with_entity()
    with pytest.raises(ValueError) as caught:       # openpyxl wraps the parser's refusal
        load_workbook(io.BytesIO(data), read_only=False)
    assert isinstance(caught.value.__context__, EntitiesForbidden) or isinstance(caught.value.__cause__, EntitiesForbidden)
    with pytest.raises(readers.ReadError):
        readers.read_xlsx(data)


# ---------------------------------------------------------------- the scheduled security scans

SCAN = ROOT / ".github/workflows/security-scan.yml"


def scan_workflow():
    return yaml.safe_load(SCAN.read_text(encoding="utf-8"))


def test_security_scan_workflow_exists_with_its_triggers_and_jobs():
    data = scan_workflow()
    triggers = data.get("on", data.get(True))
    assert {"workflow_dispatch", "schedule", "push"} <= set(triggers)
    assert triggers["push"]["tags"] == ["v*"] and "branches" not in triggers["push"]
    assert {"image", "config", "dynamic", "secrets"} <= set(data["jobs"])
    assert (ROOT / ".github/zap/auth_probe.py").is_file() and (ROOT / ".gitleaks.toml").is_file()


def test_security_scan_actions_are_pinned_to_commit_shas_and_images_to_digests():
    text = SCAN.read_text(encoding="utf-8")
    uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", text, re.M)
    assert uses
    for ref, rest in uses:
        assert re.match(r"^[\w.\-]+/[\w.\-]+@[0-9a-f]{40}$", ref), f"{ref} is not pinned to a full commit SHA"
        assert re.match(r"^\s*# v\d+\.\d+\.\d+\s*$", rest), f"{ref} lacks a version comment"
    images = re.findall(r"^\s*(?:ZAP|GITLEAKS)_IMAGE:\s*(\S+)$", text, re.M)
    assert len(images) == 2
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in images)


def test_security_scan_has_least_privilege_permissions_and_no_persisted_credentials():
    data = scan_workflow()
    assert data["permissions"] == {"contents": "read"}
    for name, job in data["jobs"].items():
        assert set(job.get("permissions") or {}) <= {"security-events"}, f"{name} asks for more than read access"
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False
    assert data["jobs"]["secrets"]["steps"][0]["with"]["fetch-depth"] == 0
    assert "secrets." not in SCAN.read_text(encoding="utf-8")


def test_gitleaks_config_allows_only_exact_placeholders_never_a_catch_all():
    import tomllib
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert config["extend"]["useDefault"] is True
    allowlists = config.get("allowlists", []) + ([config["allowlist"]] if "allowlist" in config else [])
    assert allowlists
    real_looking = ["AKIA" + "IOSFODNN7EXAMPLE", "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8", "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
                    "-----BEGIN RSA " + "PRIVATE KEY-----", "xoxb-" + "1234567890-abcdefghijklmnop", "password = \"hunter2hunter2\"", "", "token"]
    for entry in allowlists:
        assert not entry.get("paths"), "allow-list whole files only by exact placeholder text, not by path"
        assert entry.get("regexes")
        for pattern in entry["regexes"]:
            assert pattern.strip() not in {".*", ".+", "^.*$", "(?s).*", r"[\s\S]*", r"\w+", r".*?"}, f"catch-all allow-list pattern {pattern!r}"
            compiled = re.compile(pattern)
            assert compiled.search("") is None, f"{pattern!r} matches an empty string"
            for sample in real_looking:
                assert compiled.search(sample) is None, f"{pattern!r} would hide {sample[:12]}..."
            assert len(pattern) >= 8, f"{pattern!r} is too short to be an exact placeholder"

# Supply Chain

What the Amide images are built from, how that is pinned, and how to refresh it.

## The Files

- `requirements.txt` and `requirements-watcher.txt` are the human-edited inputs: every package pinned with `==`. `requirements-dev.txt` adds the test tools (CI only). `tools/requirements.txt` is for the one-off card importer and is never installed in an image.
- `requirements.lock` and `requirements-watcher.lock` are generated from those inputs and list every package, direct and transitive, with the SHA-256 of every file PyPI publishes for that release. The Dockerfiles install only from them, with `pip install --require-hashes --no-deps`, so pip refuses any file whose hash differs and any package not listed.
- The base image is `python:3.13-slim` pinned by digest (the multi-arch index digest).

## Regenerating The Locks

After editing a requirements file:

```bash
python tools/make_lock.py requirements.txt requirements.lock --exclude opencv-python --extra antlr4-python3-runtime==4.9.3
python tools/make_lock.py requirements-watcher.txt requirements-watcher.lock --native
```

The script (standard library only) asks pip to resolve for linux/amd64 on Python 3.13 using binary wheels only, whatever machine it runs on, then reads the hashes from pypi.org. Review the diff and commit the inputs and the locks together; a test fails if they disagree.

- `--exclude opencv-python`: RapidOCR declares the full OpenCV as a dependency; Amide installs the headless build (no libGL needed in the image) and the lock leaves the full one out.
- `--native` (watcher only): the Telethon dependency `pyaes` ships only as a source archive, so there is no wheel to resolve for a target platform. The watcher set is pure Python, so resolving on the current machine gives the same packages. pip builds `pyaes` in the image from the hashed source archive; its build tools (setuptools) are fetched by pip into a throwaway build environment and are not covered by the lock.

## Updates And Auditing

- Dependabot (`.github/dependabot.yml`) opens weekly pull requests for packages, the base-image digest and the GitHub Actions (each pinned to a full commit SHA). For a package update, regenerate the lock files on that pull request.
- The CI `audit` job runs `pip-audit` against both lock files and fails on any known vulnerability. An advisory that has been judged not to apply is listed, with a reason, in `.github/pip-audit-ignore.txt` (empty today).
- Run it yourself: `pip install pip-audit`, then `pip-audit --require-hashes --disable-pip -r requirements.lock`.

## Refreshing The Base Image By Hand

Read the digest of the current index with `docker buildx imagetools inspect python:3.13-slim` (the `Digest:` line) and replace the `sha256:` value in the `FROM` line of `Dockerfile` and `Dockerfile.watcher`. Dependabot does this on its own once a week.

## Licences Worth Knowing

PyMuPDF (used only by the developer-only `tools/import_cards.py`) is AGPL-3.0, which is why it is kept out of `requirements.txt` and out of the images. `defusedxml` is added because openpyxl uses it, when installed, to refuse XML entity expansion in untrusted spreadsheets, which matters because the Telegram ingest reads spreadsheets posted by third parties.

## Scheduled Scans

`.github/workflows/security-scan.yml` holds the scans that are too slow or too noisy for every push. It runs by hand (Actions, Security Scan, Run workflow), every Monday at 04:23 UTC, and whenever a `v*` tag is pushed. It uses no secrets and no tokens beyond the read-only one GitHub gives every run; nothing is pushed anywhere.

| Job | What It Does | Fails When |
| --- | --- | --- |
| Image Vulnerabilities | Builds the Amide image and the watcher image from their Dockerfiles, then scans each with Trivy for operating system and Python package vulnerabilities | A HIGH or CRITICAL finding has a fix available (unfixed ones are hidden) |
| Dockerfile Misconfigurations | Trivy configuration scan of the Dockerfiles. Trivy has no checks for compose files, so those are covered by the tests | A HIGH or CRITICAL misconfiguration |
| Running App Scan | Starts the fresh image with a throwaway volume and `AMIDE_REGISTRATION=open`, waits for `/healthz`, runs `.github/zap/auth_probe.py`, then the OWASP ZAP baseline scan | The probe finds a missing header, a version banner or a stack trace. The ZAP step only reports |
| Secret Scan | gitleaks over every commit of the full history, with `.gitleaks.toml` | Any finding |

The tools are pinned: Trivy through the action at a full commit SHA, ZAP and gitleaks as container images pinned by digest (the digest sits in the `env` block of the job). Refresh a digest with `docker buildx imagetools inspect <image>:<tag>`; Dependabot refreshes the action SHAs.

### Reading The Results

- Open the run in the Actions tab. A red job shows its findings in the log: Trivy prints a table (package, installed version, fixed version), gitleaks prints the file, commit and rule, and the probe prints one `FAIL` line per problem.
- Every run keeps its reports for 30 days as artifacts: `trivy-amide`, `trivy-watcher` and `trivy-config` (JSON), `zap-report` (HTML and JSON) and `gitleaks-report` (JSON, secrets redacted).
- The ZAP log ends with a summary of alert types by risk. Medium and low alerts are advice to read, not a failure; fix the ones that apply and note the ones that do not.
- A Trivy finding with a fix usually means rebuilding on a newer base image or regenerating the lock files (see above). A finding in a package with no fix never appears, by design.
- The signed-in probe registers a random throwaway account inside the container, signs in, loads the main pages and checks the security headers, no-store caching on private pages, no `Server` banner, and no stack trace or framework name in 404, 405 and 422 style answers. The account disappears with the container.

### Adding An Allow-List Entry

- Vulnerabilities: add the id (for example a CVE number) to `.trivyignore`, one per line, with a comment saying why it does not apply and when to look again. The file starts empty.
- Secrets: a real secret is never allowed. Rotate it, then remove it from history if you can. For an obvious placeholder, add the exact text (regex-escaped) to the `regexes` list in `.gitleaks.toml`, with a comment saying where it lives. Never add a pattern that could match a real secret; a test rejects catch-all entries.
- Headers or error pages: fix the app; the probe has no ignore list.

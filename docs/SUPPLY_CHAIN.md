# Supply Chain

What the Amide images are built from, how that is pinned, and how to refresh it.

## The Files

- `requirements.txt` and `requirements-watcher.txt` are the human-edited inputs: every package pinned with `==`. `requirements-dev.txt` adds the test tools (CI only). `tools/requirements.txt` is for the one-off card importer and is never installed in an image.
- `requirements.lock` and `requirements-watcher.lock` are generated from those inputs and list every package, direct and transitive, with the SHA-256 of every file PyPI publishes for that release. The Dockerfiles install only from them, with `pip install --require-hashes --no-deps`, so pip refuses any file whose hash differs and any package not listed.
- The base image is `python:3.13-slim` pinned by digest (the multi-arch index digest).

## Regenerating The Locks

After editing a requirements file:

```bash
python tools/make_lock.py requirements.txt requirements.lock --exclude opencv-python
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

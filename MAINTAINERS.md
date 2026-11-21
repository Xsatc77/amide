# Maintainers

**Owner:** the repository owner, [@Xsatc77](https://github.com/Xsatc77). Report problems privately as described in [SECURITY.md](SECURITY.md); everything else goes in the repository's issues.

## What A New Maintainer Needs To Know

- **How it fits together:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (request flow, who owns what, a checklist for new routes).
- **Running and testing it:** the README. The full suite is `pytest` from the project root; the all-in-one security procedure is [docs/SECURITY_CHECK.md](docs/SECURITY_CHECK.md) and `python tools/security_check.py`.
- **Deploying, rolling back, logs:** [docs/DEPLOYING.md](docs/DEPLOYING.md).
- **Releases:** push to `main`, wait for green CI, tag `vX.Y.Z`. A version tag publishes the app and watcher images. Patch numbers stop at .29, then the minor number increases.
- **Dependencies:** Python packages install from hashed lock files made by `tools/make_lock.py` (see [docs/SUPPLY_CHAIN.md](docs/SUPPLY_CHAIN.md)); Dependabot proposes updates, and each one needs the lock regenerated and the full suite run.
- **Secrets:** nothing secret is committed. Stored 2FA seeds, AI keys and calendar addresses are sealed with `data/secret.key` (or `AMIDE_SECRET_KEY`). Do not change or lose that key on an install that already has data.

## Rules The Project Keeps

- No vendor names, price lists, real people or real domains in any committed file, test or commit message.
- Real lab reports, photos and other personal files never enter the repository.
- Every route that takes an id checks that the row belongs to the signed-in person (or is on the reviewed shared list), and has a line in the cross-user test.
- No inline scripts or inline event handlers in templates (a test enforces it).

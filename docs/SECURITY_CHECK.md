# All-In-One Security Check

One repeatable procedure for answering "is this safe enough to ship, and to hand to someone else?". Run it before a release, after a large change, and at least every quarter. It has four parts: the automated checks, the review prompts, the hostile probes, and the manual checks, followed by the release gate that turns all of it into a decision.

Nothing here needs a secret. Nothing here may put a real vendor name, price list, person, domain or credential into the repository.

## 1. Automated Checks

Run these from the project root.

```powershell
python tools/security_check.py            # everything, including the full test suite (about 6 minutes)
python tools/security_check.py --quick    # everything except the tests
```

`tools/security_check.py` reports one PASS, FAIL or SKIP line each for:

- tracked file types that must never be committed (`.env`, keys, Telegram sessions, databases, backups);
- credential-shaped strings in every tracked file;
- open TODO, FIXME and HACK notes in the code;
- the handoff and security documents (this file, `SECURITY.md`, `docs/ARCHITECTURE.md`, the rollback, logging and secrets sections of `docs/DEPLOYING.md`, `.github/CODEOWNERS`);
- the dependency audit of both hashed lock files (`pip-audit`, install it with `pip install pip-audit`);
- static analysis (`bandit`, high severity only; `pip install bandit`);
- the full test suite.

The GitHub side runs on every push (`.github/workflows/ci.yml`: tests, dependency audit, image build with an OCR smoke test) and, on a schedule or by hand, `.github/workflows/security-scan.yml`:

- **Image vulnerabilities:** Trivy on the app and watcher images, plus Dockerfile checks.
- **Running app:** the image is started and scanned with OWASP ZAP, then a signed-in probe (`.github/zap/auth_probe.py`) checks the security headers and the error pages.
- **Secrets:** gitleaks over the full git history. A reviewed false positive goes in `.gitleaksignore` by its exact fingerprint, never as a catch-all.

Start it with `gh workflow run security-scan.yml`, then read each job. ZAP warnings do not fail the job; read them.

Two more automated suites are not part of a normal test run:

```powershell
$env:AMIDE_REALHTTP = "1"; .venv\Scripts\pytest tests/test_realhttp.py     # real sockets: request smuggling shapes, header floods, proxy headers
.venv\Scripts\pytest tests/test_hostile_files.py tests/test_output_encoding.py tests/test_security_races.py
```

## 2. The Name And Secret Scans

A scan that finds nothing proves nothing until it has found something. Before trusting a "clean" result, plant a known positive (a made-up credential, or a name from your local list), confirm the scan reports it, and remove it again.

- **Credentials:** `tools/security_check.py` covers the tracked files; gitleaks in the scheduled workflow covers the whole history.
- **Private names:** keep a list of vendor names, people and domains that must never be committed in a file outside the repository, and scan the added lines of every diff against it before each push. The list itself must never be committed.

## 3. The Review Prompts

Run these in order, each in a fresh session that has read `docs/ARCHITECTURE.md`. Reviews do not patch; the patch prompt is a separate, explicit step. Keep every finding's ID so the patch step and the changelog can refer to it.

### 3.1 Security Review

```
Act as a senior application security reviewer.

Do not modify code yet.

Review this project for security issues using OWASP ASVS-style thinking and secure SDLC practices.

Focus on:
- authentication and authorization flaws
- broken access control
- injection risks: SQL, command, template, LDAP, NoSQL, path traversal
- insecure file upload/download handling
- SSRF, XXE, deserialization, prototype pollution, request smuggling where relevant
- secret exposure and hardcoded credentials
- unsafe environment variable handling
- insecure CORS, cookies, sessions, JWTs, OAuth, API keys
- missing input validation and output encoding
- cryptography mistakes
- logging of sensitive data
- weak error handling that leaks internals
- dependency and supply-chain risks
- dangerous default config
- insecure Docker, CI/CD, or deployment assumptions

Output format:

1. Executive summary
2. Threat model assumptions
3. Findings table:
   - ID
   - Severity: critical/high/medium/low/info
   - File/function
   - Issue
   - Exploit scenario
   - Recommended fix
   - Confidence
4. False-positive notes
5. Items requiring human confirmation
6. Do not patch yet
```

### 3.2 Hostile Re-Review

```
Act as a hostile security reviewer trying to prove the previous review missed something.

Assume the code will be exposed to the internet.

Find:
- security assumptions that were not stated
- trust boundaries that were not modeled
- places where user input reaches sensitive sinks
- privilege escalation paths
- IDOR/BOLA risks
- race conditions
- unsafe background jobs, queues, cron tasks, webhooks, or file processors
- unsafe admin/debug/dev-only routes
- missing rate limits
- missing audit logs
- missing abuse-case handling

Do not repeat the previous findings unless you add new evidence.

Output only:

1. Newly discovered issues
2. Previously underrated issues
3. Security assumptions that must be verified manually
4. Highest-risk attack path
```

### 3.3 Supply-Chain Review

```
Act as a software supply-chain security reviewer.

Review the project dependencies, build process, package scripts, lockfiles, Dockerfiles, CI/CD config, install instructions, and generated code.

Check for:
- unnecessary dependencies
- abandoned or suspicious packages
- typosquatting risk
- packages with broad install/postinstall scripts
- missing lockfiles
- dependency confusion risk
- unsafe GitHub Actions or CI secrets exposure
- unpinned Docker images
- unpinned GitHub Actions
- missing SBOM/provenance notes
- build steps that download remote code at build time
- scripts that execute shell commands unsafely
- missing reproducibility notes

Output:

1. Supply-chain risk summary
2. Dependency risk table
3. Build/CI risks
4. Required changes before release
5. Nice-to-have hardening
6. Exact files to inspect manually
```

### 3.4 Maintainability Review

```
Act as a senior maintainer inheriting this project six months from now.

Review the code for maintainability, clarity, and safe future modification.

Do not optimize prematurely. Do not rewrite for style alone.

Find:
- unclear architecture
- duplicated logic
- hidden coupling
- confusing names
- overly large files/functions
- missing comments where intent is non-obvious
- comments that lie or are stale
- missing error boundaries
- missing tests around critical behavior
- fragile assumptions
- config scattered across the codebase
- business logic mixed with UI or transport code
- security-sensitive code without explanation
- places where a future developer is likely to break security accidentally

Output:

1. Maintainability summary
2. Top 10 future-breakage risks
3. Refactor recommendations ranked by value/risk
4. Files that need comments or documentation
5. Tests that should exist before future changes
6. Do not patch yet
```

### 3.5 Patch

Only after the findings above are approved by name.

```
Patch the project based on the approved findings below.

Rules:
- Preserve existing behavior unless a finding requires changing it.
- Do not remove features.
- Make the smallest safe changes.
- Add tests for every security-sensitive fix.
- Update documentation if setup, config, or behavior changes.
- Do not introduce new dependencies unless necessary.
- If a dependency is necessary, justify it.
- Keep a changelog of every file changed.

Approved findings to fix:
[paste finding IDs here]

Output:

1. Summary of changes
2. Files changed
3. Security issues fixed
4. Tests added/updated
5. Manual verification steps
6. Remaining risks
```

### 3.6 Release Gate

```
Act as a release gate reviewer.

Decide whether this project is safe and maintainable enough to hand to another developer or deploy to a limited environment.

Check:
- known security findings
- unresolved TODO/FIXME/HACK comments
- secrets or test credentials
- debug routes
- verbose errors
- missing environment documentation
- missing install/run/test instructions
- missing rollback instructions
- missing logs/monitoring notes
- missing ownership notes
- dependency risks
- license issues
- tests passing or not documented
- migration/setup risks

Output:

Release decision:
- ship
- ship with conditions
- do not ship

Then provide:

1. Blocking issues
2. Non-blocking issues
3. Required manual checks
4. Handoff notes
5. Recommended next maintainer tasks
```

## 4. Hostile Probes

The prompts find what a reader can see. These find what only running code shows. Each has tests in the repository; extend them when you add a feature in the same area.

| Probe | What it does | Where it lives |
|---|---|---|
| Red team, web layer | Tries to break headers, redirects, body-size caps, SSRF guards, file downloads, vendor access rules | `tests/test_redteam_web.py`, `tests/test_security_followups_web.py` |
| Red team, accounts | Tries to bypass sign-up rules, throttles, sessions, 2FA, passkeys, the security log and secret storage | `tests/test_redteam_auth.py`, `tests/test_security_auth_*.py`, `tests/test_security_secrets_at_rest.py` |
| Cross-user matrix | A second ordinary account calls every route that takes an id, with the first account's ids | `tests/test_security_cross_user.py` |
| Route inventory | Every route is either behind the sign-in gate or on a reviewed public list | `tests/test_security_route_inventory.py` |
| Real HTTP | A real server process and raw sockets: framing attacks, header floods, forwarded-header trust, slow clients | `tests/test_realhttp.py` (needs `AMIDE_REALHTTP=1`) |
| Races | Parallel first registrations, replayed 2FA codes, lockout counting, share accept/decline, migrations starting together | `tests/test_security_races.py` |
| Hostile files | Zip and PDF bombs, truncated and mutated files, giant images, hostile CSV and JSON through every parser | `tests/test_hostile_files.py`, `tests/test_pdfsafe_answer.py` |
| Output encoding | Canary strings through every form, then every page, file and feed checked for unescaped output | `tests/test_output_encoding.py` |
| Watcher review | The Telegram watcher treats chat content as hostile: file names, sizes, token handling, backoff | `watcher/tests/test_security_watcher.py` |
| Upgrade rehearsal | Migrate a copy of an old database to the current revision; check row counts, integrity and foreign keys | `tests/test_pre_upgrade_snapshot.py`, `tests/test_migrations.py` |

## 5. Manual Checks

These cannot be automated from a build machine. Do them on the real deployment after every release that touches sign-in, files or headers.

1. Back up the data volume, then deploy. Confirm the app starts, `/healthz` answers, and the footer shows the new version.
2. Barcode scanner: open the Food tab, choose Scan A Barcode, and allow the camera.
3. Passkey: add one on a phone (it asks for your password), sign out, sign in with it.
4. Open a COA or lab-report PDF in Chrome; confirm the viewer shows it.
5. Upload a scanned price-list PDF; confirm the text is read.
6. Telegram watcher: send one PDF and one photo through it after redeploying it.
7. Proxy: sign in a few times, open Settings, Security Log: the addresses must be real client addresses, not the proxy's. Check the response headers on the public address (Content-Security-Policy, Strict-Transport-Security, and a Secure session cookie).
8. Rollback rehearsal: restore the pre-update copy of the data volume into a scratch stack and confirm the previous image starts on it (see Rolling Back in `docs/DEPLOYING.md`).

## 6. The Release Gate

A release is **ship** when all of these hold:

- the automated checks in part 1 pass, and the scheduled scan workflow has been run on the release commit with every job green;
- no open finding is rated high or critical, and every medium has a written decision (fixed, or accepted with a reason);
- the review prompts in part 3 have been run since the last large change, and nothing they raised is undecided;
- the manual checks in part 5 are done and recorded;
- rollback is documented and a pre-update copy of the data exists.

It is **ship with conditions** when only documentation, hygiene or rehearsal items remain; list the conditions. It is **do not ship** when a high or critical finding is open, a secret is exposed, or an update cannot be undone.

## 7. Last Recorded Run

| Item | Result |
|---|---|
| Date and version | 2026-11-21, release v1.3.21 plus follow-ups |
| Tests | 4,418 passed, 11 skipped (real-socket and POSIX-only tests) |
| Dependency audit | clean on both lock files |
| Image scan (Trivy) | clean after the zlib update and removing pip from the runtime images |
| Running-app scan (ZAP) | 0 failures, 60 passes, 8 warnings (all reviewed, accepted) |
| Secret scan (gitleaks, full history) | clean; one reviewed false positive allow-listed by fingerprint |
| Release gate | ship with conditions: rollback documentation, environment variable documentation, logging notes, ownership notes, manual checks |

Update this table at the end of every run.

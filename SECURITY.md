# Security Policy

## Reporting A Vulnerability

Please do not open a public issue for a security problem. Use GitHub's private advisories instead: open this repository's **Security** tab, choose **Report a vulnerability**, and describe what you found, how to reproduce it and what it could allow. Only the maintainer sees the report.

Include the Amide version (shown in the footer and in Settings) and whether you tested a default install or a particular setup (reverse proxy, `AMIDE_PUBLIC_URL`, registration open or closed).

You will get an acknowledgement as soon as the maintainer sees it. Amide is a hobby project run by one person, so there is no guaranteed response time, but reports of anything that exposes another user's data are handled first. A fix is released as a new version and credited in the advisory unless you prefer to stay anonymous.

## Supported Versions

Only the latest release receives security fixes. Update by pulling the newest image (see `docs/DEPLOYING.md`).

## What Is In Scope

The Amide application, the Telegram watcher in `watcher/`, and the container images and workflow in this repository. Problems in a third-party package belong to that package, but tell us if Amide uses it in a way that makes it exploitable.

## Verifying A Release

Published images carry a software bill of materials and a build provenance attestation, and are built from hash-pinned dependencies. How to check them is in `docs/DEPLOYING.md`, under "Verifying A Release".

# Running Amide for real: HTTPS, a reverse proxy, backups and updates

Amide is made to run on a computer you control: a home server, a mini PC, a small VPS. This page covers what to do beyond `docker compose up -d`.

Using Portainer? Paste [docker-compose.portainer.yml](../docker-compose.portainer.yml) into **Stacks, Add stack, Web editor**. It runs the published image with your data in a named volume, so there is nothing to build. Update with **Pull and redeploy** (Re-pull image on).

## 1. Decide who can reach it

| You want | Do this |
| --- | --- |
| Only you, on the computer running it | Run it as is and open `http://localhost:1707`. |
| Your phone and other devices at home | Run it on a computer that stays on and open `http://<that computer's address>:1707` on your home network. No HTTPS is needed on a trusted home network, but the sign-in cookie then travels unencrypted. |
| Away from home | **Do not open the port on your router.** Use a private network such as [Tailscale](https://tailscale.com) or WireGuard, or put Amide behind a reverse proxy with HTTPS (below). |

Amide holds health records, so treat it like a bank login: strong password, two-factor turned on (Settings), and HTTPS whenever it is reachable beyond your own home network.

## 2. HTTPS with a reverse proxy

A reverse proxy is a small program that sits in front of Amide, gets and renews the HTTPS certificate, and forwards requests to it. Amide marks its sign-in cookie `Secure` whenever it sees the request arrived over HTTPS, so the proxy must tell Amide the original scheme (all the examples below do).

### Caddy (simplest: certificates are automatic)

`Caddyfile`:

```
amide.example.com {
    reverse_proxy 127.0.0.1:1707
}
```

Point your domain's DNS at the server, open ports 80 and 443 to it, and run Caddy. That is the whole setup.

### nginx

```nginx
server {
    listen 443 ssl;
    server_name amide.example.com;
    # ssl_certificate / ssl_certificate_key: for example from certbot

    client_max_body_size 30m;            # price lists and photos; Amide's own limit is AMIDE_MAX_UPLOAD_MB

    location / {
        proxy_pass http://127.0.0.1:1707;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Telling Amide to trust the proxy

The container already starts with `--proxy-headers`, but uvicorn only believes those headers from `127.0.0.1` by default. If the proxy runs in another container or on another machine, give `FORWARDED_ALLOW_IPS` the address or network of that proxy and nothing wider, for example the proxy's Docker network:

```yaml
    environment:
      FORWARDED_ALLOW_IPS: "172.18.0.0/16"   # only the proxy; never "*"
```

Do not use `*`: it makes Amide believe `X-Forwarded-For` from anyone who can reach the port, which lets a visitor pretend to be any address and walk around the per-address sign-in throttle and the security log. Bind the published port to localhost (or to the proxy's network only), for example `127.0.0.1:1707:8000` in `docker-compose.yml`, so nobody can skip the proxy.

If `FORWARDED_ALLOW_IPS` is missing behind a proxy, Amide sees every visitor as the proxy's own address, so they all share one sign-in throttle. The symptom is that everyone is locked out after 30 sign-ins in 10 minutes. Amide logs a warning on the first POST it sees when `AMIDE_PUBLIC_URL` is https, the peer is a private or loopback address and `FORWARDED_ALLOW_IPS` is not set.

### Sign-In Settings

- `AMIDE_PUBLIC_URL`: the https address people use, for example `https://amide.example.test`. Set it on every public install. It fixes the passkey origin (without it Amide trusts the `Host` / `X-Forwarded-Host` header of each request and logs a warning at start) and it is the address the calendar feed link is built from. It does not touch the cookies: the session and device cookies are `Secure` whenever the visit came in over https (directly, or through your proxy, which says so in `X-Forwarded-Proto`), and an `http://` LAN visit keeps a normal cookie, so you can use the home address at home and the https address away from home. Passkeys only work on the https address.
- `AMIDE_REGISTRATION`: `open` or `closed` forces the New User page open or shut. Leave it unset to use the admin's switch, Allow New Users To Register, in Settings under Admin. The very first account can always register (it becomes the admin); after that sign-up is closed by default, including on an upgraded install. Admins can still add people in Settings.
- `AMIDE_SESSION_MAX_HOURS` (default 24): a session ends this long after sign-in even if a tab keeps it alive. Sessions that sit idle for 30 minutes end too.
- Sign-in, 2FA, passkey, New User and legal-notice posts are limited per client address and answer `429` with a `Retry-After` header when exceeded. The last 50 security events (sign-ins, failures, lockouts, password and passkey changes) are listed to the admin in Settings under Security Log and are also written to the `amide.security` logger.

## 5. Settings you may want

See the environment-variable table in the README. The ones that matter for a public-facing install: `AMIDE_PASSWORD_MIN_LENGTH` (default 8), `AMIDE_PORT`, `AMIDE_DATA_DIR`, `AMIDE_MAX_UPLOAD_MB` and `AMIDE_MAX_BACKUP_MB`.

## 6. Installing Amide on a phone, calendar subscriptions and reminders

- **Install to the home screen:** open Amide in the phone's browser over HTTPS (a plain `http://` address on your home network will not offer it, except on `localhost`) and choose Add to Home Screen.
- **Calendar subscription:** Settings, Subscribe to your calendar makes a private address. Your calendar app needs to reach it, so from outside your home network it must be your HTTPS address.
- **Reminders:** Settings, Dose reminders turns on ntfy push messages. Amide posts to `AMIDE_NTFY_SERVER` (default `https://ntfy.sh`), so the server needs internet access for this one feature. Leave it off and Amide makes no outside calls.

Updating an existing install: pull the new image (Portainer or Dockhand: "Pull and redeploy" with "Re-pull image" on). On start Amide runs its database updates and fills any empty library entries from the base library that ships with it, merging the old-style duplicate names; entries that already hold card data, and everything you entered, are left alone. Your data volume is not touched.

## 7. Price-list ingest and the Telegram watcher (optional, advanced)

The watcher has its own container image: use [docker-compose.portainer-with-watcher.yml](../docker-compose.portainer-with-watcher.yml), or add its service to your existing stack without re-pulling Amide. Steps, including the one-time Telegram login, are in [watcher/README.md](../watcher/README.md).

Amide can read price lists posted in chat groups through a separate program, `watcher/`, that runs on your own computer with your own Telegram account. It is optional and nothing in Amide depends on it. See [watcher/README.md](../watcher/README.md).

## Web Hardening, Binding And Reverse Proxy

- **Response headers.** Every response carries a Content-Security-Policy (scripts, styles, images and connections from Amide itself only; no framing), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, a Permissions-Policy (camera allowed for the barcode scanner, microphone, location and payment denied) and `Cross-Origin-Opener-Policy: same-origin`. `Strict-Transport-Security` is added when the request arrived over HTTPS or `AMIDE_PUBLIC_URL` starts with `https://`. If a page ever misbehaves after an update, set `AMIDE_CSP=report-only` and restart: the policy is then only reported in the browser console, not enforced. Remove it once the cause is found.
- **API pages are off.** `/docs`, `/redoc` and `/openapi.json` return 404. Set `AMIDE_API_DOCS=1` to turn them on for development.
- **Request size limits.** A body larger than its limit is refused with 413 before it is read: backups up to `AMIDE_MAX_BACKUP_MB` plus 1 MB, Apple Health uploads up to `AMIDE_HEALTH_MAX_MB` plus 1 MB, other uploads (photos, COAs, price lists, ingest) up to 64 MB, and ordinary forms and JSON 2 MB. `AMIDE_MAX_REQUEST_MB` changes the 64 MB figure.
- **Uploaded files** (COAs, lab reports, price lists, wallet QR codes, photos) are shown inline but served with a sandboxing policy, `nosniff` and `Cache-Control: private, no-store`.
- **Choosing the address Amide listens on.** The compose files publish on every network interface so phones on your home network can reach it. Set `AMIDE_BIND=127.0.0.1` (a `.env` file next to `docker-compose.yml`, or a stack environment variable in Portainer) to publish on this machine only, and put a reverse proxy on the same machine in front of it. The container also runs with `no-new-privileges` and all Linux capabilities dropped.
- **Reverse proxy.** Terminate HTTPS at the proxy and forward to `127.0.0.1:1707`. Pass `X-Forwarded-Proto` and `Host`, and set `AMIDE_PUBLIC_URL` to the public `https://` address. Do not expose port 1707 itself to the internet when a proxy is in front of it.

## Verifying A Release

Every image published for a version tag is built by the CI workflow from hash-pinned dependencies and a digest-pinned base image, and carries a software bill of materials (SBOM) and a build provenance attestation. To look at them, use Docker with buildx:

```bash
docker buildx imagetools inspect ghcr.io/<owner>/amide:<version> --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/<owner>/amide:<version> --format '{{ json .Provenance }}'
```

To run a release you have checked, pin it by digest in your compose file: `image: ghcr.io/<owner>/amide@sha256:<digest>` (the digest is printed by `docker buildx imagetools inspect`). A tag can move; a digest cannot. How the dependency lock files are made and refreshed is in [SUPPLY_CHAIN.md](SUPPLY_CHAIN.md).

## Secrets At Rest

Amide seals the values it has to read back: each person's two-factor (2FA) seed and their AI provider key are stored encrypted (AES-256-GCM, stored with an `enc1:` prefix). Sign-in sessions are stored only as a SHA-256 hash of the browser's cookie value, so a copy of the database holds no working cookie. Passwords, remembered-device tokens, ingest and Apple Health tokens were already stored as hashes. The calendar feed address is found by its SHA-256 hash, and a sealed copy lets Settings show it again, so a copy of the database holds no usable address (with `AMIDE_SECRET_KEY` kept outside `data/`, not even the sealed copy can be read). Treat the address like a password and regenerate it if it leaks.

- **Where the key comes from.** Set `AMIDE_SECRET_KEY` in the environment (any long random string) and Amide derives the key from it. Without it, Amide creates a random key on first use and keeps it in `data/secret.key` (readable by its owner only).
- **A copy of `data/` carries the key file.** To make a copy of the database alone useless, set `AMIDE_SECRET_KEY` in the environment (compose `.env` file or Portainer stack variable) and keep it out of `data/` and out of your backups of `data/`. Do this before the first start of a new install, or before updating an existing one: the update seals what is already stored with whichever key is in force at that moment. If you start without the variable and add it later, the existing seals become unreadable.
- **Losing the key.** Stored 2FA seeds and AI keys can no longer be read. A person with an unreadable 2FA seed is still asked for a code, so an administrator uses Remove 2FA for them in Settings (they then set it up again), and they type their AI key again. Nothing else is lost. Keep a copy of the key where you keep your other secrets.
- **Updating from an earlier version** seals the stored seeds and keys once, and signs everyone out once (their old cookies no longer match anything).
- **Backups.** A whole-installation backup is sealed by its own passphrase and carries 2FA seeds and AI keys as plain values inside it; a restore seals them again with the key of the installation it is restored into, so a backup restores correctly even under a different key. Profile exports never contain either.

- **AI agent on your own network.** The AI "My own server" address (Ollama, LM Studio) may point at a private-network address only for an administrator, unless `AMIDE_AI_LOCAL_ALLOWED_HOSTS` lists the allowed hosts or networks (comma separated). When that list is set, only those hosts work, for every account. Public hosts are open to everyone. Any failure to reach the agent is reported as the same message, so an account cannot probe your network. A request to the agent also stops after at most three minutes in total.

## Rolling Back

An update can change the database's shape. A newer Amide migrates the database when it starts, and an **older Amide cannot start on a database a newer one has migrated**: it stops with an error that it cannot find the newer revision. So going back means putting the old database back, not only the old image.

Before every update:

1. Copy the whole data volume (or the `data/` folder): the database `amide.db` and everything beside it, especially `secret.key` if you did not set `AMIDE_SECRET_KEY`. A copy made while Amide is stopped is the cleanest; a copy of a running install can miss the newest writes.
2. Note the image tag you are running now (for example `1.3.21`), so you can pull it again. Pin the tag in your stack instead of `latest` if you want updates to be a deliberate act.

Amide also keeps its own safety net: when an update would migrate an existing database, it first copies the database to `data/backups/pre-upgrade-<from>-to-<to>-<time>.db` (the newest three are kept). That file is a plain SQLite copy of the database as it was just before the update; protect it like the database itself.

To go back:

1. Stop the Amide container.
2. Put the old database back: either restore your copy of the data volume, or replace `data/amide.db` with the matching `pre-upgrade-...db` file (delete any `amide.db-wal` and `amide.db-shm` beside it first). Uploaded files are not changed by an update, so they do not need restoring.
3. Start the previous image tag (`ghcr.io/xsatc77/amide:<old tag>`), then check `/healthz` and sign in.

Anything entered after the update is lost by going back. If you only need the data and not the old version, use Settings, Backup And Restore instead: it loads a backup into the current version. Keep the same `secret.key` (or the same `AMIDE_SECRET_KEY`) throughout; a different key makes stored 2FA seeds and AI keys unreadable (see Secrets At Rest).

## Logs And Monitoring

- **Where the logs are.** Amide writes to the container's standard output: `docker logs <container>` or the log view of Portainer or Dockhand. Nothing is written to log files inside `data/` except the Telegram watcher's own log in its data folder.
- **What to watch.**
  - `amide.security`: unreadable secrets, startup warnings (public address not set, proxy trust), and one line per security event.
  - `amide.errors`: one line (exception class and path only) for every unhandled error, which also returns a plain 500 to the browser.
  - `app.migrate`: the pre-update database copy and its name.
  - Background jobs (reminders, the ingest worker, scheduled backups) log their failures and keep running.
- **Security Log.** Administrators see the last 50 security events in Settings (sign-ins, failures, lockouts, password and 2FA changes, tokens, shares, backups, vendor edits). It keeps 90 days and at most 50,000 lines.
- **Health.** `GET /healthz` answers `{"ok": true}` without signing in; the image uses it for its container health check, and an uptime monitor can poll it. It confirms the web process is up, not that the data is healthy: also watch free disk space and that the newest file in `data/backups` is recent.
- **Alerts worth setting.** The container restarting repeatedly, `/healthz` failing, a burst of failed sign-ins in the Security Log, and disk space under 20 percent.

## Other Environment Variables

These are read by Amide but only needed in special cases.

- `AMIDE_BASE_LIBRARY`: `0` stops Amide filling the shipped peptide cards at start (default `1`).
- `AMIDE_INGEST_WORKER`: `0` stops the background loop that processes received price lists (default `1`).
- `AMIDE_VERSION`: set by the image build to its release tag; shown in the footer and in Settings. Leave it alone.
- `AMIDE_WATCHER_HOME` (watcher container only): the folder holding the watcher's session, state and queue (default `/watcher-data` in the image).

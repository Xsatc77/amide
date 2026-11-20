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

### Sign-In Settings

- `AMIDE_PUBLIC_URL`: the https address people use, for example `https://amide.example.test`. Set it on every public install. It fixes the passkey origin (without it Amide trusts the `Host` / `X-Forwarded-Host` header of each request and logs a warning at start) and it makes the session and device cookies `Secure` even when the proxy hides that the visitor used https.
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

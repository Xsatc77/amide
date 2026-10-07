# Running Amide for real: HTTPS, a reverse proxy, backups and updates

Amide is made to run on a computer you control: a home server, a mini PC, a small VPS. This page covers what to do beyond `docker compose up -d`.

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

The container already starts with `--proxy-headers`, but uvicorn only believes those headers from `127.0.0.1` by default. If the proxy runs in another container or on another machine, tell Amide its address, or `*` when the proxy is the only thing that can reach the port:

```yaml
    environment:
      FORWARDED_ALLOW_IPS: "*"
```

Then bind the published port to the proxy only, for example `127.0.0.1:1707:8000` in `docker-compose.yml`, so nobody can skip the proxy.

## 3. Back up

Everything lives in the `data/` folder (the database and every uploaded file). Two layers:

1. **Inside Amide:** Settings, Backup and restore makes an encrypted `.amidebackup` file. The administrator can back up the whole installation. Keep the passphrase somewhere safe; there is no recovery.
2. **Outside Amide:** copy the `data/` folder on a schedule (your backup software, a cron job, a NAS snapshot). Stop the container first if you want a perfectly clean copy, or use SQLite's own backup command while it runs.

3. **Automatic:** set `AMIDE_BACKUP_PASSPHRASE` (8 or more characters; it lives in the environment, not the database, so a copy of the database cannot open the files) and Amide writes an encrypted whole-installation backup to `data/backups` every `AMIDE_BACKUP_DAYS` days (default 7), keeping the newest `AMIDE_BACKUP_KEEP` (default 4). Settings shows whether it is on. Keep the passphrase somewhere safe: there is no recovery. Copy `data/backups` to another disk or a cloud drive for protection against a lost disk.

Try a restore once on a spare install before you need it.

## 4. Update

```bash
git pull
docker compose up -d --build
```

Database migrations run automatically at startup. Back up first.

## 5. Settings you may want

See the environment-variable table in the README. The ones that matter for a public-facing install: `AMIDE_PASSWORD_MIN_LENGTH` (default 8), `AMIDE_PORT`, `AMIDE_DATA_DIR`, `AMIDE_MAX_UPLOAD_MB` and `AMIDE_MAX_BACKUP_MB`.

## 6. Installing Amide on a phone, calendar subscriptions and reminders

- **Install to the home screen:** open Amide in the phone's browser over HTTPS (a plain `http://` address on your home network will not offer it, except on `localhost`) and choose Add to Home Screen.
- **Calendar subscription:** Settings, Subscribe to your calendar makes a private address. Your calendar app needs to reach it, so from outside your home network it must be your HTTPS address.
- **Reminders:** Settings, Dose reminders turns on ntfy push messages. Amide posts to `AMIDE_NTFY_SERVER` (default `https://ntfy.sh`), so the server needs internet access for this one feature. Leave it off and Amide makes no outside calls.

## 7. Price-list ingest and the Telegram watcher (optional, advanced)

Amide can read price lists posted in chat groups through a separate program, `watcher/`, that runs on your own computer with your own Telegram account. It is optional and nothing in Amide depends on it. See [watcher/README.md](../watcher/README.md).

# The Telegram watcher as an optional container: design

Date: 2026-10-09. Status: awaiting the owner's review.

## Problem

The Telegram price-list watcher is a separate Python program, and the published Docker image contains only the Amide app. Someone who installs with Docker, Portainer or Dockhand has nothing to run the watcher with unless they download the source, install Python and use a second computer's startup entry. That is too much for a feature the project advertises.

## Goal

An optional second container, `ghcr.io/xsatc77/amide-watcher`, that runs beside Amide in the same stack with only environment variables and one interactive Telegram login. Adding it must not touch an existing Amide install, its data volume or its image.

## Design

### Image
`Dockerfile.watcher`: `python:3.13-slim`, installs `requirements-watcher.txt` (telethon, httpx), copies only the `watcher/` package (it imports nothing from the app), runs as a non-root user, home folder `/watcher-data` (a volume, set through `AMIDE_WATCHER_HOME`), command `python -m watcher serve`. No ports are opened.

### Settings from the environment
`watcher/config.py` reads these variables and they win over `config.toml`, so a container needs no file: `AMIDE_URL` (default stays `http://127.0.0.1:8000`; the stack sets `http://amide:8000`), `AMIDE_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and optionally `WATCHER_BACKFILL_DAYS`, `WATCHER_POLL_SECONDS`, `WATCHER_MAX_FILE_MB`. Existing `config.toml` users are unaffected. Error messages name the key and never its value, as today.

### A new `serve` command
Like `run`, but if Telegram is not signed in it does not exit (a restart loop would spam the log and could trigger Telegram flood limits): it logs once, "Not signed in to Telegram. Open a console in this container and run: python -m watcher login", then checks again every 30 seconds with a fresh connection (a login made in another process is only visible to a new connection) and starts watching by itself once the login succeeds.

### The login
Done once, by hand, from the stack manager's console for the watcher container (`python -m watcher login`: phone number, the code Telegram sends, the two-step password if set). The session is written to the `watcher-data` volume, so image updates and restarts keep it. This step cannot be automated: Telegram sends the code to the person's phone.

### The stack
`docker-compose.portainer-with-watcher.yml`: the same `amide` service as `docker-compose.portainer.yml`, unchanged, plus a `watcher` service (depends on `amide`, environment as above, `watcher-data` volume, `restart: unless-stopped`). The Amide-only file stays as the default. An existing install adds the `watcher` service and the `watcher-data` volume to its current stack; Compose creates only what is new, so the running Amide container and its data volume are not touched (as long as "re-pull image" is left off).

### Unchanged guarantees
The watcher still only reads: it never posts, reacts, joins, leaves or marks anything read, and it reads only groups mapped and switched on in Amide. Logs hold only group titles, message ids, counts and error class names. The token, API id and hash never appear in logs or `status` output.

### Release and CI
The CI `docker` job also builds the watcher image on every push (so a broken Dockerfile is caught), runs `python -m watcher status` inside it with dummy settings to prove the image starts and reads its environment (that command never contacts Telegram), and publishes `ghcr.io/<owner>/amide-watcher` with the same tags only for a `v*` tag. The first release that carries it is v1.0.1. The package must be made Public once on GitHub, as for the Amide image.

## Out of scope
A web page for the Telegram login, running more than one Telegram account, any change to what the watcher reads or sends, and the Windows startup entry (it stays for people who run the watcher on a PC).

## Testing
Environment settings (precedence, missing keys, bad numbers, no config.toml at all), the `serve` wait-then-start behaviour with a fake client, a CI smoke run of the built image, and a check that the two stack files parse and that the Amide service in both is identical.

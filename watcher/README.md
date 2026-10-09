# Price list watcher

A small program that runs on your own computer, watches the Telegram groups you map in Amide, and hands every new price-list
message (PDF, photos, spreadsheet or typed prices) to Amide's Price list inbox. Amide works out the vendor, warehouse and date.

## Run it as a container (Docker, Portainer, Dockhand)

The watcher has its own image, `ghcr.io/xsatc77/amide-watcher`, and runs next to Amide in the same stack. Nothing is installed on your PC.

1. In Amide: Settings, Admin, **Price list inbox**, create a token and copy it (shown once). Register your own Telegram application at <https://my.telegram.org> (API development tools) for an `api_id` and `api_hash`.
2. Use [docker-compose.portainer-with-watcher.yml](../docker-compose.portainer-with-watcher.yml). **Adding it to a stack you already run:** open the stack, paste in the `watcher` service and add `watcher-data:` under `volumes:`, fill in `AMIDE_TOKEN`, `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`, and redeploy **without** "re-pull image", so your running Amide container is left alone.
3. Open the console of the `amide-watcher` container and run `python -m watcher login` once: your phone number, the code Telegram sends you, and your two-step password if you set one. Telegram sends the code to your phone, so this one step cannot be automated.
4. That is all. The watcher waits for the login (it logs "Not signed in to Telegram yet" and checks every 30 seconds), then starts by itself. The login is kept in the `watcher-data` volume, so updates and restarts keep it.
5. In Amide's inbox, map each group to a vendor and switch it on. If you also ran the watcher on a PC, stop that one (`python -m watcher remove-startup`) so two copies do not deliver the same lists.

To remove it, delete the `watcher` service from the stack. Settings can also be given as `AMIDE_URL`, `AMIDE_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `WATCHER_BACKFILL_DAYS`, `WATCHER_POLL_SECONDS` and `WATCHER_MAX_FILE_MB` environment variables instead of a `config.toml` (they win over the file).

**If you run a reverse proxy in front of Amide:** the container talks to Amide directly at `http://amide:8000`, so the proxy is not involved.

## Run it on a computer you sign in to

(Windows, or any computer with Python. If you installed Amide with Docker, the container above is simpler.) Download the code (**Code, Download ZIP** on GitHub, or `git clone https://github.com/Xsatc77/amide`), install Python 3.12 or newer, and in that folder run `pip install -r requirements-watcher.txt`. Set `amide_url` in `config.toml` to your server's address, for example `http://<your server>:1707`. If a reverse proxy sits in front of Amide, make sure it allows uploads of about 30 MB, or deliveries can fail with a "413 too large" error.

It only **reads**. It never posts, reacts, joins, leaves or marks anything read, and it reads only groups you have mapped to a
vendor and switched on in Amide. For every other group it sends Amide the title, so you can map it.

## One-time setup

1. Register your own Telegram application at <https://my.telegram.org> (API development tools) to get an `api_id` and `api_hash`.
2. In Amide: Settings, Admin, **Price list inbox**, create a token and copy it (it is shown once).
3. Make the folder `%APPDATA%\amide-watcher` and copy `watcher\config.example.toml` into it as `config.toml`. Fill in the token,
   `api_id` and `api_hash`. **This file and the login session stay on your computer; never commit them.**
4. `pip install -r requirements-watcher.txt`
5. `python -m watcher login` (phone number, the code Telegram sends you, and your two-step password if you set one).
6. `python -m watcher status` (checks the token and shows what the watcher knows).
7. In Amide's inbox, map a group to a vendor and switch it on, then `python -m watcher once --days 2` to try one group.
8. `python -m watcher install-startup` so it runs when you sign in to Windows (`remove-startup` undoes it).

## Commands

| Command | What it does |
|---|---|
| `login` | One-time Telegram sign-in. |
| `run` | The background watcher (polls every `poll_seconds`, default 300). |
| `once [--days N]` | One catch-up pass, then exit. |
| `status` | Shows the home folder, whether a session exists, whether Amide accepts the token, the queue and each group's position. Never shows a secret and never contacts Telegram. |
| `install-startup`, `remove-startup` | Add or remove the Windows Task Scheduler entry. |

## Topics (forum groups)

Some groups are split into topics (named threads such as "US warehouse" or "Chatter"). The watcher registers each forum group's
topics with Amide. In the inbox's Groups table, tick **Only selected topics** for the group and choose the topics to follow.
A new topic whose name contains one of the group's **auto-follow words** (default `price, prices, pricing, pricelist, warehouse`) starts ticked; others
start off. **Skip words** (for example `UK, EU`) set aside any list whose caption, filename, topic name or text mentions them.
A topic's name also helps decide the warehouse. Groups without topics work as before.

## How it behaves

- First time it sees a mapped group it reads back `backfill_days` (default 7); after that it continues from the last message.
- Photos sent together as an album are sent as one list. Files over `max_file_mb` and file types other than PDF, JPEG/PNG and
  xlsx are skipped.
- Messages are written to a local queue first, then delivered in order; if Amide is down they wait and are retried with growing
  pauses (30 seconds up to 15 minutes). The queue holds at most 500 items or 2 GB, after which it stops reading until it drains.
- A group is reported "no longer active" only after two polls in a row find it unreadable. A quiet group, a network error or a
  flood wait never counts. If it becomes readable again the watcher reports it active.
- A refused token pauses delivery with a clear log line instead of looping.

## Files (all in the home folder, never in the repository)

`config.toml`, `telegram.session`, `state.json`, `queue\`, `watcher.log`. Logs contain only group titles, message ids, counts
and error class names, never message text or secrets.

## Known limits (version 1)

Edits and deletions of old messages are ignored; it polls rather than listening live; a group Telegram can no longer resolve at
all is reported as unknown rather than gone; Windows startup runs at sign-in, not as a service before sign-in.

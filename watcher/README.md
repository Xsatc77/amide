# Price list watcher

A small program that runs on your own computer, watches the Telegram groups you map in Amide, and hands every new price-list
message (PDF, photos, spreadsheet or typed prices) to Amide's Price list inbox. Amide works out the vendor, warehouse and date.

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
A new topic whose name contains one of the group's **auto-follow words** (default `price, warehouse`) starts ticked; others
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

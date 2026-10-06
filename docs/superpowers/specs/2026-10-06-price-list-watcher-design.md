# Price List Watcher (Part B: the program on the owner's computer) — Design

Status: written for owner review 2026-10-06. Part A (the engine inside Amide, spec `2026-10-06-price-list-ingest-design.md`) is built and pushed.

## Problem

Part A can take in a price list from anything that holds a token, but nothing sends it lists yet. The owner follows several
vendor chat groups on Telegram, where lists are posted as PDFs, photos, spreadsheets and typed messages. A small program on
the owner's computer should watch those groups with the owner's own account and hand every new list to Amide, so nothing is
downloaded or renamed by hand.

## Decisions already made (with the owner)

| Question | Decision |
|---|---|
| Whose account | The owner's own Telegram account, read only. The login session and API credentials never enter the repository. |
| Hand-off | Part A's token API (`Authorization: Bearer <token>`). The token is made by the administrator in the Price list inbox. |
| Which groups | Chosen in Amide (the inbox maps each group to a vendor and switches it on), not in the watcher. |
| Group disappears | The watcher reports it; Amide shows "<VENDOR> Telegram group is no longer active." |
| Where it runs | On the owner's computer, in the background, starting with Windows. |

## What it is

A separate Python program in the repository folder `watcher/`, with its own `requirements-watcher.txt` and its own tests. It is
**not** part of the Amide web app, is not installed in the Docker image, and Amide does not import it. It uses
**Telethon** (a Telegram client library for user accounts) and **httpx** to call Amide. It runs as one process with an
asynchronous loop.

Commands (`python -m watcher <command>`):

- `login`: one-time interactive sign-in (phone number, the code Telegram sends, the two-step password if set). Saves the
  session. Nothing is printed that could be pasted by mistake: the session file is never shown.
- `run`: the background watcher.
- `once [--days N]`: one catch-up pass over the enabled groups (default 7 days back), then exit. Used for testing and after a
  long outage.
- `status`: shows what it knows without contacting Telegram: configuration found, Amide reachable and the token accepted,
  groups being watched, last message seen per group, how many items wait in the local retry queue.
- `install-startup` / `remove-startup`: create or remove a Windows Task Scheduler entry that runs `run` at logon, minimized,
  restarting after failure. (Windows only; other platforms run it however they like.)

## Local files (never in the repository)

All live in one folder outside the repo, by default `%APPDATA%\amide-watcher` (override with `AMIDE_WATCHER_HOME`):

- `config.toml`: `amide_url` (default `http://127.0.0.1:8000`), `amide_token`, `telegram_api_id`, `telegram_api_hash`,
  `backfill_days` (default 7), `poll_seconds` (default 300, the gone-check and catch-up interval), `max_file_mb` (default 25).
- `telegram.session`: the Telethon session.
- `state.json`: per group, the highest message id handed over and the last time the group was seen alive.
- `queue/`: items that could not be delivered yet (see Delivery).

The folder is created with owner-only permissions where the platform allows it. The repository ships only
`watcher/config.example.toml` (placeholders), and `.gitignore` excludes any `config.toml`, `*.session`, `state.json` and
`queue/` as a second guard. The Telegram `api_id` and `api_hash` come from the owner's own registration at my.telegram.org.

## What it does

1. **Start:** read config; connect to Telegram with the saved session (if the session is missing or expired it logs an
   error saying to run `login` and exits non-zero); check Amide with `GET /api/ingest/sources` (401 means the token is
   wrong or revoked: log it, retry slowly, never crash-loop).
2. **Register groups:** for every group, supergroup or channel the account is in (never private chats, never bots), send
   `PUT /api/ingest/sources/{chat_id}` with its title, so the owner can map and enable it in the inbox. Titles only; no
   messages from unmapped groups are read or sent. Re-registered every poll, which also keeps titles current.
3. **Watch:** the groups returned by `GET /api/ingest/sources` (enabled and mapped) are the watch list, refreshed every poll
   interval, so enabling or disabling a group in Amide takes effect within minutes without touching the watcher.
4. **Backfill:** the first time a group is watched (no entry in `state.json`), read back `backfill_days`; afterwards read from
   the last id seen. This also covers the computer being off for a while. Messages are processed oldest first.
5. **New message:** for each message from a watched group, in the order they arrive:
   - Collect the text (message text or caption) and any attached document or photo. Only these are sent: PDF, image
     (JPEG/PNG), spreadsheet (`.xlsx`), as the files of the message; everything else (video, voice, stickers, other
     documents) is skipped. Files larger than `max_file_mb` are skipped with a log line.
   - **Albums** (several photos sent together, which Telegram links with a grouped id) are sent as one message with
     `album_id` set, so Amide reads them as one list. Because the album's messages arrive one by one, the watcher waits 5
     seconds after the last one before sending the album together.
   - Call `POST /api/ingest/messages` with `chat_id`, `message_id`, `album_id` (when an album), `date` (the message time in
     UTC, ISO 8601), `text` and the files. Amide decides everything else; the watcher never interprets prices, vendors or
     warehouses.
   - A message with neither text nor a wanted file is skipped silently. Edits and deletions of old messages are ignored in
     version 1.
6. **Poll (every `poll_seconds`):** catch up on anything missed (the live update stream can drop), refresh the watch list,
   re-register titles, and run the gone check.
7. **Gone check:** for each registered group, ask Telegram whether the account can still read it. A group counts as gone
   when Telegram says it is private or inaccessible to this account, the group was deleted, or the account was removed or
   banned. A quiet group is **not** gone. To avoid reporting a hiccup, the watcher reports `gone` only after two consecutive
   polls agree, then sends `POST /api/ingest/sources/{chat_id}/state {state: "gone", reason}` (reason in plain words such
   as "removed from the group"). If a group reported gone is readable again, it sends `{state: "active"}`. Network errors
   and flood waits are never treated as gone.

## Delivery and reliability

- **Idempotent by design:** Amide answers `duplicate` for a message and file it has seen, so the watcher may safely resend.
  It still remembers the highest id per group so it does not re-download everything each time.
- **Retry queue:** if Amide is unreachable or answers 5xx or 429, the prepared message (files included) is written to
  `queue/` and retried with increasing waits (30 seconds up to 15 minutes), oldest first, never skipping ahead of an older
  item in the same group. A 4xx other than 429 (for example the group was disabled: 409, or a limit exceeded: 422) is
  logged and the item dropped, not retried forever. A 401 pauses all delivery and logs a clear message.
- **Queue cap:** at most 500 items or 2 GB; beyond that the watcher stops reading new messages (it does not drop queued ones), logs it, and resumes from its saved position when there is room, so nothing is lost silently.
- **Telegram limits:** honors `FloodWait` by sleeping the stated time; downloads one file at a time; never sends anything
  to Telegram (no messages, reactions, joins or leaves) and never marks messages read.
- **Crash safety:** `state.json` is written atomically after a message is delivered or queued, never before.
- **Logging:** a rotating log file in the home folder; no message text, file contents, phone number, tokens or session data
  are ever logged, only group titles, message ids, counts and error kinds.

## Safety and privacy

- Read only: the watcher uses the account only to read the chosen groups. It cannot post, react or join.
- Only enabled, mapped groups are read; everything else is limited to the group's title.
- Secrets (token, API hash, session) exist only in the home folder; they are never printed, logged or committed. `status`
  shows only that they are present.
- Telegram's terms allow personal automation of one's own account at a low rate; this program reads a few groups, with
  waits between requests, and honors flood waits.
- No vendor, group name or price-list data is ever written into the repository, tests or commit messages (tests use
  invented names and a fake Telegram client).

## Structure

`watcher/` package: `config.py` (load and validate, paths), `state.py` (atomic state file), `telegram_client.py` (a thin
interface, `TelethonClient` is the real implementation), `amide_client.py` (the API calls, httpx), `queue.py` (disk queue),
`runner.py` (register, watch, backfill, albums, poll, gone check), `cli.py` (commands), `startup.py` (Task Scheduler).
Everything above `telegram_client.py` is tested with a fake client and a fake Amide (httpx mock transport); the Telethon
class is thin and exercised only by a manual check against the real account.

## Testing

- Config: missing and invalid values, example config has no real values, paths overridable, secrets never printed by `status`.
- State and queue: atomic writes, resume after a crash, ordering, retry waits, 4xx drop and 401 pause.
- Runner with a fake Telegram: registers titles only; watches only the groups Amide lists; backfills `backfill_days` once,
  then from the last id; sends text-only, file, album (one request, one `album_id`, waits for the last part) and skips
  unwanted types and oversized files; preserves order; resends are harmless; a disabled group stops being read.
- Gone check: a quiet group is not gone; two consecutive failures report gone once; recovery reports active; a network error
  is not gone.
- Amide client against the real API in a test (the Amide test app through a transport): registration, list, message, state
  report, the duplicate answer, 401 and 429 handling.
- A repository guard test that no `config.toml`, session or state file is tracked and `.gitignore` covers them.
- Manual check, once, by the owner: `login`, `status`, `once --days 2` against one real group, then `install-startup`.

## Out of scope for version 1

Edited or deleted messages; replies and threads (a list in a reply is still just a message); other chat platforms; a tray
icon or window (logs and `status` only); watching private chats; reading voice, video or stickers; automatic group joining;
running as a true Windows service before logon.

## Review Focus

1. A secret leaking: the token, API hash or session in a log line, an error message, the example config, a test fixture or
   a commit.
2. A group read that should not be: unmapped or disabled groups, private chats, and the title-only registration rule.
3. A hiccup reported as "group gone", and a real removal never reported (two-poll rule, flood waits, network errors).
4. A list lost: a crash between download and delivery, Amide down for hours, the queue growing without bound, a message
   delivered twice, an album split in two because its last photo was slow.
5. Hammering Telegram or Amide: flood waits, retry loops, a 401 crash loop, a huge backfill.

# Price List Ingest: Follow Topics, Skip Words (Part C) — Design

Status: written for owner review 2026-10-06. Builds on part A (`2026-10-06-price-list-ingest-design.md`) and part B (`2026-10-06-price-list-watcher-design.md`), both built.

## Problem

Several vendor groups are Telegram **forum groups**: one group (the "top group") split into named topics, for example one topic
for price lists and others for chat or other regions. The watcher reads every topic of a mapped group, so Amide receives (and
the owner sees) a flood of chatter and lists the owner cannot use (for example a UK list). The owner wants to follow only the
price-list topic of a group, and to have lists for regions they cannot order from set aside automatically.

## Decisions (with the owner)

- Narrow by **topic** within a group: the owner picks which topics of a group to follow, in the Price list inbox.
- A topic's **name is a context clue** like the caption: a topic called "US Price List" tells the warehouse.
- **Skip words** per group: a list whose caption, filename, topic name or text mentions a skipped word (for example "UK") is
  set aside as ignored, never imported. The owner says they cannot order from the UK.

## Scope

In: topic registration and selection, reading only selected topics, the topic name as warehouse context, skip words, display
of the topic on inbox items. Out: reading topics of groups that are not forums (they behave as today), closing or reordering
topics, topic icons, notifications.

## Behavior

### Watcher
1. For a forum group, the watcher fetches its topic list (id and title) when it registers the group, refreshed daily, and
   sends it with the registration. Non-forum groups send no topics.
2. `GET /api/ingest/sources` now tells it, per group, whether to follow the whole group or only some topics.
3. When a group follows selected topics, the watcher reads only those topics, each with its own position in its state file
   (the key is the chat id plus the topic id). A topic that is not selected is never fetched.
4. Each message sent to Amide carries its `topic_id` and the topic's current `topic_title` (when the group is a forum).
5. A topic Amide has not seen before is registered **off**; nothing in it is read until the owner ticks it.
6. A closed, deleted or renamed topic needs no special handling: no new messages arrive, or the new title is registered.

### Amide
- New table `ingest_topics` (`source_id`, `topic_id` text, `title`, `enabled` default false, `created_at`; unique on
  `(source_id, topic_id)`; cascade on source). New columns: `ingest_sources.topics_only` (default false) and
  `ingest_sources.skip_words` (text, may be empty); `ingest_items.topic_id` and `ingest_items.topic_title` (text, nullable).
  One migration (0041).
- API changes (all still token-protected, same limits):
  - `PUT /api/ingest/sources/{chat_id}` accepts optional `topics: [{id, title}]` (at most 200); known topics get their title
    refreshed, new ones are added off. Topics the watcher no longer lists are kept (history), not deleted.
  - `GET /api/ingest/sources` returns `[{chat_id, title, topics: null | ["<id>", ...]}]`; `topics` is null when the whole
    group is followed, otherwise the ids of the enabled topics (possibly an empty list, meaning nothing yet).
  - `POST /api/ingest/messages` accepts optional `topic_id` and `topic_title`. If the group follows selected topics and the
    topic is not enabled, the message is answered `ignored` and not stored (the server does not rely on the watcher).
- Inference: the topic title is added to the context texts for the warehouse words and the date, alongside the caption and
  filename. Skip words are checked first, before any file is read (cheap, and nothing is imported or kept).
- **Skip words** match whole words, case-insensitive, in the caption, filename, topic title and the first 2,000 characters of
  the list's own text. A match sets the item `ignored` with the reason "skipped: UK" (the matched word), keeps no text and
  deletes the file. A skipped item can be found under the ignored tab.
- The inbox: the Groups table gains a **Follow** control (whole group, or selected topics) and, when topics are known, a
  checklist of them; plus a **Skip words** field (comma separated). Items show their topic name.

## Amendment: auto-follow words and group search (2026-10-06)

- Each group has **auto-follow words** (default `price, warehouse`, editable). A topic seen for the first time is ticked
  when its name contains one of them as a whole word and none of the group's skip words; otherwise it starts off. A later
  rename or a manual change of a tick is never overridden. Existing groups get the default. (A topic named "US warehouse"
  or "Price List" is followed without any clicking; "Chatter" or "Promotional Event" is not.)
- The Groups table has a search box (`?q=`) that shows only groups whose title contains the text.
- Not built: a file-name filter (photos usually have no useful name), and automatic mapping of groups to vendors.

## Backward compatibility and safety

- Groups with no topics, or `topics_only` off, behave exactly as today.
- The watcher tolerates an older Amide answer (a plain chat id list) by treating it as whole groups.
- Only the administrator edits topic selection and skip words (same 404 rule as the rest of the inbox).
- The server re-checks the topic on every message, so a stale or hostile watcher cannot import from an unselected topic.
- Skip words are plain text, never a pattern, so a bad entry cannot hang the matcher; each is at most 40 characters, at most
  20 words.
- No vendor, group or topic names in any tracked file; tests use invented names.

## Testing

- Migration and models: defaults, uniqueness, cascade.
- API: topics registered off; titles refreshed; the list shows null for whole-group and the enabled ids otherwise; a message
  from an unselected topic is ignored and not stored; stored items carry topic id and title; limits (200 topics).
- Inference: a topic title "US Price List" sets the warehouse; skip words match whole words only ("UK" matches "UK stock", not
  "Duke"), case-insensitively, and each source of text (caption, filename, topic, list text).
- Inbox: Follow control and topic ticks persist; skip words saved and trimmed; non-administrators get 404; escaping.
- Watcher (fake Telegram with a forum group): topics registered with the group; only enabled topics are read; per-topic
  positions are independent; a newly seen topic is not read; messages carry topic id and title; an old-style Amide answer
  still works; a non-forum group is unchanged.
- Real Telegram class: topic listing and per-topic reading are exercised by reading the code against Telethon and by the
  owner's manual check on one forum group.

## Review Focus

1. A message from a topic that should not be read, or a list from a skipped region imported anyway.
2. A topic that Telegram reports differently (the General topic, a closed topic, a group that is not a forum).
3. Positions: two topics of one group sharing a position, losing or repeating messages.
4. Skip words that match too much (a short word inside a longer one) or too little (other case, punctuation).
5. An old watcher or old Amide on either side of the changed API.

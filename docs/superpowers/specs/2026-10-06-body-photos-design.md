# Body Photos with Optional 2FA Unlock — Design

Status: approved in conversation 2026-10-06 (design), written spec pending owner review.

## Problem

Someone tracking a body recomposition wants progress photos next to their measurements and journal. These photos can be
sensitive (nudity), so they need to be private by default: never visible to anyone else, hidden from a glance over the
shoulder, and optionally locked behind an authenticator code.

## Goals

- A **Body photos** section on Weight & Measurements (`/measurements`) with upload, a gallery, reveal, and delete.
  An **Add body photo** link also sits in the Journal section and opens the same upload form.
- Every photo is **blurred until the owner asks to see it**. The blur is done on the server, so the sharp image never
  reaches the browser while blurred.
- A per-account setting **Body Recomp Photo 2FA**. When on, photos stay blurred until the owner enters a valid code from
  the account's authenticator; a correct code clears the blur for **10 minutes**.
- Photos are **never shared**: only the owner can fetch one; they are never in a Share file or any other person's view.
- Photos travel in the owner's own encrypted backup and Export (so a new computer keeps them).

## Decisions made with the owner

| Question | Decision |
|---|---|
| Which authenticator unlocks photos | Reuse the account's existing 2FA. The setting needs account 2FA on. |
| What a photo attaches to | A date, with an optional Front / Side / Back / Other label and an optional short note. |
| Backups and exports | Included in the owner's own backup and Export; never in Share. |
| File handling | Strip metadata (GPS, camera), convert iPhone HEIC to JPEG, apply orientation, shrink above ~2000 px. |

## Out of scope

Editing a photo's date, label or note after upload (delete and re-add); side-by-side comparison; zoom or slideshow;
a separate photo-only authenticator; per-photo sharing; thumbnails stored on disk.

## Data model

- **`body_photos`** (migration 0037): `id`, `owner_id` (FK users, ON DELETE CASCADE, indexed), `taken_on` (date),
  `angle` (text, one of `front|side|back|other` or NULL), `note` (text, 200 chars max, nullable), `filename` (text, the
  random stored name), `created_at`. Index on `(owner_id, taken_on)`.
- **`users.photo_2fa_required`** (boolean, default false).
- **`sessions.photo_unlocked_until`** (datetime, nullable, naive UTC): when this browser session's photo unlock ends.
- Files: `config.BODY_PHOTO_DIR = UPLOAD_DIR / "body_photos"`. One JPEG per photo, random hex name. No second file: the
  blurred version is generated when requested.

## Upload and processing (`app/body_photos.py`)

1. Accept JPG, PNG, WEBP and HEIC/HEIF, at most `config.MAX_UPLOAD_BYTES`. The file's content must decode as an image
   (checked by decoding, not by extension); anything else is refused with a plain message. A pixel cap (50 megapixels)
   refuses decompression bombs.
2. Apply EXIF orientation, convert to RGB (alpha flattened onto white), downscale so the longest side is at most 2000 px,
   save as JPEG quality 88 **with no metadata**.
3. The stored name is a random token plus `.jpg`; the original filename is never kept or shown.
4. HEIC is read through `pillow-heif` (registered once at import); `Pillow` is pinned explicitly alongside it.
5. **Blurred preview** (`blurred_jpeg(path)`): shrink to at most 480 px, apply a strong Gaussian blur (radius large enough
   that faces and bodies are not recognizable and the blur cannot be reversed), return JPEG bytes. Produced per request.

## Routes (all require sign-in, all owner-only)

- `GET  /measurements/photos/{id}/preview` → blurred JPEG. 404 unless the photo belongs to the signed-in user.
- `GET  /measurements/photos/{id}/full` → the sharp JPEG. Allowed only when the photo is the signed-in user's **and**
  (the 2FA setting is off **or** this session is unlocked). Otherwise 403 with `{"locked": true}` when the setting is on,
  404 when the photo is not theirs. All photo responses send `Cache-Control: private, no-store`.
- `POST /measurements/photos` → upload (date defaults to today; invalid date, bad file or note over 200 chars gives 422).
- `POST /measurements/photos/{id}/delete` → delete the row and its file.
- `POST /measurements/photos/unlock` → body `{code}`. Valid code sets `photo_unlocked_until = now + 10 minutes`.
- `POST /measurements/photos/lock` → clears the unlock at once.
- Other users, people the owner shares data with, and the administrator get 404 for any photo that is not theirs. No
  photo id or URL appears in any shared view, API, export-to-others or Share file.

## The unlock

- Uses `app.routers.auth.check_code` (same one-time-use protection as login: a code at or before `totp_last_step` is
  refused, so a code just used to sign in cannot be reused).
- A wrong or reused code is a failure counted by the same lockout as login (`sessions.record_failure`); a locked account
  gets the lockout message and no check is made.
- Unlock lasts exactly 10 minutes from the correct code and is **not** extended by activity. It ends on logout (the
  session row is deleted), on **Lock now**, or on expiry. The unlock is per browser session.
- With the setting **off**, the full image is served without a code and the page blurs/unblurs per click only.

## The setting (Settings page card "Body Recomp Photo 2FA")

- Shows the state and one control. **Turning on** needs account 2FA enabled; if it is not, the card says so and links to
  `/account/2fa`. **Turning off** needs a valid code (same checks as above).
- Account 2FA cannot be disabled while the photo setting is on ("Turn off Body Recomp Photo 2FA first").
- When account 2FA is removed another way (administrator reset, `app/users.py` and `routers/settings.py` reset paths), the
  photo setting is switched off in the same step, and the user sees a note the next time they open Settings.

## Page behavior (`/measurements`)

- **Body photos** section after Entries: newest first, grouped by date, each tile showing the blurred preview, date, angle
  label and note, with a Delete button (confirm).
- Setting off: click a tile → the sharp image replaces the blur for that tile until the page reloads or it is clicked again.
- Setting on and locked: click → dialog "Enter the 6-digit code from your authenticator" → on success every tile shows the
  sharp image, with a visible countdown ("Unlocked, 9:12 left") and a **Lock now** button; when the countdown ends, or on a
  reload after expiry, tiles are blurred again. A page rendered during an unlock renders tiles sharp (server decides).
- **Add body photo** (section header and Journal header) opens the upload dialog: file, date (default today), angle,
  optional note.
- Photos never appear in the Journal entry list, the dashboard, calendar or any other page.

## Backup, export, share, deletion

- New backup section **Body photos** (person level): table `body_photos` filtered by `owner_id`, file column `filename`,
  file directory key `body_photos` → `BODY_PHOTO_DIR`. `share_drop=True`, and the section is **not offered in Share files**.
- It is included in a person's Backup and Export, and in the administrator's whole-installation backup (which already
  carries every account's data). Loading a photos section **Add** skips nothing silently: photos have no natural key, so
  Add appends; Replace deletes the loader's own photos first (and their files) as other sections do.
- Deleting a photo deletes its file after the row commits. Deleting an account removes its rows (cascade) **and** its
  files. A restore swaps the photo folder with the other file folders.
- The existing guard test that requires every table to be in a section keeps this honest.

## Security notes

- The only copy of a sharp photo that leaves the server is from `/full` to its owner while permitted.
- Stored names are random and never reflect the owner or date; the folder is not served statically.
- Magic-byte/decode validation blocks non-images; the pixel cap blocks decompression bombs; the 15 MB size limit is the
  existing upload cap.

## Testing

Unit and route tests with invented data only (no real photos): metadata is stripped and orientation applied; large images
shrink; HEIC converts; non-images and oversize files are refused; the preview is blurred (pixel variance far below the
original) and never equals the original; owner-only access returns 404 for another user, a user the owner shares with, and
the administrator; `/full` is 403 when the setting is on and the session is locked, 200 when unlocked, and again 403 after
10 minutes (time injected); a correct code unlocks once and the same code is refused the second time; wrong codes count
toward lockout; the setting needs account 2FA on, needs a code to turn off, and account 2FA cannot be disabled while it is
on; admin and settings 2FA resets switch it off; backup includes photos and files, share excludes them; deleting a photo or
an account removes the file; the page renders previews not sharp URLs while locked, and sharp URLs while unlocked.

## Review Focus (input classes the tests above do not exercise on their own)

1. A photo id belonging to someone else guessed in the URL (404, never 403 that would confirm it exists).
2. A corrupted or truncated image, or a non-image renamed `.jpg`.
3. A very large pixel dimension that is small in bytes.
4. Two browser tabs: unlocking in one, locking in the other.
5. The unlock timer when the clock moves, and when the server restarts (the unlock is in the database, so it persists).
6. Account 2FA being removed while photos are unlocked.

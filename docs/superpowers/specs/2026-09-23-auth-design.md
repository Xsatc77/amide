# Legal Notice, Accounts & 2FA — Design

Date: 2026-09-23 · Status: approved by owner in chat

## 1. Intent

Secure Amide: every visit after a 10-minute absence starts with a legal notice the user must acknowledge, then a welcome screen (New User / Login). Accounts are private: each user sees only their own inventory and protocols. Optional two-factor authentication with an authenticator app. No email exists, so recovery is by server command.

## 2. Decisions

| Topic | Decision |
| --- | --- |
| Data | Private per user: inventory (incl. COA files) and protocols (incl. items/steps). Library (cards, dose ranges, notes, goal stacks) is shared reference. Sharing options come later in Settings. |
| Existing data | Owned by the **first** account created; that account is admin. |
| Registration | Open: New User on the welcome and login screens; no approval. |
| Username | 3–32 chars `[A-Za-z0-9._-]`, case-insensitive unique; displayed as typed. |
| Password | Case-sensitive; ≥ `AMIDE_PASSWORD_MIN_LENGTH` (default **4**) + 1 upper, 1 lower, 1 number, 1 special; typed twice. Stored as argon2 hash. |
| 2FA | TOTP authenticator app (QR + manual key, 6-digit, 30 s, ±1 step). No backup codes; lost device → server command. |
| Timeout | Session dies after **10 minutes** with no request; open tabs ping every 60 s. Dead session → notice → welcome/login. |
| Lockout | 5 failed passwords or 2FA codes → account locked 15 minutes. |
| CSRF | State-changing requests must come from Amide's own origin (Origin/Referer host check) + SameSite=Lax cookie. |
| Banner | Built-in Amide SVG; overridden by `data/branding/banner.{svg,png,jpg,jpeg,webp}` if present. |
| Recovery | `python -m app.users list | reset-password NAME | reset-2fa NAME`. |

## 3. Data (migration `0006`)

- `users`: id, username, username_key (lower, unique), password_hash, is_admin, totp_secret (nullable), totp_enabled, failed_attempts, locked_until, notice_accepted_at, created_at, last_login_at.
- `sessions`: id (token, 43-char urlsafe), user_id (nullable FK users, cascade), notice_accepted_at, twofa_pending, created_at, last_seen.
- `inventory_items.owner_id`, `protocols.owner_id`: nullable FK users (SET NULL on user delete is not needed—users are not deleted in this build). Rows with NULL owner are claimed by the first registered user.

## 4. Flow & routes

| Route | Purpose |
| --- | --- |
| `GET/POST /notice` | Legal notice; POST requires `understand=1`; records acceptance on the session (creates one if needed). |
| `GET /welcome` | Banner + New User / Login. Requires accepted notice. |
| `GET/POST /register` | New user form. On success: user created (first user = admin + claims data), signed in; if 2FA ticked → `/account/2fa`, else `/protocols`. |
| `GET/POST /login` | Username + password; Add New User button. 2FA users → `/login/2fa`. |
| `GET/POST /login/2fa` | 6-digit code. |
| `GET/POST /account/2fa` | Setup (QR + key + confirm code) or disable (confirm with current code). |
| `POST /logout` | Ends session → `/notice`. |
| `POST /session/ping` | Keeps the session alive while a tab is open. |
| `GET /branding/banner` | Owner's banner file if present, else 404 (templates fall back to the built-in SVG). |

Gate (middleware), for everything except `/static`, `/healthz`, `/branding`, and the auth routes themselves:
no/expired session or notice not accepted → `/notice`; not signed in → `/welcome`; 2FA pending → `/login/2fa`. `/api/*` answers 401 JSON instead of redirecting. `/` → `/protocols`.

Top bar (signed in): circle with the username's first letter + username, menu: Two-factor authentication, Log out.

## 5. Privacy enforcement

All inventory/protocol queries filter by `owner_id == current user`; fetching another user's item by id → 404. Protocol items may only link inventory items owned by the same user. Library is shared.

## 6. Testing

Unit: username/password rules, TOTP verify, lockout, session expiry. Integration: gate redirects (notice → welcome → protocols; API 401), notice checkbox required, register (validation, case-insensitive duplicates, first user admin + claims data), login (bad password, lockout, 2FA flow), 2FA setup/disable, timeout after 10 minutes (clock injected), logout, privacy (user B cannot see/edit/delete A's inventory, COA, protocols; API lists only own), cross-origin POST rejected, banner override, CLI reset commands. Existing tests run as a signed-in user.

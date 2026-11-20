# Architecture

A one-page map of how a request travels through Amide, who may reach what, and where the rules live. Read it before adding a route.

## Request Flow

```
browser or phone app
  -> SecurityMiddleware  (app/security_headers.py, outermost)
  -> the gate            (app/auth/gate.py, an HTTP middleware)
  -> a router            (app/routers/*.py, one file per feature)
  -> the database        (SQLAlchemy session from app.db.get_session)
```

- **SecurityMiddleware** wraps everything, including the gate's own redirects and refusals. It adds the security headers (Content Security Policy, frame, referrer and content-type rules, HSTS over https) and caps request bodies by path before any route reads them (413 over the cap).
- **The gate** decides who a request is. It refuses unsafe methods (POST, PUT, PATCH, DELETE) whose Origin or Referer names another site, applies the per-address throttles to sign-in, registration and notice posts, loads the browser session, sets `request.state.user` and the request's time zone, and then sends the browser to the right step.
- **A router** declares the route. Most routes take `uid = Depends(current_user_id)` from `app/auth/deps.py`; the gate has already made sure a signed-in person is behind it.

## The Gate's Rules

The steps for a browser are: legal notice (`/notice`), welcome (`/welcome`), sign in (password, then a 2FA code unless a passkey or a remembered device vouches), then the app. Pages answer with a redirect to the next step; paths under `/api/` and `/session/ping` answer 401 JSON instead.

The prefix allow-lists live at the top of `app/auth/gate.py`:

| List | What it holds | Who may reach it |
| --- | --- | --- |
| `OPEN_PREFIXES` | `/static/`, `/branding/` | anyone, no session needed |
| `TOKEN_PREFIXES` | `/api/ingest/`, `/calendar/feed/` | anyone with the right secret token; the route checks it, the gate does not |
| `OPEN_PATHS` | `/healthz`, `/notice`, `/sw.js` | anyone |
| `SIGN_IN_PATHS` | `/welcome`, `/register`, `/login...`, `/logout`, `/session/ping` | anyone who accepted the notice |
| `/legal/` | the legal texts | anyone |

Everything else needs a signed-in session that has finished 2FA. A session ends after `SESSION_IDLE_MINUTES` without a request, and after `SESSION_MAX_HOURS` in all, whichever comes first.

The test `tests/test_security_route_inventory.py` keeps this honest: every route the app declares is either behind the gate or on a reviewed list of public routes with a reason each, and every token route refuses a missing or wrong token.

## How Ownership Is Enforced

Nothing in the database layer filters rows for you. Each route that takes an id looks the row up and checks its owner itself, and answers 404 (never 403) when the row is someone else's, so a stranger cannot tell which ids exist.

- **Own-or-404 helpers** sit next to the routes that use them: `_own_item`, `_own_active_vial`, `_own_order_item` (`app/routers/inventory.py`), `_own_photo` (`app/routers/body_photos.py`, `app/routers/journal.py`), `_own_or_404` (`app/routers/food.py`), `_own_protocol_item` (`app/routers/dosing.py`), and the owner clause in the queries of the other routers.
- **Shared on purpose:** the peptide library and its card images, the learn pages, starter foods (no owner), vendors with their wallets and price lists, and price data. Reading is open to every signed-in account. Vendor edits and deletes are limited to the person who added the vendor or an administrator (`app/vendors/access.py`); price alerts and price corrections are administrator-only.
- **The share model:** a `Share` row lets one person (the owner) show one category (inventory, personal data) to another (the grantee), one direction at a time. Sharing only widens what the grantee may read, through helpers such as `_visible_items`; it never lets them change the owner's rows. Both sides can end a share, and each change is written to the security log.
- **Administrator-only routes** answer 404 to everyone else (`_require_admin` in `app/routers/settings.py`, `_admin` in the ingest and price correction routers).

The test `tests/test_security_cross_user.py` finds every route with an id in its path, calls it as a second ordinary account with the first account's ids, and proves that nothing of the first account changed and that no 2xx answer shows its data. A new id parameter that the test does not understand fails it, which is the prompt to teach it.

## Token-Authenticated Routes

Three kinds of route skip the session and carry their own secret:

- **Watcher API** (`/api/ingest/...`, `app/routers/ingest_api.py`): a bearer token created by an administrator in Settings. Only a hash of it is stored.
- **Apple Health receiver** (`/api/ingest/health`, `app/routers/health_api.py`): a bearer token of the person who owns the data, with its own request limit and its own body cap.
- **Calendar feed** (`/calendar/feed/<token>.ics`, `app/routers/calendar.py`): the address itself is the secret; a wrong token is a 404, and fetches are limited per address.

Each has an in-memory rate limiter (`RateLimiter` in `app/ingest/tokens.py`), so a guesser is slowed down.

## Background Jobs

Started in `app.main.lifespan` and stopped with it. Each is a loop that catches errors, writes one line to the log, and tries again at the next interval:

| Job | File | Does |
| --- | --- | --- |
| Ingest worker | `app/ingest/worker.py` | reads received price lists every 20 seconds |
| Reminders | `app/reminders.py` | sends dose and photo reminders to a person's notification topic |
| Scheduled backup | `app/backup/scheduled.py` | checks hourly and writes the scheduled backup when one is due |
| Health history | `app/health/history.py` | purges raw Apple Health copies, reprocesses unfinished chunks |

Background jobs have **no request time zone**. The gate sets "today" from the signed-in person's setting or browser; a job has no browser, so it uses each person's saved time zone (or UTC) explicitly and must never call code that quietly reads the request's zone.

## Where Secrets Live

Two-factor seeds and AI provider keys are sealed with AES-256-GCM (`app/secretbox.py`); session ids are stored only as a hash; passwords and tokens are hashed. The key comes from `AMIDE_SECRET_KEY` or `data/secret.key`. The full story, including what a copy of `data/` carries and how backups treat secrets, is in [DEPLOYING.md](DEPLOYING.md#secrets-at-rest). Never log a secret, a code or a token: `app/auth/audit.py` stores only names, event labels and short details.

## Where Configuration Lives

`app/config.py` reads almost every setting from the environment once, with defaults and bounds. A few settings are read elsewhere because they are switches for one module: `AMIDE_API_DOCS` in `app/main.py`, `AMIDE_CSP` and `AMIDE_MAX_REQUEST_MB` in `app/security_headers.py`, `AMIDE_AI_LOCAL_ALLOWED_HOSTS` and `AMIDE_AI_ALLOW_LOOPBACK` in `app/ai_summary.py`, `AMIDE_SECRET_KEY` in `app/secretbox.py`, `AMIDE_VERSION` in `app/version.py` and the launcher settings in `app/__main__.py`. The settings people may change are listed in the README.

## Rules For New Routes

Before you merge a new route, check each line:

1. **Declare who owns it.** A route that takes an id looks up the row and checks the owner (own-or-404). Shared data is on the reviewed list in the cross-user test, with a reason.
2. **Take the session, do not invent one.** Use `Depends(current_user_id)`. Public routes need a line, with a reason, in `PUBLIC` in `tests/test_security_route_inventory.py`.
3. **No inline scripts or event handlers.** Scripts live in `app/static/js`; the Content Security Policy blocks the rest.
4. **Redirect with `safe_redirect`** whenever the target comes from the request (`next`, a return address).
5. **Serve downloads and uploaded files with `FILE_HEADERS`** (sandboxed, no sniffing) from `app/security_headers.py`.
6. **Rate-limit anything unauthenticated**: a throttle group in `app/auth/throttle.py` or a `RateLimiter`.
7. **Cap and check uploads**: read at most the configured limit, check the extension and the first bytes, and decode images rather than storing them as sent.
8. **Add the route to the cross-user test if it takes an id.** It is found automatically; teach `kind_for` in `tests/test_security_cross_user.py` what the new parameter means.
9. **Record security-relevant changes** (tokens, shares, restores, deletes of shared data) with `audit.record(...)` and never put a secret in the detail.
10. **Keep headings in Title Case** in templates and docs; a guard test checks.

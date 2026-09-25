<p align="center"><img src="docs/images/amide-banner.jpg" alt="Amide — Peptide Tracking: all-in-one, self-hosted solution for peptide management" width="100%"></p>

# amide
Amide is a self-hosted Peptide Tracking System that brings together tracking, scheduling, and research in one place.

Amide is the name of the chemical bond that holds a peptide together, which is much like the goal of this project. Every peptide is a chain of amino acids, and each link in that chain is an amide bond. That "bond" idea maps onto what this app does: it links vials, doses, schedules, and logs into one chain of records, allowing you to see where you started, where you are now, as well as finding what works and what did not. At the same time, keeping YOUR health data in YOUR hands, without being held hostage by yet another subscription service

Amide's goal is a self-hosted method of tracking:

- Quick View Dashboard
- Inventory
- Order Tracking
- Personal Distributor Contacts
- Protocols
- Titration Schedules
- Daily Dosing
- Macro Tracking including Water, Protein, & Fiber
- Reconstitution Calculator
- Peptide Pen Tracking
- Weight & Measurement Tracking
- Labs & Medical Results
- Exercise Plan Building & Tracking
- Journal
- Peptide Library
- Peptide Learning
- Health Tracker Integrations (Hume, Apple, etc)

While all of these features may not be active yet, this is the goal.  The system that bonds you & your peptide use together, for long term success.

Amide: every dose, linked.

----------------------------------------------------------------------------------------------------
LEGAL NOTICE

No information herein constitutes a medical prescription, personal use recommendation, or substitution for professional health guidance.
The doses, cycles, and protocols described reflect ranges used primarily in the U.S. in research and integrative medicine contexts. 

Regulatory status varies by country.

Always remember: Consult a doctor before using any substance. Responsible use begins with information and adequate professional guidance.
----------------------------------------------------------------------------------------------------


## Status

**v1.0 — Active Vials.** Reconstituting a Lyophilized inventory item (from the Inventory page or the Calculator) creates a tracked Active Vial — concentration, total content, discard-by date, and doses per vial, shown as a card with the real vial icon. Expired vials flag themselves for a one-time-per-24-hours discard prompt; discarding never deletes the record, just retires it. A default discard window (in days) is configurable in Settings.

**v0.9 — Sharing.** Per-person sharing, off by default: grant specific users a read-only view of your Inventory, your Protocols (personal data), or both, from the new Sharing section in Settings. Shared inventory merges into the viewer's own list, tagged by owner; shared protocols show in a separate "Shared with me" tab, never mixed into your own view or Calendar. Vendors are now a shared list across all users (like the peptide library) — what you bought from one stays private unless you share your inventory.

**v0.8 — Settings.** A Settings page (from the username menu) for changing your username/password, turning two-factor authentication on or off, setting an email (for future sharing features) and timezone, and picking a colorway — Light, Dark, or one of seven named palettes, including a pure-black-and-white High Contrast option. Admins get a Manage Users panel: add accounts, reset passwords, remove someone's two-factor authentication, or delete an account (which also removes everything they own, after two confirmations).

**v0.7 — Inventory Foundations.** The inventory form adapts to the medium: amount + unit (mg/mcg/IU), volume for Liquid, units-per-package for Autoinjector/Pill, plus expiration date and storage location, each required only where it makes sense. Vendors are now their own table (type a name to reuse or create it, like the peptide picker). The inventory list has search, medium filters, and sortable columns. Backup & restore (JSON + CSV export, additive-only import) is on the account menu.

**v0.6 — Reconstitution Calculator.** Live vial + BAC water + dose → concentration, draw volume and U-100 syringe units, with a syringe-fill visual, presets, and a reverse solver (pick the units you want, it works out the water). Pre-fills from Inventory (lyophilized items) or a Protocol's dose. *Not yet built:* turning a mixed vial into a tracked "Active Vial" that depletes inventory and has a discard-by date — that's still open, see the roadmap.

**v0.5 — Calendar.** Month, week and day views of every dose your active and scheduled protocols call for (titration steps included). Click any line, block or card to see exactly what's due.

**v0.4 — Accounts & legal notice.** Every visit after 10 minutes away starts with the legal notice, then New User / Login. Each user's inventory and protocols are private (the library is shared). Optional two-factor authentication with an authenticator app. See *Accounts* below.

**v0.3 — Peptide Library.** A searchable Library of your peptide cards (details as text plus the original card image), with your own dose range, frequency, aliases, notes and goal stacks per peptide. The protocol builder searches the whole library as you type. See *Importing your peptide cards* below.

**v0.2 — Protocols.** Build protocols from goals (suggested peptide stacks), set your own dose and schedule per peptide, optional titration steps, and see active protocols as cards; saved protocols can be paused, ended, repeated, or deleted.

**v0.1 — Inventory.** Add, edit, and delete inventory items (name, count, vial size, medium, lot/batch #, cost, vendor, order/shipped/arrival dates, COA photo/PDF with lab-measured vial size and purity, notes). See [docs/ROADMAP.md](docs/ROADMAP.md) for what comes next.

## Running Amide

All of your data (the SQLite database and uploaded COAs) lives in one folder, `data/`. Back up that folder and you've backed up everything.

### With Docker (recommended for self-hosting)

```bash
docker compose up -d --build
```

Then open http://localhost:1707 (or `http://<server-ip>:1707` from another device).

To use a different port, copy `.env.example` to `.env` and change `AMIDE_PORT`, then run `docker compose up -d` again.

### Without Docker (for development)

Requires Python 3.12+. Run each line one at a time.

**Windows (PowerShell):**

```powershell
py -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m app --reload
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m app --reload
```

Then open http://localhost:1707. Press Ctrl+C to stop. Run the tests with `.venv\Scripts\pytest` (Windows) or `.venv/bin/pytest`.

Database migrations are applied automatically on startup.

### Importing your peptide cards

Your card PDF is imported once with a helper script (it needs one extra library, PyMuPDF, which the app itself doesn't use):

```bash
pip install -r tools/requirements.txt
python tools/import_cards.py "path/to/Peptide Cards.pdf"
```

Card text and images are saved in `data/library/` (private, never committed). To load them into another database later, no PDF needed: `python -m app.library_load`. Details: [tools/README.md](tools/README.md).

### Settings (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `AMIDE_PORT` | `1707` | Port Amide is reachable on |
| `AMIDE_HOST` | `127.0.0.1` | Address to listen on when run without Docker (`0.0.0.0` = reachable from other devices) |
| `AMIDE_DATA_DIR` | `./data` (`/data` in Docker) | Where the database and uploads are stored |
| `AMIDE_DATABASE_URL` | `sqlite:///<data dir>/amide.db` | Override to use another database |
| `AMIDE_MAX_UPLOAD_MB` | `15` | Max COA upload size |
| `AMIDE_PASSWORD_MIN_LENGTH` | `4` | Minimum password length (raise this for stronger passwords) |

### Accounts

- **First run:** accept the legal notice, choose **New User**. The first account is the admin and takes over any data created before accounts existed.
- **Privacy:** each account sees only its own inventory and protocols. The peptide library is shared.
- **Passwords:** case sensitive, at least `AMIDE_PASSWORD_MIN_LENGTH` characters with an uppercase, lowercase, number and special character. Usernames are not case sensitive.
- **Two-factor (optional):** any authenticator app (Google/Microsoft Authenticator, Authy, 1Password, Bitwarden…). Turn it on at sign-up or from the menu under your initial (top right).
- **Timeout:** with no Amide tab open for 10 minutes you're signed out; the next visit shows the legal notice again.
- **Lockout:** 5 wrong passwords or codes lock that account for 15 minutes.
- **Recovery** (there's no email), run on the server:

  ```bash
  python -m app.users list
  python -m app.users reset-password <username>
  python -m app.users reset-2fa <username>
  ```

  (In Docker: `docker compose exec amide python -m app.users list`.)
- **Banner:** put your own image at `data/branding/banner.svg` (or `.png`, `.jpg`, `.webp`) and it replaces the built-in one on the sign-in screens.

> Logins over plain `http://` are fine on your home network. To reach Amide over the internet, put it behind HTTPS (a reverse proxy) or a VPN.

## Tech stack

Python · [FastAPI](https://fastapi.tiangolo.com/) · [SQLAlchemy](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/) migrations · SQLite · server-rendered Jinja templates with a little vanilla JS · Docker.

## Project layout

```
app/
  __main__.py        `python -m app` launcher
  main.py            app entry point
  models.py          database tables
  routers/           pages + JSON API, one file per feature
  library/           card loader + library form rules
  protocols/         protocol status + builder form rules
  templates/         HTML
  static/            CSS / JS
migrations/          Alembic database migrations
tests/               pytest suite
tools/               one-off helpers (card PDF import)
docs/ROADMAP.md      where this is going
```

--------------------------------------------------------------------------------------------------------------------------------------------------------------------



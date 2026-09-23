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

### Settings (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `AMIDE_PORT` | `1707` | Port Amide is reachable on |
| `AMIDE_HOST` | `127.0.0.1` | Address to listen on when run without Docker (`0.0.0.0` = reachable from other devices) |
| `AMIDE_DATA_DIR` | `./data` (`/data` in Docker) | Where the database and uploads are stored |
| `AMIDE_DATABASE_URL` | `sqlite:///<data dir>/amide.db` | Override to use another database |
| `AMIDE_MAX_UPLOAD_MB` | `15` | Max COA upload size |

> Amide has no login yet. Run it on your home network or behind a VPN/reverse proxy with authentication — don't expose it directly to the internet.

## Tech stack

Python · [FastAPI](https://fastapi.tiangolo.com/) · [SQLAlchemy](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/) migrations · SQLite · server-rendered Jinja templates with a little vanilla JS · Docker.

## Project layout

```
app/
  __main__.py        `python -m app` launcher
  main.py            app entry point
  models.py          database tables
  routers/           pages + JSON API, one file per feature
  templates/         HTML
  static/            CSS / JS
migrations/          Alembic database migrations
tests/               pytest suite
docs/ROADMAP.md      where this is going
```

--------------------------------------------------------------------------------------------------------------------------------------------------------------------



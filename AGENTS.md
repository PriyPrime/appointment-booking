# AGENTS.md

## Stack
- Frontend: plain HTML/CSS/ES6 JavaScript only — no frameworks, no build step, no npm. Use the Fetch API for all API calls.
- Backend: Python 3.10+ with Flask. JSON API only. Flask also serves the static frontend.
- Database: SQLite via the standard `sqlite3` module, no ORM. Enable `PRAGMA foreign_keys = ON` on every connection (it defaults off).
- Dependencies: `flask` for the app, `pytest` for tests. Do not add others without recording why in `DECISIONS.md`.

## Layering
- Frontend and backend are separate layers. The frontend never touches the database.
- All business rules are enforced on the server; client-side checks are for convenience only and are not authoritative.

## Secrets
- No secrets in code. Read the secret key from an environment variable with a development fallback (see `config.py`).

## Layout
- `app.py` — app factory, blueprint registration, entry point.
- `config.py` — settings: hours, step, cutoff, secret key, DB path.
- `db.py` — connection helper, schema creation, seed data.
- `schema.sql` — all tables and indexes (single source of truth).
- `routes/` — blueprints: `auth.py`, `services.py`, `providers.py`, `slots.py`, `appointments.py`, `reports.py`.
- `services_logic/` — `slots.py` (slot generation), `notifications.py`.
- `static/` — `index.html`, `css/`, `js/` (one JS file per page or module).
- `tests/` — pytest files mirroring the routes.
- `README.md` — setup, run, test, default accounts.
- `DECISIONS.md` — assumptions made and their rationale.

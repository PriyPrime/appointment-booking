# Appointment Booking System

A small appointment booking app for service providers such as a clinic or
salon. Customers book time slots, providers manage their availability, and an
admin manages services and views reports.

- **Backend:** Flask 3 (JSON API) — `app.py`
- **Frontend:** plain HTML, CSS and ES6 JavaScript, served by Flask
- **Database:** SQLite through the standard `sqlite3` module (no ORM)

## Prerequisites

- Python 3.10 or newer (tested with 3.13)
- Flask (the only runtime dependency)
- pytest (tests only)

On Windows, if `python` opens the Microsoft Store instead of running Python,
use the `py` launcher as shown below.

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install flask pytest
```

## Run

```powershell
py app.py
```

On first run the app creates `appointment_booking.db`, applies `schema.sql`
and inserts the seed data. Open <http://127.0.0.1:5000/>.

Environment variables:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session signing key. Set a real value outside development. |
| `DB_PATH` | Alternative SQLite file location. Defaults to `appointment_booking.db`. |
| `MAIL_SERVER` | SMTP host. Empty (the default) logs emails to the console instead of sending them. |
| `MAIL_PORT` | SMTP port. Defaults to `587`. |
| `MAIL_USERNAME` | SMTP username, if the server needs one. |
| `MAIL_PASSWORD` | SMTP password, if the server needs one. |
| `MAIL_USE_TLS` | `1`/`true` to call `STARTTLS`. Defaults to true. |
| `MAIL_FROM` | Sender address on outgoing mail. |
| `MAIL_TIMEOUT_SECONDS` | How long to wait for the SMTP server. Defaults to 10. |

## Notifications

Bookings, cancellations and reschedules write `pending` notification rows.
Reminders are added for booked appointments inside `REMINDER_HOURS_BEFORE`
(24 hours by default). A worker sends the queued rows and records `sent` or
`failed`.

Run the worker from a scheduler or by hand:

```powershell
py -m flask --app app notifications run
```

Or without the Flask CLI:

```powershell
py -m services_logic.notifications
```

To run the two steps separately:

```powershell
py -m flask --app app notifications reminders
py -m flask --app app notifications send
```

With no `MAIL_SERVER`, each email is written to the app log, so development
needs no mail account. Configure the `MAIL_*` variables above to send real
mail. The worker is not started automatically; schedule it, for example every
few minutes.

## Reports

Admins get a **Manage** tab with three screens: services, providers and
reports. Reports cover an inclusive date range and return totals plus
breakdowns by service, provider and day. Revenue counts `completed`
appointments only.

The same data is available as JSON and CSV:

| Endpoint | Purpose |
|---|---|
| `GET /api/reports/summary?from=YYYY-MM-DD&to=YYYY-MM-DD` | Totals and all three breakdowns in one response. |
| `GET /api/reports/appointments?from=…&to=…&status=…` | Filtered appointment rows. Also accepts `provider_id` and `service_id`. |
| `GET /api/reports/export?from=…&to=…` | The same rows as a downloadable CSV. |

Both bounds are inclusive, default to the last 30 days, and a range longer
than `REPORT_MAX_RANGE_DAYS` (366) is rejected.

Providers open the **Availability** tab to edit their own weekly hours and
time off. Admins see the same screen with a provider selector.

## Documentation

`docs/` holds the report source material:

| File | Contents |
|---|---|
| `docs/modules.md` | Module list and responsibilities |
| `docs/database.md` | Entity–relationship description and indexes |
| `docs/dfd.md` | Context and level-1 data flow diagrams |
| `docs/api.md` | Complete endpoint table |
| `docs/workflows.md` | Booking, cancel, reschedule and reporting sequences |

`tests/test_phase9.py` fails if a documented API path disappears or a new
route is added without a line in `docs/api.md`, so the table cannot silently
drift.

## Test

```powershell
py -m pytest
```

Each test run uses a temporary database, so the development database is never
touched.

## Default accounts

| Role | Email | Password |
|---|---|---|
| Admin | `admin@example.com` | `admin12345` |
| Provider | `alice@example.com` | `provider123` |
| Provider | `bob@example.com` | `provider123` |

Self-registration creates customers only. Provider accounts are created by an
admin. Change these credentials before deploying.

## Folder map

```
appointment-booking/
  app.py                 app factory, blueprint registration, entry point
  config.py              settings (hours, step, cutoff, secret key, DB path)
  db.py                  connection helper, schema creation, seed data
  schema.sql             all tables and indexes
  routes/                Flask blueprints (auth, services, providers, ...)
  services_logic/        slot generation and notifications
  static/                index.html, css/, js/ (customer and staff screens)
  tests/                 pytest files mirroring the routes
  docs/                  module list, ER notes, DFDs, API table, workflows
  README.md              this file
  DECISIONS.md           assumptions and deviations
```

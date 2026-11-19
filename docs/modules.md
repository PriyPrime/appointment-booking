# Module list

Every source file, what it owns, and the functions a report reader may want to
name.

## Entry points

| File | Responsibility |
|---|---|
| `app.py` | `create_app()` builds the Flask app, loads `config.py`, registers blueprints and CLI commands, maps `/` to `static/index.html`, and turns unknown `/api/*` paths into JSON 404s. Running the file also initialises and seeds the database. |
| `config.py` | All tunables in one class: slot step, cancellation cutoff, booking horizon, session lifetime, reminder window, report range limits, secret key, database path, mail settings, seed accounts. Environment variables override the values that differ per environment. |
| `db.py` | `connect()` opens SQLite with foreign keys and a busy timeout; `get_db()` returns the per-request connection; `init_db()` applies `schema.sql`; `seed()` inserts demo data once; `utc_now()` produces stored timestamps. |
| `schema.sql` | The single source of truth for tables, checks and indexes. |

## Routes (application layer)

Each module exposes a Flask `Blueprint` named `bp`. All API routes live under
`/api`.

| Module | Blueprint prefix | Responsibility | Key functions |
|---|---|---|---|
| `routes/auth.py` | `/api/auth` | Registration, login, logout, session introspection, and the shared permission decorators. | `current_user()`, `login_required`, `role_required()`, `user_json()`, `error()` |
| `routes/services.py` | `/api` | Public service list and admin create/update. | `_validate()`, `_service_json()`, `list_services()`, `create_service()`, `update_service()` |
| `routes/providers.py` | `/api` | Provider directory, provider accounts, weekly availability and time off. | `_manageable_provider()`, `_upcoming_conflicts()`, `replace_availability()`, `add_time_off()`, `delete_time_off()` |
| `routes/slots.py` | `/api` | Thin HTTP wrapper around slot generation. | `list_slots()` |
| `routes/appointments.py` | `/api` | Booking, role-scoped listing, detail, cancel, reschedule and status changes. Holds the shared appointment query. | `_check_booking_rules()`, `_queue_notification()`, `APPOINTMENT_SELECT`, `appointment_json()` |
| `routes/reports.py` | `/api/reports` | Admin aggregates and the CSV export. | `_date_range()`, `_summary()`, `_by_service()`, `_by_provider()`, `_by_day()`, `_appointment_filters()`, `export_appointments()` |

`routes/__init__.py` registers them in dependency order and is the only place
that knows the full set.

## Business logic (service layer)

| Module | Responsibility | Key functions |
|---|---|---|
| `services_logic/slots.py` | One definition of time parsing and of which start times are bookable, used by both the slot endpoint and the booking validator. | `parse_date()`, `valid_time()`, `to_minutes()`, `to_hhmm()`, `overlaps()`, `generate_slots()` |
| `services_logic/notifications.py` | Queues reminder rows, builds messages, sends through SMTP or the console, and exposes the `flask notifications ...` CLI. | `generate_reminders()`, `build_message()`, `send_email()`, `send_pending()`, `run_all()`, `register_cli()` |

`generate_slots()` accepts injectable `today` and `now_hhmm` so slot tests do
not depend on the wall clock. Every business rejection returns an empty list;
only malformed input becomes a 400.

## Frontend (presentation layer)

Static files are served at the site root, so the browser requests
`/js/app.js`, not `/static/js/app.js`.

| File | Responsibility |
|---|---|
| `static/index.html` | Markup for all four views and the reschedule dialog. |
| `static/css/styles.css` | Theme, layout, tables, dialog and responsive rules. Colours live in custom properties. |
| `static/js/api.js` | `request()` — one `fetch` wrapper that parses JSON and raises `ApiError` with the server's message. |
| `static/js/ui.js` | Shared helpers: `$`, `$$`, `make()`, `setMessage()`, `showView()`, `setBusy()`, date/time/money formatters, cutoff check. |
| `static/js/auth.js` | Session state in memory plus `login`, `register`, `logout`, `loadSession`. |
| `static/js/slots.js` | `fetchSlots()`, `findNextAvailable()`, `renderSlotButtons()`. |
| `static/js/booking.js` | The three-step booking flow and its race guards. |
| `static/js/appointments.js` | Appointment list, cancellation, reschedule dialog, staff status actions. |
| `static/js/staff.js` | Admin services and providers screens, availability editor, time off. |
| `static/js/reports.js` | Report range controls, summary cards, breakdown tables, CSV link. |
| `static/js/app.js` | Bootstraps the session, renders the account bar, wires navigation and the auth forms. |

## Tests

| File | Covers |
|---|---|
| `tests/conftest.py` | Fresh temporary database and `client` fixture per test. |
| `tests/test_phase1.py` | Schema, seed idempotency, JSON 404, page serving. |
| `tests/test_auth.py` | Registration, login, logout, session expiry, rate limiting. |
| `tests/test_phase3.py` | Services, providers, availability, time off, rule-11 conflicts. |
| `tests/test_phase4.py` | Slot generation and booking, including the concurrency guard. |
| `tests/test_phase5.py` | Listing, detail, cancel, reschedule, status flow, cutoff. |
| `tests/test_phase6.py` | Reminder generation and notification delivery. |
| `tests/test_phase7.py` | Customer frontend is served. |
| `tests/test_phase8.py` | Reports, permissions, CSV export, staff assets. |
| `tests/test_phase9.py` | Documentation exists and the API table matches the route table. |

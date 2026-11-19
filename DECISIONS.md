# Decisions and assumptions

The spec leaves the following open. Each choice is recorded here.

## Phase 1 — skeleton, schema and seed

- **Prices** are stored as SQLite `REAL` and treated as currency amounts. Reports will sum `price` for `completed` appointments only.
- **Timestamps** are stored as UTC ISO-8601 strings produced by `datetime.now(timezone.utc).isoformat()`.
- **Database file** defaults to `appointment_booking.db` in the project root. The `DB_PATH` environment variable overrides it (tests use a temporary file).
- **Secret key** comes from `SECRET_KEY`, falling back to `dev-secret-change-before-deploying` for development only.
- **Schema creation** uses `CREATE ... IF NOT EXISTS`, so `init_db()` can run on every start.
- **Seed guard:** seed data is inserted only when the `users` table is empty, which makes repeated starts no-ops.
- **Default admin:** `admin@example.com` / `admin12345`.
- **Default providers:** `alice@example.com` and `bob@example.com`, both with password `provider123`.
- **Seed services:** General Consultation, Follow-up Visit, Haircut, Colour Treatment.
- **Seed availability:** both providers work Monday-Friday, 09:00-12:00 and 13:00-17:00 (a lunch break splits the day).
- **Email case:** emails are lowercased by the application before storage. The database `UNIQUE` constraint then matches case-insensitively in practice.
- **Duration multiple:** the "multiple of `SLOT_STEP_MIN`" rule is enforced in the service routes (phase 3), because the step is configuration rather than a schema constant.

## Phase 2 — authentication and sessions

- **Passwords** are hashed with Werkzeug's `generate_password_hash` (salted and slow) and verified with `check_password_hash`.
- **Sessions** are Flask signed-cookie sessions. They are marked permanent, so the cookie expires after `SESSION_LIFETIME_HOURS`, and the server also stores `issued_at` and rejects older sessions on every request. This makes expiry testable without waiting eight hours.
- **Session cookie flags:** `HttpOnly` and `SameSite=Lax`. `Secure` is not set because the app is served over plain HTTP in development.
- **Failed-login limit:** five failures per client IP within five minutes trigger a lockout response. The counter lives in `app.extensions` (in-process), which is sufficient for this single-process deliverable and resets on restart.
- **Duplicate registration** returns 409; the email is lowercased first, so duplicates are case-insensitive.
- **Validation limits:** name 1-80 characters, email at most 254 characters and matched against a simple pattern, password 8-128 characters, phone at most 30 characters.
- **Inactive accounts** cannot log in and are treated as logged out. Their login failure uses the same generic message as a wrong password.
- **`GET /api/auth/me`** returns the user object, or JSON `null` when logged out.
- **Role checks:** `login_required` returns 401 for anonymous users; `role_required(*roles)` returns 403 for a logged-in user with the wrong role. Ownership checks remain inside the relevant routes.

## Phase 3 — services, providers, availability and time off

- **Service updates are partial.** `PUT /api/services/{id}` changes only the fields present and keeps the rest. Creating requires `name`, `duration_min` and `price`.
- **Provider creation body:** `name`, `email`, `password`, optional `phone`, optional `specialization`, optional `service_ids`. The provider logs in with that email and password.
- **Provider updates are partial:** `specialization`, `active` and `service_ids` can be sent alone. Sending `service_ids` replaces the whole set.
- **Public vs admin provider objects:** public responses omit `email` and `phone`; admin responses include them.
- **`include_inactive=1`:** the spec grants it for services. I also allow it on `GET /api/providers` for admins, because the admin page must find and reactivate a deactivated provider. Non-admins silently get the active-only list.
- **Availability format:** `{"windows": [{"weekday": 0, "start_time": "09:00", "end_time": "12:00"}]}`. `PUT` replaces the whole schedule atomically. An empty list is allowed when no upcoming booking depends on the old hours.
- **Rule 11 conflicts** return `409` with the standard `error` field plus a `conflicts` array of affected appointments.
- **Time off** must be today or a future date. Creating it is refused with `409` when a `booked` appointment exists on that date. Deleting is always allowed.
- **Deactivating a service or provider** does not cancel or invalidate existing bookings; it only hides them from new bookings. Removing a provider-service link behaves the same way.
- **Availability is replaced, not merged.** Validation happens before any `DELETE`, so a refused request leaves the old schedule intact.

## Phase 4 — slot generation and booking

- **Slot lookup contract:** malformed query parameters or an invalid date return `400`. Business rejections (past date, beyond horizon, inactive provider/service, provider not offering the service, time-off day) return `200` with `{"slots": []}`.
- **Generated slot rule:** every start is `window_start + k * SLOT_STEP_MIN`, and `start + duration` must fit inside the same window. Overlaps use half-open ranges, so back-to-back bookings are allowed.
- **Booking start times:** `POST /api/appointments` only accepts a start that lies on that grid inside one availability window. Arbitrary times such as 09:15 are rejected with `400`.
- **Concurrency:** the booking transaction starts with `BEGIN IMMEDIATE`, so the overlap re-check and insert are serialized. `busy_timeout` is 10 seconds so the second writer waits instead of failing immediately. The partial unique index remains the final guard.
- **Status codes:** `404` for a missing provider or service; `400` for business-rule violations; `409` for a taken or overlapping slot, with the message "That time was just taken. Pick another slot."
- **Customer double-booking:** rule 9 is checked explicitly, although it is also covered by the provider-overlap rule for the same provider.
- **Confirmation notification:** inserted in the same transaction as the booking with status `pending`. The transport that sends it is added in phase 6.
- **Booking is customer-only:** providers and admins get `403`; anonymous callers get `401`.
- **Testability:** `generate_slots` accepts injectable `today` and `now_hhmm` values so unit tests do not depend on the wall clock.

## Phase 5 — listing, detail, cancel, reschedule and status flow

- **Listing:** `GET /api/appointments` is role-scoped. Customers see their own rows, providers see rows for their provider record, and admins see everything. Optional filters are `status`, `date`, `from` and `to` (inclusive); invalid values return `400`.
- **Detail:** `GET /api/appointments/{id}` returns `404` when the row is missing and `403` when the caller is not the owning customer, the assigned provider or an admin.
- **Action verbs:** cancel, reschedule and status accept both `PATCH` and `POST` on their action URLs, so the frontend can use the natural `PATCH` while simple clients can use `POST`.
- **Cancellation cutoff:** customers may cancel or reschedule only while `now <= start - CANCEL_CUTOFF_HOURS` (2 hours). Providers and admins are not limited, because the cutoff exists to protect providers from last-minute customer changes.
- **Cancellation:** only `booked` rows can be cancelled. Cancelling sets `status='cancelled'`, updates `updated_at`, queues a `cancellation` notification in the same transaction, and immediately frees the slot because the partial unique index only covers `booked` rows.
- **Reschedule:** only `booked` rows can move. The body accepts any of `date`, `start_time`, `provider_id` and `service_id`; omitted fields keep their current value. The target is re-validated with the full booking rules, ignoring the appointment being moved. A `reschedule` notification is queued in the same transaction.
- **Status flow:** `booked` may become `completed`, `cancelled` or `no_show`. `completed`, `cancelled` and `no_show` are terminal and return `409` if changed again. Only providers and admins may change status, and a provider may only change their own appointments.
- **Completion timing:** `completed` and `no_show` are refused with `400` before the appointment's start time. `cancelled` is allowed at any time through the status route.
- **Local wall-clock time:** `date` and `start_time` are compared as local naive datetimes. Only audit timestamps such as `created_at` use UTC, because the business works in the server's local time zone.
- **Race safety:** reschedule uses `BEGIN IMMEDIATE` like booking. Cancel and status use a guarded update (`WHERE id = ? AND status = 'booked'`) so two simultaneous requests cannot both act on the same row.
- **Notifications:** every cancellation and reschedule queues a `pending` row now; phase 6 adds the sender that marks them `sent` or `failed`.

## Phase 6 — reminders and notification delivery

- **Transport:** standard-library `smtplib` only. With `MAIL_SERVER` empty (the default) messages are written to the app log instead of being sent, so development needs no mail account. `MAIL_USE_TLS` controls `STARTTLS`; implicit SSL on port 465 is not implemented.
- **SMTP variables:** `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_USE_TLS`, `MAIL_FROM` and `MAIL_TIMEOUT_SECONDS`. A malformed port or timeout falls back to the default instead of failing startup.
- **Recipient:** every notification goes to the customer's email address. Providers are not copied; that would need a separate delivery policy.
- **Reminder window:** a booked appointment gets one reminder when `now < start <= now + REMINDER_HOURS_BEFORE`. Past and cancelled appointments are ignored.
- **Reminder uniqueness:** a partial unique index (`idx_notifications_one_reminder`) and `INSERT OR IGNORE` make reminder generation idempotent, including across two workers racing.
- **Delivery worker:** `flask --app app notifications reminders|send|run` (or `py -m services_logic.notifications`). The worker is not started automatically; it is designed to be run by a scheduler or by hand.
- **Retries:** a message that raises is marked `failed` with no `sent_at`. Failed rows are not retried automatically, so a permanently bad address cannot block the queue. Re-running only processes `pending` rows.
- **Stale mail:** if an appointment is no longer `booked`, a queued confirmation, reminder or reschedule is marked `failed` instead of sent. Cancellation mail is still delivered, so the customer always learns about the final state.
- **Marking sent:** the status changes only after the sender returns without raising. A crash between sending and updating could cause one duplicate on the next run; the alternative (mark first) risks losing mail.
- **Message format:** plain text, built with `email.message.EmailMessage`. Subjects differ per type so mailbox rules can filter them.
- **Why not send inline:** booking/cancellation routes only queue rows. A mail server outage therefore never delays or fails a booking.
- **Worker setup:** each CLI command runs `init_db()` and `seed()` first (both idempotent), so it works on a fresh clone.

## Phase 7 — customer web interface

- **One page, two views:** `static/index.html` holds the booking flow and the appointment list. Navigation switches `.view` sections and updates the URL hash (`#book`, `#appointments`) with the History API, so a refresh or a shared link opens the right view without a server-side router.
- **Native ES modules, no build step:** `static/js/app.js` imports the other modules directly. Flask already serves `static/` at the site root, so the browser requests `/js/app.js` and `/css/styles.css`. Nothing is bundled or transpiled.
- **Module split:** `api.js` wraps `fetch` and error handling, `ui.js` holds DOM/date/format helpers, `auth.js` owns the session and account bar, `slots.js` renders the reusable time grid, `booking.js` runs the three booking steps, `appointments.js` owns the list, cancel and reschedule dialog, and `app.js` wires everything at startup.
- **Sessions:** the API already uses a Flask cookie session, so `fetch` runs with the default same-origin credentials. The frontend only calls `/api/auth/me` to learn the current user; it never stores a token.
- **Rendering safety:** all dynamic text is inserted with `textContent` or `document.createElement`. No user-supplied value is concatenated into `innerHTML`, so a service name or note cannot inject markup.
- **Currency:** prices are shown as US dollars with two decimals (`$50.00`). The data model has no currency column, so this is a display convention; changing it means changing one formatter in `ui.js`.
- **Dates and times:** the API sends local wall-clock values (`YYYY-MM-DD`, `HH:MM`). The frontend parses those parts manually instead of passing the date string to `new Date(...)`, which would treat it as UTC and shift the day in negative time zones.
- **Client-side rules are convenience only:** the date input is limited to the 60-day horizon and customer cancel/reschedule buttons disappear inside the 2-hour cutoff. The server re-checks both; the UI simply avoids offering actions that would fail. Server error messages are shown verbatim.
- **Guest browsing:** anyone can browse services, providers and slots. Choosing a time while logged out shows "Sign in to book this time" and focuses the login form; the selection is kept so the customer can book after signing in.
- **Booking refresh:** after a successful booking the frontend re-fetches slots for the same day and broadcasts an `appointments:changed` event, so the newly taken time disappears without a page reload.
- **"Find next available day":** when a day is empty, the button requests one day at a time for the next 14 days, starting tomorrow, and stops at the first day with a free slot. Fourteen days keeps the number of requests small; the date picker still reaches the full 60-day horizon.
- **Reschedule dialog:** a native `<dialog>` reuses the same slot renderer as the booking page. The current appointment's own time still appears in the list because the server validates the move while ignoring that appointment; choosing it is harmless.
- **No provider/admin screens in this phase:** the staff flows remain available through the JSON API, which is what the spec's phase 7 asks for (customer-facing pages). A staff UI can be layered on the same endpoints later without backend changes.
- **Empty and error states:** every list has a loading, empty and failure message, so a stopped API shows an explanation instead of a blank card.

## Phase 8 — staff dashboard and reports

- **Admin-only reports:** every `/api/reports/*` endpoint is guarded with `role_required("admin")`. Anonymous callers get 401 and signed-in customers or providers get 403, so a report URL cannot leak other people's bookings.
- **Date range:** `from` and `to` are inclusive and filter on the appointment date, not on `created_at`, because the business asks "what happened on these days". Missing bounds default to the last `REPORT_DEFAULT_DAYS` (30) days including today.
- **Range limits:** the range is capped at `REPORT_MAX_RANGE_DAYS` (366) days, and an inverted range returns 400. Without a cap a single request could scan the whole table and build a huge CSV.
- **Revenue definition:** revenue is the sum of the service price for `completed` appointments only. Booked rows are not income yet and cancelled rows never were. Prices live on the service row, so a later price change changes historical revenue; storing a price snapshot per appointment would be the fix if that matters.
- **No-show rate:** `no_show / (completed + no_show)`. Cancellations are excluded because they were never a missed visit, and including them would make the rate depend on how quickly customers cancel.
- **Response shape:** one `/summary` call returns `totals`, `by_service`, `by_provider` and `by_day` together, so the dashboard needs a single round trip and cannot show cards and tables from different moments.
- **Day breakdown:** only dates with appointments are returned. The frontend does not draw a chart, so empty days would only add noise.
- **Detail report and CSV:** `/api/reports/appointments` and `/api/reports/export` reuse the same filter builder, so a downloaded CSV always matches what the screen showed. The CSV carries `price` and a separate `revenue` column that is zero for anything that is not `completed`.
- **CSV construction:** built with the stdlib `csv` module into a `StringIO` buffer and sent with `Content-Disposition: attachment`. The writer handles quoting, so a customer name containing a comma or quote cannot break the file.
- **`provider_id` on the session user:** `GET /api/auth/me` now includes `provider_id` for provider accounts, so the UI can open that provider's availability without an extra lookup endpoint. It is not included for customers or admins, and every provider-owned route still checks ownership.
- **Staff navigation is role-driven:** `Manage` shows only for admins and `Availability` for admins and providers. The buttons are hidden by `data-roles`, and `navigate()` refuses the view anyway, because hiding a button is not a permission check.
- **Editing forms are one form per entity:** clicking *Edit* loads the row into the same form used for creation and swaps the heading and button text. That keeps one code path for save and one place for validation errors.
- **Service deactivation replaces deletion:** there is no delete endpoint, so the UI toggles `active` instead. Inactive services stay linked to their history but disappear from the booking page.
- **Provider edits send `service_ids` wholesale:** the API replaces the whole set, so the checkbox group is rendered from the current links on every edit rather than merged.
- **Availability is a window editor:** each row is weekday plus start and end time, submitted as `PUT /api/providers/{id}/availability`. A 409 answer carries `conflicts`, and the UI lists the stranded bookings in the message so the admin knows what to move first.
- **Providers edit only their own hours:** the provider selector is hidden for the provider role and the id comes from the session, matching the ownership check on the endpoint.
- **Time off uses the existing routes:** adding and removing days off calls `POST` and `DELETE /api/providers/{id}/time-off`. A date with bookings still returns 409, and that message is shown as-is.
- **Marking visits complete:** providers and admins get *Complete* and *No-show* buttons on booked rows, but only once the start time has passed. That mirrors the server rule; the server still refuses a premature change with 400.
- **No extra dependencies:** the dashboard, tab switching, tables and CSV download use plain DOM APIs and stdlib Python. Nothing beyond `flask` and `pytest` was added.

## Phase 9 — documentation

- **Separate files per report section:** `docs/README.md` is the index; `modules.md`, `database.md`, `dfd.md`, `api.md` and `workflows.md` each stand alone, so a section can be handed in without the rest.
- **Diagrams are duplicated:** Mermaid for anything that renders markdown, plus a plain-text version underneath. A report printed or pasted into Word cannot rely on Mermaid support.
- **The DFD has no slot table:** available times are computed on demand from availability, time off and booked rows. Storing them would need a cache and an invalidation rule that this system does not need.
- **The ER notes explain the trade-offs**, not just the columns: why one `users` table serves three roles, why prices live on services, and why services are deactivated instead of deleted.
- **API table is test-enforced:** `tests/test_phase9.py` normalises every rule in Flask's URL map (`<int:id>` becomes `{id}`) and requires the path to appear in `docs/api.md`. Adding a route without documenting it fails the suite.
- **Documented behaviours come from the code**, including the non-obvious ones: business rejections on `/api/slots` return an empty list rather than an error, and the partial unique index is what frees a slot on cancellation.

## Open items decided in later phases

Pagination limits for very large appointment lists are not implemented; the report range cap and the 60-day booking horizon keep the current queries bounded.

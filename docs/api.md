# HTTP API

Base URL: `http://127.0.0.1:5000`. Every endpoint under `/api` speaks JSON.
The frontend is served from the same origin, so the session cookie is sent
automatically.

## Conventions

- **Request bodies** are JSON objects. A missing or non-object body returns
  `400`.
- **Errors** always look like `{"error": "human readable message"}`.
- **Dates** are `YYYY-MM-DD`; **times** are `HH:MM` on a 24-hour clock.
  Both are local wall-clock values.
- **Auth** uses a signed session cookie. `public` means anyone; `login` means
  any signed-in role; `customer`, `provider`, `admin` mean that role only.
  Endpoints that say `owner` also allow the provider who owns the record.

| Status | Meaning here |
|---|---|
| 200 | OK |
| 201 | Created |
| 400 | Malformed input or a rejected business rule |
| 401 | Not signed in |
| 403 | Signed in, wrong role or not the owner |
| 404 | Record or route does not exist |
| 409 | Conflict: slot taken, duplicate email, terminal status, stranded bookings, busy database |

## Authentication

| Method | Path | Auth | Body / query | Returns |
|---|---|---|---|---|
| POST | `/api/auth/register` | public | `name`, `email`, `password`, optional `phone` | 201 user object; 409 duplicate email; 400 validation |
| POST | `/api/auth/login` | public | `email`, `password` | 200 user object; 401 bad credentials; 429 too many failures |
| POST | `/api/auth/logout` | login | — | 200 `{"ok": true}` |
| GET | `/api/auth/me` | public | — | 200 user object, or JSON `null` when signed out |

The user object is `{id, name, email, phone, role, active}`. Provider
accounts also carry `provider_id`, which the staff screen needs to open that
provider's schedule. `password_hash` is never returned.

```http
POST /api/auth/login
Content-Type: application/json

{"email": "alice@example.com", "password": "provider123"}
```

### Session rules

- The cookie is `HttpOnly` and `SameSite=Lax`, valid for
  `SESSION_LIFETIME_HOURS` (8 by default). The server also stores `issued_at`
  and rejects an older cookie, because a client controls its own clock.
- The user is re-read on every request, so a deactivated account is signed out
  immediately.
- Five failed logins from one IP within five minutes lock that IP out.

## Services

| Method | Path | Auth | Body / query | Returns |
|---|---|---|---|---|
| GET | `/api/services` | public | `include_inactive=1` (admins only) | 200 array of services |
| POST | `/api/services` | admin | `name`, `duration_min`, `price`, optional `description`, `active` | 201 service; 409 duplicate name |
| PUT | `/api/services/{service_id}` | admin | any of the creation fields | 200 updated service; 404 unknown id |

A service is `{id, name, description, duration_min, price, active}`.
`duration_min` must be a positive multiple of `SLOT_STEP_MIN` (30). `price`
must be zero or more.

## Providers

| Method | Path | Auth | Body / query | Returns |
|---|---|---|---|---|
| GET | `/api/providers` | public | `service_id`, `include_inactive=1` (admins only) | 200 array of providers |
| POST | `/api/providers` | admin | `name`, `email`, `password`, optional `phone`, `specialization`, `service_ids` | 201 provider; 409 duplicate email |
| PUT | `/api/providers/{provider_id}` | admin | any of `specialization`, `active`, `service_ids` | 200 updated provider |
| GET | `/api/providers/{provider_id}/availability` | admin or owner | — | 200 `{windows, time_off}` |
| PUT | `/api/providers/{provider_id}/availability` | admin or owner | `{"windows": [{weekday, start_time, end_time}]}` | 200 `{windows}`; 409 with `conflicts` when bookings would be stranded |
| POST | `/api/providers/{provider_id}/time-off` | admin or owner | `date`, optional `reason` | 201 day-off row; 409 with `conflicts` when that date has bookings |
| DELETE | `/api/providers/{provider_id}/time-off/{date_value}` | admin or owner | — | 200 message; 404 unknown date |

Public provider objects are `{id, name, specialization, active, service_ids}`.
Admins additionally see `email` and `phone`.

`weekday` is `0` (Monday) to `6` (Sunday). Windows on the same weekday must
not overlap. `PUT` replaces the whole schedule in one transaction, so a
rejected request leaves the old hours untouched.

```http
PUT /api/providers/1/availability

{"windows": [
  {"weekday": 0, "start_time": "09:00", "end_time": "12:00"},
  {"weekday": 0, "start_time": "13:00", "end_time": "17:00"}
]}
```

Conflict response:

```json
{"error": "Those hours would strand upcoming bookings. Reschedule or cancel them first.",
 "conflicts": [{"id": 12, "date": "2026-11-23", "start_time": "16:30", "end_time": "17:00"}]}
```

## Slots

| Method | Path | Auth | Query | Returns |
|---|---|---|---|---|
| GET | `/api/slots` | public | `provider_id`, `service_id`, `date` | 200 `{"slots": ["09:00", ...]}` |

Malformed ids or dates return 400. Business rejections — past date, beyond
the 60-day horizon, time off, inactive provider or service, provider does not
offer the service — return `200 {"slots": []}`.

Slots start on the step grid inside an availability window and must leave
enough room for the full service duration. Times already booked are removed,
and on the current day, start times at or before now are removed.

## Appointments

| Method | Path | Auth | Body / query | Returns |
|---|---|---|---|---|
| POST | `/api/appointments` | customer | `provider_id`, `service_id`, `date`, `start_time`, optional `notes` | 201 appointment; 409 slot taken |
| GET | `/api/appointments` | login | `status`, `date`, `from`, `to` | 200 array, scoped by role |
| GET | `/api/appointments/{appointment_id}` | customer, provider or admin with access | — | 200 appointment |
| POST / PATCH | `/api/appointments/{appointment_id}/cancel` | customer, provider or admin with access | — | 200 cancelled appointment |
| POST / PATCH | `/api/appointments/{appointment_id}/reschedule` | customer, provider or admin with access | any of `date`, `start_time`, `provider_id`, `service_id` | 200 moved appointment |
| POST / PATCH | `/api/appointments/{appointment_id}/status` | provider or admin | `status` in `completed`, `cancelled`, `no_show` | 200 updated appointment |

An appointment is `{id, user_id, provider_id, service_id, date, start_time,
end_time, status, notes, created_at, updated_at, customer_name,
provider_name, service_name}`.

### Scoping

| Role | `GET /api/appointments` returns |
|---|---|
| Customer | Only their own rows. |
| Provider | Only rows where they are the assigned provider. |
| Admin | Every row. |

`from` and `to` are inclusive. An invalid status, date or inverted range
returns 400.

### Booking rules

`POST /api/appointments` re-checks all of these on the server:

1. The provider exists and is active.
2. The service exists and is active.
3. The provider offers that service.
4. The date is not in the past and within `BOOKING_HORIZON_DAYS` (60).
5. A same-day start time has not already passed.
6. The provider has no time off that day.
7. The start sits on the step grid inside one window and the service fits
   inside that window.
8. No other `booked` appointment overlaps for that provider.
9. The customer has no overlapping booking with the same provider.

Rules 8 and 9 are checked inside a `BEGIN IMMEDIATE` transaction, and rule 8
is also protected by the partial unique index, so two simultaneous requests
cannot both win.

```http
POST /api/appointments

{"provider_id": 1, "service_id": 1, "date": "2026-11-23",
 "start_time": "10:00", "notes": "First visit"}
```

### Lifecycle

```
booked ──> completed
   |  └──> no_show
   └─────> cancelled
```

`completed`, `cancelled` and `no_show` are terminal; changing them again
returns 409. `completed` and `no_show` are refused before the start time
(400). Customers may cancel or reschedule only while at least
`CANCEL_CUTOFF_HOURS` (2) remain; staff are not limited.

## Reports

| Method | Path | Auth | Query | Returns |
|---|---|---|---|---|
| GET | `/api/reports/summary` | admin | `from`, `to` | 200 totals plus three breakdowns |
| GET | `/api/reports/appointments` | admin | `from`, `to`, `status`, `provider_id`, `service_id` | 200 filtered rows |
| GET | `/api/reports/export` | admin | same as above | 200 `text/csv` attachment |

`from` and `to` are inclusive and default to the last 30 days. An inverted
range, an invalid date, or a range longer than 366 days returns 400.

Summary shape:

```json
{
  "from": "2026-11-01",
  "to": "2026-11-19",
  "totals": {
    "appointments": 12,
    "by_status": {"booked": 3, "completed": 7, "cancelled": 1, "no_show": 1},
    "completed": 7,
    "revenue": 350.0,
    "no_show_rate": 0.125,
    "unique_customers": 9
  },
  "by_service": [{"service_id": 1, "service_name": "General Consultation",
                  "appointments": 6, "completed": 4, "no_show": 1, "revenue": 200.0}],
  "by_provider": [{"provider_id": 1, "provider_name": "Alice Chen",
                   "appointments": 8, "completed": 6, "no_show": 1, "revenue": 300.0}],
  "by_day": [{"date": "2026-11-02", "appointments": 3, "completed": 2,
              "no_show": 0, "revenue": 100.0}]
}
```

Definitions:

- **Revenue** sums the service price of `completed` rows only.
- **No-show rate** is `no_show / (completed + no_show)`; cancellations are
  excluded.
- **`by_day`** lists only dates that have appointments.

The CSV columns are `id, date, start_time, end_time, status, customer,
provider, service, price, revenue`, with `revenue` zero for anything not
completed.

## Static frontend

| Method | Path | Returns |
|---|---|---|
| GET | `/` | `static/index.html` |
| GET | `/css/styles.css`, `/js/*.js` | Static assets |

An unknown `/api/*` path returns `404 {"error": "The requested resource was
not found."}` instead of an HTML error page.

## Notification CLI (not HTTP)

Queued mail is sent by a worker, not by a request:

```powershell
py -m flask --app app notifications run      # reminders then send
py -m flask --app app notifications reminders
py -m flask --app app notifications send
py -m services_logic.notifications           # same as "run"
```

With `MAIL_SERVER` empty, each message is written to the app log.

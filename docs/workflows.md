# Workflows

Sequence-level descriptions for the report. Each one names the layers it
touches so the reader can see where a rule lives.

## 1. Registration and sign-in

```
Customer        Browser              Flask route            SQLite
   |               |                     |                    |
   | fill form --->|                     |                    |
   |               | POST /api/auth/register                 |
   |               |-------------------->|                    |
   |               |                     | validate fields    |
   |               |                     | hash password      |
   |               |                     | INSERT users ----->|
   |               |                     |<--- user id -------|
   |               |                     | start session      |
   |               |<-- 201 user JSON ---|                    |
   |<-- "Welcome" -|                     |                    |
```

1. The form is submitted by `app.js`, which calls `register()` in `auth.js`.
2. `routes/auth.py` lowercases the email, validates length and pattern, and
   hashes the password with a salted, slow hash.
3. The `UNIQUE` email constraint is the final duplicate check; a violation is
   turned into a 409 rather than a 500.
4. Only after the commit does the session start, so a failed insert never
   produces a signed-in user with no row.
5. The browser stores the returned user in memory and re-renders the header.
   A refresh calls `/api/auth/me` to restore it.

## 2. Browsing and choosing a time

```
booking.js                        routes/slots.py            services_logic/slots.py
    |                                   |                            |
    | GET /api/services                 |                            |
    |---------------------------------->|                            |
    |<--- services ---------------------|                            |
    |                                   |                            |
    | GET /api/providers?service_id=1   |                            |
    |---------------------------------->|                            |
    |<--- providers --------------------|                            |
    |                                   |                            |
    | GET /api/slots?provider_id=1&service_id=1&date=...            |
    |---------------------------------->|                            |
    |                                   | generate_slots() --------->|
    |                                   |<--- ["09:00","09:30",...] -|
    |<--- {"slots": [...]} -------------|                            |
```

`generate_slots()` reads the provider, the service duration, the time-off
rows, the availability windows for that weekday and the booked rows for that
day. It walks each window in `SLOT_STEP_MIN` steps and keeps a start time
only when the whole service fits and nothing overlaps. Because it takes no
locks and writes nothing, two people can look at slots at the same time
without interfering.

The browser keeps the chosen service and provider in memory. Changing the
service clears the provider and the slots, and a request counter discards a
slow response that belongs to an older choice.

## 3. Booking

```
Customer   booking.js        routes/appointments.py            SQLite
   |           |                       |                        |
   | click --->|                       |                        |
   |           | POST /api/appointments                        |
   |           |---------------------->|                        |
   |           |                       | shape check (400)      |
   |           |                       | BEGIN IMMEDIATE ------>|
   |           |                       | re-check rules 1-9 --->|
   |           |                       | INSERT appointments -->|
   |           |                       | INSERT notification -->|
   |           |                       | COMMIT --------------->|
   |           |<-- 201 appointment ---|                        |
   |           | refresh slots         |                        |
   |<-- banner |                       |                        |
```

The shape check runs before any database work, so a malformed request cannot
open a transaction. The transaction then starts with `BEGIN IMMEDIATE`, which
takes the write lock up front; the overlap re-check and the insert therefore
cannot interleave with another booking. `busy_timeout` makes a second writer
wait instead of failing immediately, and the partial unique index is the last
guard if anything slips through.

The confirmation notification is inserted in the same transaction. The
request never waits for a mail server, so a booking succeeds even when email
is down.

## 4. Rescheduling

```
Customer picks a new slot in the dialog
    |
    v
PATCH /api/appointments/{id}/reschedule  {date, start_time}
    |
    v
BEGIN IMMEDIATE
  load the appointment (404 if missing, 403 if not theirs)
  refuse unless status = 'booked'                      -> 409
  refuse for customers inside the 2-hour cutoff        -> 409
  merge omitted fields with the current values
  re-run rules 1-9, excluding this appointment's own row
  UPDATE appointment + queue a reschedule notification
COMMIT
    |
    v
200 with the new appointment
```

Excluding the appointment's own id is what allows moving a booking by half an
hour on the same day without the old slot looking like a conflict.

## 5. Cancellation

1. Load the row and check ownership (404 / 403).
2. Refuse unless `status = 'booked'` (409).
3. For customers only, refuse inside the cutoff (409). Staff are exempt
   because the cutoff protects providers, not customers.
4. `UPDATE ... WHERE id = ? AND status = 'booked'`. If `rowcount` is zero,
   another request already changed it, so return 409 instead of claiming
   success.
5. Queue a cancellation notification and commit.

The slot becomes bookable again the moment the status changes, because the
partial unique index only covers `booked` rows.

## 6. Availability change with a conflict

```
Provider saves new hours
    |
    v
PUT /api/providers/{id}/availability
    |
    v
validate shape, time format, start < end, no same-day overlap   -> 400
    |
    v
load upcoming booked appointments for that provider
  keep only those that no longer fit inside the new windows
    |
    +-- any left? --> 409 {"error": ..., "conflicts": [...]}
    |
    +-- none? ------> DELETE old windows, INSERT new, COMMIT -> 200
```

The screen shows the returned `conflicts` list so the admin knows exactly
which bookings to move. Everything is validated before the `DELETE`, so a
refused request leaves the schedule exactly as it was.

## 7. Reminder generation and delivery

```
Scheduler (every few minutes)
      |
      v
flask notifications run
      |
      +--> generate_reminders()
      |      for each booked appointment with now < start <= now + 24h
      |      INSERT OR IGNORE a 'reminder' row
      |      (the partial unique index makes this idempotent)
      |
      +--> send_pending()
             for each pending row:
               if the appointment is no longer booked and this is not
               cancellation mail  -> mark 'failed'
               else build the message and call the sender
                    success -> mark 'sent' with sent_at
                    exception -> mark 'failed'
```

Generation and delivery are separate so a mail outage cannot lose reminders:
the rows are already queued and the next run retries nothing that already
failed, only what is still pending.

## 8. Reporting

```
Admin opens Reports
    |
    v
GET /api/reports/summary?from=...&to=...
    |
    v
_date_range(): parse, order, cap at 366 days          -> 400 on failure
    |
    v
four aggregate queries, all filtered by the same dates
    |  _summary      -> one row per status, revenue, distinct customers
    |  _by_service   -> GROUP BY service
    |  _by_provider  -> GROUP BY provider
    |  _by_day       -> GROUP BY date
    v
one JSON object with totals and three lists
    |
    v
five cards + three tables; the CSV link reuses the same filters
```

Revenue is computed in SQL with `SUM(CASE WHEN status = 'completed' THEN
price ELSE 0 END)`, so the browser never adds up money and the number on
screen matches the number in the export.

## 9. Sign-out and session expiry

- Sign-out is a `POST` that clears the server-side session; the cookie is
  removed by the response and the frontend drops its cached user.
- Expiry is checked twice: the browser stops sending an old cookie, and
  `current_user()` compares `issued_at` with the configured lifetime. A
  client that replays an old cookie is therefore treated as signed out.
- A deactivated account fails the same check on its next request, because the
  user row is re-read every time.

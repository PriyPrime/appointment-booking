-- Schema for the appointment booking system.
-- Applied by db.init_db(). Foreign keys are enabled per connection in
-- db.connect(), not here, because SQLite sets that pragma per connection.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    phone         TEXT,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'customer'
                          CHECK (role IN ('customer', 'provider', 'admin')),
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    description  TEXT,
    duration_min INTEGER NOT NULL CHECK (duration_min > 0),
    price        REAL    NOT NULL CHECK (price >= 0),
    active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS providers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT,
    specialization TEXT,
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS provider_services (
    provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE RESTRICT,
    service_id  INTEGER NOT NULL REFERENCES services(id)  ON DELETE RESTRICT,
    PRIMARY KEY (provider_id, service_id)
);

CREATE TABLE IF NOT EXISTS availability (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE RESTRICT,
    weekday     INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time  TEXT    NOT NULL,
    end_time    TEXT    NOT NULL,
    CHECK (start_time < end_time)
);

CREATE INDEX IF NOT EXISTS idx_availability_provider_weekday
    ON availability (provider_id, weekday);

CREATE TABLE IF NOT EXISTS time_off (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE RESTRICT,
    date        TEXT    NOT NULL,
    reason      TEXT,
    UNIQUE (provider_id, date)
);

CREATE TABLE IF NOT EXISTS appointments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id)     ON DELETE RESTRICT,
    provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE RESTRICT,
    service_id  INTEGER NOT NULL REFERENCES services(id)  ON DELETE RESTRICT,
    date        TEXT    NOT NULL,
    start_time  TEXT    NOT NULL,
    end_time    TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'booked'
                        CHECK (status IN ('booked', 'completed', 'cancelled', 'no_show')),
    notes       TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    CHECK (start_time < end_time)
);

CREATE INDEX IF NOT EXISTS idx_appointments_user
    ON appointments (user_id, date);

CREATE INDEX IF NOT EXISTS idx_appointments_provider
    ON appointments (provider_id, date);

-- Double-booking guard enforced by the database itself. Partial, so rows that
-- are no longer `booked` do not keep the slot blocked.
CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_unique_booked
    ON appointments (provider_id, date, start_time)
    WHERE status = 'booked';

CREATE TABLE IF NOT EXISTS notifications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL REFERENCES appointments(id) ON DELETE RESTRICT,
    type           TEXT    NOT NULL
                           CHECK (type IN ('confirmation', 'reminder', 'cancellation', 'reschedule')),
    status         TEXT    NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'sent', 'failed')),
    created_at     TEXT    NOT NULL,
    sent_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_notifications_appointment
    ON notifications (appointment_id);

-- At most one reminder per appointment, so two worker runs cannot queue two
-- emails for the same visit.
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_one_reminder
    ON notifications (appointment_id)
    WHERE type = 'reminder';

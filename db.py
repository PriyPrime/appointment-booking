"""SQLite connection helper, schema creation and seed data."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash

# Absolute path so the schema loads no matter which directory the app starts from.
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def utc_now():
    """Current UTC time as an ISO-8601 string (the storage format)."""
    # UTC avoids timezone bugs and makes string sorting equal time sorting.
    return datetime.now(timezone.utc).isoformat()


def connect(db_path=None):
    """Open a connection with foreign keys enabled and row-name access.

    SQLite disables foreign keys per connection by default, so every
    connection made here turns them on explicitly.
    """
    if db_path is None:
        db_path = current_app.config["DB_PATH"]
    conn = sqlite3.connect(db_path)
    # Rows behave like dictionaries, so code can read row["email"] instead of
    # remembering numeric column positions.
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait briefly for another writer instead of failing on the first lock.
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def get_db():
    """Request-scoped connection, closed automatically at teardown."""
    # Flask's `g` lives for one request. Reusing one connection avoids opening
    # a new SQLite file handle for every query in the same handler.
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(exc=None):
    # Registered as a teardown hook, so the file handle is released after every
    # request, including requests that raised an error.
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    """Create all tables and indexes from schema.sql. Safe to re-run."""
    conn = connect()
    try:
        # executescript runs the whole file; every statement uses IF NOT EXISTS
        # so starting the app twice does not fail.
        with open(SCHEMA_PATH, encoding="utf-8") as schema_file:
            conn.executescript(schema_file.read())
        conn.commit()
    finally:
        conn.close()


def seed():
    """Insert development data once.

    Returns True when rows were inserted and False when seeding was skipped.
    The guard is the users table: seed runs only on an empty database.
    """
    conn = connect()
    try:
        # First-run marker: any existing user means seeding already happened.
        # Without this check, every restart would duplicate the demo data.
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return False

        now = utc_now()
        conn.execute(
            "INSERT INTO users (name, email, phone, password_hash, role, active, created_at)"
            " VALUES (?, ?, ?, ?, 'admin', 1, ?)",
            (
                current_app.config["SEED_ADMIN_NAME"],
                current_app.config["SEED_ADMIN_EMAIL"].lower(),
                None,
                # Seed passwords use the same hashing as real registrations.
                generate_password_hash(current_app.config["SEED_ADMIN_PASSWORD"]),
                now,
            ),
        )

        services = [
            ("General Consultation", "30-minute appointment with a doctor.", 30, 50.0),
            ("Follow-up Visit", "Short follow-up consultation.", 30, 30.0),
            ("Haircut", "Wash, cut and style.", 30, 25.0),
            ("Colour Treatment", "Full colour treatment.", 60, 80.0),
        ]
        service_ids = {}
        for name, description, duration, price in services:
            cursor = conn.execute(
                "INSERT INTO services (name, description, duration_min, price, active)"
                " VALUES (?, ?, ?, ?, 1)",
                (name, description, duration, price),
            )
            # Keep the generated id so provider_services can link to it.
            service_ids[name] = cursor.lastrowid

        providers = [
            {
                "name": "Alice Chen",
                "email": "alice@example.com",
                "specialization": "General Practitioner",
                "services": ["General Consultation", "Follow-up Visit"],
            },
            {
                "name": "Bob Rivera",
                "email": "bob@example.com",
                "specialization": "Hair Stylist",
                "services": ["Haircut", "Colour Treatment"],
            },
        ]

        for data in providers:
            cursor = conn.execute(
                "INSERT INTO users (name, email, phone, password_hash, role, active, created_at)"
                " VALUES (?, ?, ?, ?, 'provider', 1, ?)",
                (
                    data["name"],
                    data["email"].lower(),
                    None,
                    # Hash per account: every call generates a fresh salt.
                    generate_password_hash(current_app.config["SEED_PROVIDER_PASSWORD"]),
                    now,
                ),
            )
            user_id = cursor.lastrowid
            cursor = conn.execute(
                "INSERT INTO providers (user_id, specialization, active) VALUES (?, ?, 1)",
                (user_id, data["specialization"]),
            )
            provider_id = cursor.lastrowid

            for service_name in data["services"]:
                conn.execute(
                    "INSERT INTO provider_services (provider_id, service_id) VALUES (?, ?)",
                    (provider_id, service_ids[service_name]),
                )

            # Monday-Friday, with a lunch break. Weekday 0 is Monday.
            for weekday in range(5):
                conn.execute(
                    "INSERT INTO availability (provider_id, weekday, start_time, end_time)"
                    " VALUES (?, ?, '09:00', '12:00')",
                    (provider_id, weekday),
                )
                conn.execute(
                    "INSERT INTO availability (provider_id, weekday, start_time, end_time)"
                    " VALUES (?, ?, '13:00', '17:00')",
                    (provider_id, weekday),
                )

        conn.commit()
        return True
    finally:
        conn.close()


def init_app(app):
    # Flask runs close_db after each request, even when a view raises.
    app.teardown_appcontext(close_db)

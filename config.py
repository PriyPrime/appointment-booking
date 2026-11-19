"""Application configuration.

Every tunable value lives here. Environment variables override the two values
that must differ between environments: the secret key and the database path.
Tests override the Flask config directly via ``create_app(test_config)``.
"""

import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env(name, default):
    return os.environ.get(name, default)


def _env_int(name, default):
    # A malformed environment value should fall back, not crash app startup.
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    # --- Business settings (spec section 4) ---
    SLOT_STEP_MIN = 30
    CANCEL_CUTOFF_HOURS = 2
    BOOKING_HORIZON_DAYS = 60
    SESSION_LIFETIME_HOURS = 8
    REMINDER_HOURS_BEFORE = 24
    # Reporting defaults: a month back, and a cap so one query cannot scan the
    # whole table by accident.
    REPORT_DEFAULT_DAYS = 30
    REPORT_MAX_RANGE_DAYS = 366

    # --- Security ---
    # From the environment so a real key never lands in source control.
    # The fallback exists only so a fresh clone runs without setup.
    SECRET_KEY = _env("SECRET_KEY", "dev-secret-change-before-deploying")
    # HttpOnly keeps JavaScript from reading the cookie (limits XSS damage).
    SESSION_COOKIE_HTTPONLY = True
    # Lax sends the cookie on normal navigation but not on cross-site form
    # POSTs, which blocks the simplest CSRF attacks.
    SESSION_COOKIE_SAMESITE = "Lax"
    # The browser drops the cookie after this; auth.current_user() also checks
    # the age server-side because a client can replay an expired cookie.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=SESSION_LIFETIME_HOURS)

    # --- Database ---
    # Overridable so tests and deployments can point at a different file.
    DB_PATH = _env("DB_PATH", str(BASE_DIR / "appointment_booking.db"))

    # --- Email ---
    # An empty MAIL_SERVER switches to the console transport, so development
    # needs no mail account. Everything here comes from the environment.
    MAIL_SERVER = _env("MAIL_SERVER", "")
    MAIL_PORT = _env_int("MAIL_PORT", 587)
    MAIL_USERNAME = _env("MAIL_USERNAME", "")
    MAIL_PASSWORD = _env("MAIL_PASSWORD", "")
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", True)
    MAIL_FROM = _env("MAIL_FROM", "no-reply@appointment.local")
    MAIL_TIMEOUT_SECONDS = _env_int("MAIL_TIMEOUT_SECONDS", 10)

    # --- Seed accounts (development data, documented in README) ---
    SEED_ADMIN_NAME = "Admin"
    SEED_ADMIN_EMAIL = "admin@example.com"
    SEED_ADMIN_PASSWORD = "admin12345"
    SEED_PROVIDER_PASSWORD = "provider123"

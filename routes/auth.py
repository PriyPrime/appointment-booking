"""Registration, login, logout, session helpers and permission decorators."""

import re
import sqlite3
import time
from functools import wraps

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import db as database

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FAILED_LOGIN_LIMIT = 5
FAILED_LOGIN_WINDOW_SECONDS = 300

# Fixed column list reused by several queries. Interpolating a constant is
# safe; request data must always go through ? placeholders instead.
USER_COLUMNS = "id, name, email, phone, role, active"


def error(message, status):
    """Build the standard JSON error response."""
    return jsonify({"error": message}), status


def user_json(row):
    # Turn a database row into the public shape. password_hash is deliberately
    # absent so it can never leak through the API.
    data = {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "role": row["role"],
        "active": bool(row["active"]),
    }
    if row["role"] == "provider":
        # The staff UI needs the provider row id to load availability. It is
        # not secret, and every provider-owned route already checks ownership.
        provider = database.get_db().execute(
            "SELECT id FROM providers WHERE user_id = ?", (row["id"],)
        ).fetchone()
        data["provider_id"] = provider["id"] if provider else None
    return data


def current_user():
    """Return the logged-in user row, or None if the session is not valid."""
    user_id = session.get("user_id")
    if not user_id:
        return None

    # The browser enforces its cookie expiry, but the client controls the
    # cookie. Re-checking the issue time server-side means a replayed or
    # edited cookie cannot stay valid beyond SESSION_LIFETIME_HOURS.
    issued_at = session.get("issued_at")
    lifetime_seconds = current_app.config["SESSION_LIFETIME_HOURS"] * 3600
    if not issued_at or time.time() - issued_at > lifetime_seconds:
        session.clear()
        return None

    # Re-read the user on every request so a deactivated account loses access
    # immediately instead of waiting for its session to expire.
    row = database.get_db().execute(
        f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None or not row["active"]:
        session.clear()
        return None
    return row


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return error("Please log in to continue.", 401)
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    """Allow only the given roles. Anonymous users get 401, others 403."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            # 401 means "log in"; 403 means "logged in, but not allowed".
            # Keeping them separate lets the frontend redirect to login only
            # when that would actually help.
            if user is None:
                return error("Please log in to continue.", 401)
            if user["role"] not in roles:
                return error("You do not have permission to do that.", 403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def _json_body():
    # silent=True returns None for invalid JSON instead of Flask's HTML error,
    # so every route can reply with the same JSON error shape.
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _text(value):
    # JSON fields can be numbers, lists or null. Accept only real strings so
    # later .strip() and len() calls cannot crash on the wrong type.
    return value.strip() if isinstance(value, str) else ""


def _start_session(user_id):
    session.clear()
    session["user_id"] = user_id
    session["issued_at"] = time.time()
    # permanent tells Flask to attach PERMANENT_SESSION_LIFETIME to the cookie.
    session.permanent = True


def _failed_attempts():
    # Kept on the app object, not a module global, so every test app starts
    # with an empty counter. This is in-process only; several workers would
    # need shared storage.
    return current_app.extensions.setdefault("failed_login_attempts", {})


def _too_many_failures(key):
    attempts = _failed_attempts().setdefault(key, [])
    # Forget timestamps outside the window, so the limit is a rolling window
    # rather than a permanent lockout.
    cutoff = time.time() - FAILED_LOGIN_WINDOW_SECONDS
    attempts[:] = [stamp for stamp in attempts if stamp > cutoff]
    return len(attempts) >= FAILED_LOGIN_LIMIT


def _record_failure(key):
    _failed_attempts().setdefault(key, []).append(time.time())


@bp.post("/register")
def register():
    data = _json_body()
    if data is None:
        return error("Send a JSON object with name, email and password.", 400)

    name = _text(data.get("name"))
    # Lowercase before validation and storage: the UNIQUE index then treats
    # Foo@x.com and foo@x.com as the same account.
    email = _text(data.get("email")).lower()
    password = data.get("password")
    phone = _text(data.get("phone")) or None

    if not name:
        return error("Name is required.", 400)
    if len(name) > 80:
        return error("Name must be 80 characters or fewer.", 400)
    if not EMAIL_RE.match(email):
        return error("Enter a valid email address.", 400)
    if len(email) > 254:
        return error("Email must be 254 characters or fewer.", 400)
    if not isinstance(password, str) or len(password) < 8:
        return error("Password must be at least 8 characters.", 400)
    if len(password) > 128:
        return error("Password must be 128 characters or fewer.", 400)
    if phone and len(phone) > 30:
        return error("Phone must be 30 characters or fewer.", 400)

    conn = database.get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO users (name, email, phone, password_hash, role, active, created_at)"
            " VALUES (?, ?, ?, ?, 'customer', 1, ?)",
            (
                name,
                email,
                phone,
                # Stored as a salted, slow hash; the raw password is never saved.
                generate_password_hash(password),
                database.utc_now(),
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # The database's UNIQUE email rule is the final judge. Catching it
        # here turns a duplicate (or a race between two registrations) into a
        # clean 409 instead of a 500.
        conn.rollback()
        return error("An account with that email already exists.", 409)

    # Start the session only after the INSERT committed, so we never hand out
    # a session for a user that was not actually saved.
    _start_session(cursor.lastrowid)
    row = conn.execute(
        f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return jsonify(user_json(row)), 201


@bp.post("/login")
def login():
    data = _json_body()
    if data is None:
        return error("Send a JSON object with email and password.", 400)

    email = _text(data.get("email")).lower()
    password = data.get("password")
    if not email or not isinstance(password, str) or not password:
        return error("Email and password are required.", 400)

    attempt_key = request.remote_addr or "unknown"
    # Reject before querying the database: repeated attacks should be cheap.
    if _too_many_failures(attempt_key):
        return error(
            "Too many failed attempts. Please wait a few minutes and try again.",
            401,
        )

    row = database.get_db().execute(
        "SELECT id, name, email, phone, password_hash, role, active"
        " FROM users WHERE email = ?",
        (email,),
    ).fetchone()

    password_ok = row is not None and check_password_hash(
        row["password_hash"], password
    )
    # One message for unknown email, wrong password and inactive accounts, so
    # the response cannot be used to discover which emails exist.
    if not password_ok or not row["active"]:
        _record_failure(attempt_key)
        return error("Invalid email or password.", 401)

    # A successful login clears this IP's failure history.
    _failed_attempts().pop(attempt_key, None)
    _start_session(row["id"])
    return jsonify(user_json(row)), 200


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"message": "Logged out."}), 200


@bp.get("/me")
def me():
    user = current_user()
    return jsonify(user_json(user) if user is not None else None), 200

"""Providers, their weekly availability and time off."""

import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

import db as database
from routes.auth import EMAIL_RE, current_user, error, role_required
from services_logic.slots import parse_date, to_minutes, valid_time

bp = Blueprint("providers", __name__, url_prefix="/api")

PROVIDER_SELECT = (
    "SELECT p.id, p.user_id, p.specialization, p.active,"
    " u.name, u.email, u.phone"
    " FROM providers p JOIN users u ON u.id = p.user_id"
)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _provider_json(row, service_ids, include_contact):
    data = {
        "id": row["id"],
        "name": row["name"],
        "specialization": row["specialization"],
        "active": bool(row["active"]),
        "service_ids": sorted(service_ids),
    }
    # Contact details are for admins only; the public list stays minimal.
    if include_contact:
        data["email"] = row["email"]
        data["phone"] = row["phone"]
    return data


def _service_ids_for(conn, provider_id):
    rows = conn.execute(
        "SELECT service_id FROM provider_services WHERE provider_id = ?"
        " ORDER BY service_id",
        (provider_id,),
    ).fetchall()
    return [row["service_id"] for row in rows]


def _service_map(conn):
    result = {}
    for row in conn.execute("SELECT provider_id, service_id FROM provider_services"):
        result.setdefault(row["provider_id"], []).append(row["service_id"])
    return result


def _validate_service_ids(conn, raw):
    """Return (ids, error_message) for a list of service ids."""
    if not isinstance(raw, list):
        return None, "service_ids must be a list of service ids."

    ids = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            return None, "service_ids must contain whole numbers."
        ids.append(value)
    ids = list(dict.fromkeys(ids))  # remove duplicates, keep first order

    if ids:
        # Placeholders are built from validated ints, never from raw text.
        placeholders = ",".join("?" for _ in ids)
        found = {
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM services WHERE id IN ({placeholders})", ids
            )
        }
        if any(service_id not in found for service_id in ids):
            return None, "One or more services do not exist."
    return ids, None


def _manageable_provider(provider_id):
    """Return (provider_row, error_response) for a provider or admin caller."""
    user = current_user()
    if user is None:
        return None, error("Please log in to continue.", 401)

    row = database.get_db().execute(
        PROVIDER_SELECT + " WHERE p.id = ?", (provider_id,)
    ).fetchone()
    if row is None:
        return None, error("Provider not found.", 404)

    is_owner = user["role"] == "provider" and row["user_id"] == user["id"]
    if user["role"] != "admin" and not is_owner:
        return None, error("You do not have permission to manage this provider.", 403)
    return row, None


def _upcoming_conflicts(conn, provider_id, windows):
    """Upcoming booked appointments that no longer fit inside the new hours."""
    today = datetime.now().date()
    now_hhmm = datetime.now().strftime("%H:%M")
    rows = conn.execute(
        "SELECT id, date, start_time, end_time FROM appointments"
        " WHERE provider_id = ? AND status = 'booked'",
        (provider_id,),
    ).fetchall()

    conflicts = []
    for row in rows:
        appointment_date = parse_date(row["date"])
        if appointment_date is None or appointment_date < today:
            continue
        # Past appointments, and ones already finished today, no longer need
        # to fit inside the new schedule.
        if appointment_date == today and row["end_time"] <= now_hhmm:
            continue

        weekday = appointment_date.weekday()
        start = to_minutes(row["start_time"])
        end = to_minutes(row["end_time"])
        covered = any(
            window["weekday"] == weekday
            and to_minutes(window["start_time"]) <= start
            and end <= to_minutes(window["end_time"])
            for window in windows
        )
        if not covered:
            conflicts.append(
                {
                    "id": row["id"],
                    "date": row["date"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                }
            )
    return conflicts


@bp.get("/providers")
def list_providers():
    user = current_user()
    is_admin = user is not None and user["role"] == "admin"

    service_param = request.args.get("service_id")
    service_id = None
    if service_param not in (None, ""):
        try:
            service_id = int(service_param)
        except ValueError:
            return error("service_id must be a whole number.", 400)

    include_inactive = request.args.get("include_inactive") == "1" and is_admin
    # Extension beyond the spec: admins need inactive providers so they can
    # reactivate them; everyone else only ever sees the active list.

    sql = PROVIDER_SELECT
    conditions = []
    params = []
    if not include_inactive:
        conditions.append("p.active = 1")
    if service_id is not None:
        conditions.append(
            "EXISTS (SELECT 1 FROM provider_services ps"
            " WHERE ps.provider_id = p.id AND ps.service_id = ?)"
        )
        params.append(service_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY u.name"

    conn = database.get_db()
    rows = conn.execute(sql, params).fetchall()
    service_map = _service_map(conn)
    return jsonify(
        [
            _provider_json(row, service_map.get(row["id"], []), is_admin)
            for row in rows
        ]
    ), 200


@bp.post("/providers")
@role_required("admin")
def create_provider():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON object with the provider details.", 400)

    name = _text(data.get("name"))
    email = _text(data.get("email")).lower()
    password = data.get("password")
    phone = _text(data.get("phone")) or None
    specialization = _text(data.get("specialization")) or None

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
    if specialization and len(specialization) > 100:
        return error("Specialization must be 100 characters or fewer.", 400)

    conn = database.get_db()
    service_ids, message = _validate_service_ids(conn, data.get("service_ids", []))
    if message:
        return error(message, 400)

    # The user row, provider row and service links must all succeed together,
    # so a failure anywhere rolls the whole thing back.
    try:
        cursor = conn.execute(
            "INSERT INTO users (name, email, phone, password_hash, role, active, created_at)"
            " VALUES (?, ?, ?, ?, 'provider', 1, ?)",
            (
                name,
                email,
                phone,
                generate_password_hash(password),
                database.utc_now(),
            ),
        )
        user_id = cursor.lastrowid
        cursor = conn.execute(
            "INSERT INTO providers (user_id, specialization, active) VALUES (?, ?, 1)",
            (user_id, specialization),
        )
        provider_id = cursor.lastrowid
        for service_id in service_ids:
            conn.execute(
                "INSERT INTO provider_services (provider_id, service_id) VALUES (?, ?)",
                (provider_id, service_id),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        if "users.email" in str(exc):
            return error("An account with that email already exists.", 409)
        return error("Could not create the provider because of a data conflict.", 409)

    row = conn.execute(
        PROVIDER_SELECT + " WHERE p.id = ?", (provider_id,)
    ).fetchone()
    return jsonify(_provider_json(row, service_ids, True)), 201


@bp.put("/providers/<int:provider_id>")
@role_required("admin")
def update_provider(provider_id):
    conn = database.get_db()
    row = conn.execute(
        PROVIDER_SELECT + " WHERE p.id = ?", (provider_id,)
    ).fetchone()
    if row is None:
        return error("Provider not found.", 404)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON object with the provider details.", 400)

    specialization = row["specialization"]
    if "specialization" in data:
        value = data["specialization"]
        if value is None:
            specialization = None
        elif isinstance(value, str):
            specialization = value.strip() or None
            if specialization and len(specialization) > 100:
                return error("Specialization must be 100 characters or fewer.", 400)
        else:
            return error("Specialization must be text.", 400)

    active = row["active"]
    if "active" in data:
        value = data["active"]
        if value not in (0, 1, True, False):
            return error("Active must be true or false.", 400)
        active = int(bool(value))

    service_ids = None
    if "service_ids" in data:
        service_ids, message = _validate_service_ids(conn, data["service_ids"])
        if message:
            return error(message, 400)

    try:
        conn.execute(
            "UPDATE providers SET specialization = ?, active = ? WHERE id = ?",
            (specialization, active, provider_id),
        )
        if service_ids is not None:
            conn.execute(
                "DELETE FROM provider_services WHERE provider_id = ?", (provider_id,)
            )
            for service_id in service_ids:
                conn.execute(
                    "INSERT INTO provider_services (provider_id, service_id) VALUES (?, ?)",
                    (provider_id, service_id),
                )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return error("Could not update the provider because of a data conflict.", 409)

    row = conn.execute(
        PROVIDER_SELECT + " WHERE p.id = ?", (provider_id,)
    ).fetchone()
    current_ids = (
        service_ids
        if service_ids is not None
        else _service_ids_for(conn, provider_id)
    )
    return jsonify(_provider_json(row, current_ids, True)), 200


def _window_json(row):
    return {
        "id": row["id"],
        "weekday": row["weekday"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
    }


@bp.get("/providers/<int:provider_id>/availability")
def get_availability(provider_id):
    _provider, response = _manageable_provider(provider_id)
    if response is not None:
        return response

    conn = database.get_db()
    windows = conn.execute(
        "SELECT id, weekday, start_time, end_time FROM availability"
        " WHERE provider_id = ? ORDER BY weekday, start_time",
        (provider_id,),
    ).fetchall()
    today = datetime.now().date().isoformat()
    time_off = conn.execute(
        "SELECT id, date, reason FROM time_off"
        " WHERE provider_id = ? AND date >= ? ORDER BY date",
        (provider_id, today),
    ).fetchall()
    return jsonify(
        {
            "windows": [_window_json(row) for row in windows],
            "time_off": [
                {"id": row["id"], "date": row["date"], "reason": row["reason"]}
                for row in time_off
            ],
        }
    ), 200


@bp.put("/providers/<int:provider_id>/availability")
def replace_availability(provider_id):
    _provider, response = _manageable_provider(provider_id)
    if response is not None:
        return response

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("windows"), list):
        return error("Send a JSON object with a windows list.", 400)

    windows = []
    for item in data["windows"]:
        if not isinstance(item, dict):
            return error("Each window must be an object.", 400)
        weekday = item.get("weekday")
        start = item.get("start_time")
        end = item.get("end_time")
        if isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6:
            return error(
                "Weekday must be a whole number from 0 (Monday) to 6 (Sunday).", 400
            )
        if not valid_time(start) or not valid_time(end):
            return error("Start and end times must use HH:MM (24-hour).", 400)
        if to_minutes(start) >= to_minutes(end):
            return error("Start time must be before end time.", 400)
        windows.append({"weekday": weekday, "start_time": start, "end_time": end})

    # Reject overlaps on the same weekday. Sorting first means comparing each
    # window with its neighbour is enough; back-to-back windows are allowed.
    by_weekday = {}
    for window in windows:
        by_weekday.setdefault(window["weekday"], []).append(window)
    for day_windows in by_weekday.values():
        ordered = sorted(day_windows, key=lambda w: to_minutes(w["start_time"]))
        for previous, current in zip(ordered, ordered[1:]):
            if to_minutes(current["start_time"]) < to_minutes(previous["end_time"]):
                return error("Availability windows overlap on the same weekday.", 400)

    conn = database.get_db()
    conflicts = _upcoming_conflicts(conn, provider_id, windows)
    if conflicts:
        return jsonify(
            {
                "error": "Those hours would strand upcoming bookings."
                " Reschedule or cancel them first.",
                "conflicts": conflicts,
            }
        ), 409

    # Validate everything first, then replace in one transaction so a refused
    # request leaves the old schedule untouched.
    try:
        conn.execute("DELETE FROM availability WHERE provider_id = ?", (provider_id,))
        for window in windows:
            conn.execute(
                "INSERT INTO availability (provider_id, weekday, start_time, end_time)"
                " VALUES (?, ?, ?, ?)",
                (
                    provider_id,
                    window["weekday"],
                    window["start_time"],
                    window["end_time"],
                ),
            )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return error("Could not save availability because of a data conflict.", 409)

    rows = conn.execute(
        "SELECT id, weekday, start_time, end_time FROM availability"
        " WHERE provider_id = ? ORDER BY weekday, start_time",
        (provider_id,),
    ).fetchall()
    return jsonify({"windows": [_window_json(row) for row in rows]}), 200


@bp.post("/providers/<int:provider_id>/time-off")
def add_time_off(provider_id):
    _provider, response = _manageable_provider(provider_id)
    if response is not None:
        return response

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON object with a date.", 400)

    parsed = parse_date(data.get("date"))
    if parsed is None:
        return error("Date must use YYYY-MM-DD.", 400)
    if parsed < datetime.now().date():
        return error("Time off must be today or a future date.", 400)

    reason = data.get("reason")
    if reason is not None:
        if not isinstance(reason, str):
            return error("Reason must be text.", 400)
        reason = reason.strip() or None
        if reason and len(reason) > 200:
            return error("Reason must be 200 characters or fewer.", 400)

    conn = database.get_db()
    conflicts = conn.execute(
        "SELECT id, date, start_time, end_time FROM appointments"
        " WHERE provider_id = ? AND date = ? AND status = 'booked'",
        (provider_id, data["date"]),
    ).fetchall()
    if conflicts:
        return jsonify(
            {
                "error": "There are bookings on that date."
                " Reschedule or cancel them first.",
                "conflicts": [
                    {
                        "id": row["id"],
                        "date": row["date"],
                        "start_time": row["start_time"],
                        "end_time": row["end_time"],
                    }
                    for row in conflicts
                ],
            }
        ), 409

    try:
        cursor = conn.execute(
            "INSERT INTO time_off (provider_id, date, reason) VALUES (?, ?, ?)",
            (provider_id, data["date"], reason),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return error("Time off already exists for that date.", 409)

    row = conn.execute(
        "SELECT id, date, reason FROM time_off WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return jsonify(
        {"id": row["id"], "date": row["date"], "reason": row["reason"]}
    ), 201


@bp.delete("/providers/<int:provider_id>/time-off/<date_value>")
def delete_time_off(provider_id, date_value):
    _provider, response = _manageable_provider(provider_id)
    if response is not None:
        return response

    if parse_date(date_value) is None:
        return error("Date must use YYYY-MM-DD.", 400)

    conn = database.get_db()
    cursor = conn.execute(
        "DELETE FROM time_off WHERE provider_id = ? AND date = ?",
        (provider_id, date_value),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return error("Time off not found for that date.", 404)
    return jsonify({"message": "Time off removed."}), 200

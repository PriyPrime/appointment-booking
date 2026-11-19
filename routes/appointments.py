"""Appointment booking, listing and lifecycle."""

import sqlite3
from datetime import datetime, time, timedelta

from flask import Blueprint, current_app, jsonify, request

import db as database
from routes.auth import current_user, error, login_required, role_required
from services_logic.slots import parse_date, to_hhmm, to_minutes, valid_time

bp = Blueprint("appointments", __name__, url_prefix="/api")

APPOINTMENT_SELECT = (
    "SELECT a.id, a.user_id, a.provider_id, a.service_id, a.date, a.start_time,"
    " a.end_time, a.status, a.notes, a.created_at, a.updated_at,"
    " p.user_id AS provider_user_id,"
    " u.name AS customer_name, pu.name AS provider_name, s.name AS service_name"
    " FROM appointments a"
    " JOIN users u ON u.id = a.user_id"
    " JOIN providers p ON p.id = a.provider_id"
    " JOIN users pu ON pu.id = p.user_id"
    " JOIN services s ON s.id = a.service_id"
)


def appointment_json(row):
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "provider_id": row["provider_id"],
        "service_id": row["service_id"],
        "date": row["date"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "status": row["status"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "customer_name": row["customer_name"],
        "provider_name": row["provider_name"],
        "service_name": row["service_name"],
    }


def _booking_values(data):
    """Validate the shape of the request before any database work."""
    provider_id = data.get("provider_id")
    service_id = data.get("service_id")
    if isinstance(provider_id, bool) or not isinstance(provider_id, int) or provider_id <= 0:
        return None, "provider_id must be a whole number."
    if isinstance(service_id, bool) or not isinstance(service_id, int) or service_id <= 0:
        return None, "service_id must be a whole number."

    date_value = data.get("date")
    if parse_date(date_value) is None:
        return None, "Date must use YYYY-MM-DD."

    start_time = data.get("start_time")
    if not valid_time(start_time):
        return None, "start_time must use HH:MM (24-hour)."

    notes = data.get("notes")
    if notes is not None:
        if not isinstance(notes, str):
            return None, "Notes must be text."
        notes = notes.strip() or None
        if notes and len(notes) > 500:
            return None, "Notes must be 500 characters or fewer."

    return {
        "provider_id": provider_id,
        "service_id": service_id,
        "date": date_value,
        "start_time": start_time,
        "notes": notes,
    }, None


def _check_booking_rules(conn, user_id, values, exclude_id=None):
    """Return (end_time, error_response). End time is None on failure.

    ``exclude_id`` lets a reschedule ignore the appointment being moved, so its
    current slot does not look like a clash with itself.
    """
    provider = conn.execute(
        "SELECT active FROM providers WHERE id = ?", (values["provider_id"],)
    ).fetchone()
    if provider is None:
        return None, error("Provider not found.", 404)
    if not provider["active"]:
        return None, error("That provider is not accepting bookings.", 400)

    service = conn.execute(
        "SELECT active, duration_min FROM services WHERE id = ?",
        (values["service_id"],),
    ).fetchone()
    if service is None:
        return None, error("Service not found.", 404)
    if not service["active"]:
        return None, error("That service is not available for booking.", 400)

    offered = conn.execute(
        "SELECT 1 FROM provider_services WHERE provider_id = ? AND service_id = ?",
        (values["provider_id"], values["service_id"]),
    ).fetchone()
    if offered is None:
        return None, error("That provider does not offer this service.", 400)

    requested = parse_date(values["date"])
    now = datetime.now()
    today = now.date()
    if requested < today:
        return None, error("That date is in the past.", 400)
    horizon = current_app.config["BOOKING_HORIZON_DAYS"]
    if requested > today + timedelta(days=horizon):
        return None, error("That date is too far ahead to book.", 400)
    if requested == today and values["start_time"] <= now.strftime("%H:%M"):
        return None, error("That time has already passed.", 400)

    if conn.execute(
        "SELECT 1 FROM time_off WHERE provider_id = ? AND date = ?",
        (values["provider_id"], values["date"]),
    ).fetchone():
        return None, error("The provider is not available on that date.", 400)

    step = current_app.config["SLOT_STEP_MIN"]
    start = to_minutes(values["start_time"])
    end = start + service["duration_min"]
    windows = conn.execute(
        "SELECT start_time, end_time FROM availability"
        " WHERE provider_id = ? AND weekday = ?",
        (values["provider_id"], requested.weekday()),
    ).fetchall()
    # The requested start must sit on the slot grid inside one window. This
    # rejects arbitrary times such as 09:15 when the grid starts at 09:00.
    fits = any(
        to_minutes(window["start_time"]) <= start
        and end <= to_minutes(window["end_time"])
        and (start - to_minutes(window["start_time"])) % step == 0
        for window in windows
    )
    if not fits:
        return None, error(
            "That start time is outside the provider's working hours.", 400
        )

    end_time = to_hhmm(end)

    # Half-open overlap: existing.start < new.end AND new.start < existing.end.
    clash_sql = (
        "SELECT 1 FROM appointments WHERE provider_id = ? AND date = ?"
        " AND status = 'booked' AND start_time < ? AND ? < end_time"
    )
    clash_params = [
        values["provider_id"],
        values["date"],
        end_time,
        values["start_time"],
    ]
    own_clash_sql = (
        "SELECT 1 FROM appointments WHERE user_id = ? AND provider_id = ?"
        " AND date = ? AND status = 'booked'"
        " AND start_time < ? AND ? < end_time"
    )
    own_clash_params = [
        user_id,
        values["provider_id"],
        values["date"],
        end_time,
        values["start_time"],
    ]
    if exclude_id is not None:
        clash_sql += " AND id != ?"
        clash_params.append(exclude_id)
        own_clash_sql += " AND id != ?"
        own_clash_params.append(exclude_id)

    clash = conn.execute(clash_sql, clash_params).fetchone()
    if clash is not None:
        return None, error("That time was just taken. Pick another slot.", 409)

    own_clash = conn.execute(own_clash_sql, own_clash_params).fetchone()
    if own_clash is not None:
        return None, error(
            "You already have an appointment with this provider at that time.", 409
        )

    return end_time, None


@bp.post("/appointments")
@role_required("customer")
def create_appointment():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON object with the booking details.", 400)

    values, message = _booking_values(data)
    if message:
        return error(message, 400)

    user = current_user()
    conn = database.get_db()
    try:
        # BEGIN IMMEDIATE takes the write lock before the overlap check, so
        # two customers cannot both pass the check and then insert. The second
        # request waits (busy_timeout) and then sees the first booking.
        conn.execute("BEGIN IMMEDIATE")
        end_time, failure = _check_booking_rules(conn, user["id"], values)
        if failure is not None:
            conn.rollback()
            return failure

        now = database.utc_now()
        cursor = conn.execute(
            "INSERT INTO appointments (user_id, provider_id, service_id, date,"
            " start_time, end_time, status, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'booked', ?, ?, ?)",
            (
                user["id"],
                values["provider_id"],
                values["service_id"],
                values["date"],
                values["start_time"],
                end_time,
                values["notes"],
                now,
                now,
            ),
        )
        appointment_id = cursor.lastrowid
        # Queued here; phase 6 adds the transport that actually sends it.
        conn.execute(
            "INSERT INTO notifications (appointment_id, type, status, created_at)"
            " VALUES (?, 'confirmation', 'pending', ?)",
            (appointment_id, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return error("That time was just taken. Pick another slot.", 409)
    except sqlite3.OperationalError:
        conn.rollback()
        return error("The system was busy. Please try again.", 409)

    row = conn.execute(
        APPOINTMENT_SELECT + " WHERE a.id = ?", (appointment_id,)
    ).fetchone()
    return jsonify(appointment_json(row)), 201


def _load_appointment(conn, appointment_id):
    return conn.execute(
        APPOINTMENT_SELECT + " WHERE a.id = ?", (appointment_id,)
    ).fetchone()


def _can_manage(user, row):
    """True when this user may see or change this appointment."""
    if user["role"] == "admin":
        return True
    if user["role"] == "provider":
        return row["provider_user_id"] == user["id"]
    return row["user_id"] == user["id"]


def _start_datetime(row):
    """Combine the stored local date and time into one comparable datetime."""
    return datetime.combine(
        parse_date(row["date"]), time.fromisoformat(row["start_time"])
    )


def _customer_is_too_late(row):
    """True when the cancellation cutoff has passed for a customer."""
    cutoff = _start_datetime(row) - timedelta(
        hours=current_app.config["CANCEL_CUTOFF_HOURS"]
    )
    return datetime.now() > cutoff


def _queue_notification(conn, appointment_id, kind, now):
    # Notifications are queued, never sent inline, so a slow mail server can
    # never hold a booking or cancellation transaction open.
    conn.execute(
        "INSERT INTO notifications (appointment_id, type, status, created_at)"
        " VALUES (?, ?, 'pending', ?)",
        (appointment_id, kind, now),
    )


@bp.get("/appointments")
@login_required
def list_appointments():
    user = current_user()
    conditions = []
    params = []

    # Each role sees a different slice of the same table.
    if user["role"] == "customer":
        conditions.append("a.user_id = ?")
        params.append(user["id"])
    elif user["role"] == "provider":
        conditions.append("p.user_id = ?")
        params.append(user["id"])

    status = request.args.get("status")
    if status not in (None, ""):
        if status not in ("booked", "completed", "cancelled", "no_show"):
            return error("Unknown appointment status.", 400)
        conditions.append("a.status = ?")
        params.append(status)

    date_value = request.args.get("date")
    if date_value not in (None, ""):
        if parse_date(date_value) is None:
            return error("date must use YYYY-MM-DD.", 400)
        conditions.append("a.date = ?")
        params.append(date_value)

    start_date = request.args.get("from")
    end_date = request.args.get("to")
    for name, value in (("from", start_date), ("to", end_date)):
        if value not in (None, "") and parse_date(value) is None:
            return error(f"{name} must use YYYY-MM-DD.", 400)
    if start_date not in (None, "") and end_date not in (None, ""):
        if parse_date(start_date) > parse_date(end_date):
            return error("from must be on or before to.", 400)
    if start_date not in (None, ""):
        conditions.append("a.date >= ?")
        params.append(start_date)
    if end_date not in (None, ""):
        conditions.append("a.date <= ?")
        params.append(end_date)

    sql = APPOINTMENT_SELECT
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY a.date, a.start_time, a.id"
    rows = database.get_db().execute(sql, params).fetchall()
    return jsonify([appointment_json(row) for row in rows]), 200


@bp.get("/appointments/<int:appointment_id>")
@login_required
def get_appointment(appointment_id):
    user = current_user()
    row = _load_appointment(database.get_db(), appointment_id)
    if row is None:
        return error("Appointment not found.", 404)
    if not _can_manage(user, row):
        return error("You do not have permission to view that appointment.", 403)
    return jsonify(appointment_json(row)), 200


@bp.post("/appointments/<int:appointment_id>/cancel")
@bp.patch("/appointments/<int:appointment_id>/cancel")
@login_required
def cancel_appointment(appointment_id):
    user = current_user()
    conn = database.get_db()
    row = _load_appointment(conn, appointment_id)
    if row is None:
        return error("Appointment not found.", 404)
    if not _can_manage(user, row):
        return error("You do not have permission to cancel that appointment.", 403)
    if row["status"] != "booked":
        return error("Only a booked appointment can be cancelled.", 409)
    # Staff may cancel late; the cutoff protects providers from last-minute
    # customer no-shows, not the other way around.
    if user["role"] == "customer" and _customer_is_too_late(row):
        return error(
            f"Appointments can only be cancelled more than"
            f" {current_app.config['CANCEL_CUTOFF_HOURS']} hours in advance.",
            409,
        )

    try:
        now = database.utc_now()
        # The status guard makes a simultaneous second cancel update nothing.
        cursor = conn.execute(
            "UPDATE appointments SET status = 'cancelled', updated_at = ?"
            " WHERE id = ? AND status = 'booked'",
            (now, appointment_id),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return error("Only a booked appointment can be cancelled.", 409)
        _queue_notification(conn, appointment_id, "cancellation", now)
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
        return error("The system was busy. Please try again.", 409)

    return jsonify(appointment_json(_load_appointment(conn, appointment_id))), 200


@bp.post("/appointments/<int:appointment_id>/reschedule")
@bp.patch("/appointments/<int:appointment_id>/reschedule")
@login_required
def reschedule_appointment(appointment_id):
    user = current_user()
    conn = database.get_db()
    row = _load_appointment(conn, appointment_id)
    if row is None:
        return error("Appointment not found.", 404)
    if not _can_manage(user, row):
        return error("You do not have permission to reschedule that appointment.", 403)
    if row["status"] != "booked":
        return error("Only a booked appointment can be rescheduled.", 409)
    if user["role"] == "customer" and _customer_is_too_late(row):
        return error(
            f"Appointments can only be rescheduled more than"
            f" {current_app.config['CANCEL_CUTOFF_HOURS']} hours in advance.",
            409,
        )

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON object with the new date and start_time.", 400)

    # Missing fields keep their current values, so a provider can move just the
    # time or swap the service without repeating everything.
    values, message = _booking_values(
        {
            "provider_id": data.get("provider_id", row["provider_id"]),
            "service_id": data.get("service_id", row["service_id"]),
            "date": data.get("date", row["date"]),
            "start_time": data.get("start_time", row["start_time"]),
            "notes": row["notes"],
        }
    )
    if message:
        return error(message, 400)

    try:
        # Same lock as creation: a reschedule must not race with a new booking
        # for the target slot.
        conn.execute("BEGIN IMMEDIATE")
        current = _load_appointment(conn, appointment_id)
        if current is None or current["status"] != "booked":
            conn.rollback()
            return error("Only a booked appointment can be rescheduled.", 409)

        end_time, failure = _check_booking_rules(
            conn, current["user_id"], values, exclude_id=appointment_id
        )
        if failure is not None:
            conn.rollback()
            return failure

        now = database.utc_now()
        conn.execute(
            "UPDATE appointments SET provider_id = ?, service_id = ?, date = ?,"
            " start_time = ?, end_time = ?, updated_at = ? WHERE id = ?",
            (
                values["provider_id"],
                values["service_id"],
                values["date"],
                values["start_time"],
                end_time,
                now,
                appointment_id,
            ),
        )
        _queue_notification(conn, appointment_id, "reschedule", now)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return error("That time was just taken. Pick another slot.", 409)
    except sqlite3.OperationalError:
        conn.rollback()
        return error("The system was busy. Please try again.", 409)

    return jsonify(appointment_json(_load_appointment(conn, appointment_id))), 200


@bp.post("/appointments/<int:appointment_id>/status")
@bp.patch("/appointments/<int:appointment_id>/status")
@login_required
def change_appointment_status(appointment_id):
    user = current_user()
    if user["role"] not in ("provider", "admin"):
        return error("Only providers and admins can change appointment status.", 403)

    conn = database.get_db()
    row = _load_appointment(conn, appointment_id)
    if row is None:
        return error("Appointment not found.", 404)
    if not _can_manage(user, row):
        return error("You do not have permission to update that appointment.", 403)
    if row["status"] != "booked":
        return error("Only a booked appointment can change status.", 409)

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("status") not in (
        "completed",
        "cancelled",
        "no_show",
    ):
        return error("Status must be completed, cancelled or no_show.", 400)
    new_status = data["status"]

    # A visit cannot have happened before it started, so only cancellation is
    # allowed ahead of the appointment time.
    if new_status != "cancelled" and datetime.now() < _start_datetime(row):
        return error("That appointment has not started yet.", 400)

    try:
        now = database.utc_now()
        cursor = conn.execute(
            "UPDATE appointments SET status = ?, updated_at = ?"
            " WHERE id = ? AND status = 'booked'",
            (new_status, now, appointment_id),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return error("Only a booked appointment can change status.", 409)
        if new_status == "cancelled":
            _queue_notification(conn, appointment_id, "cancellation", now)
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
        return error("The system was busy. Please try again.", 409)

    return jsonify(appointment_json(_load_appointment(conn, appointment_id))), 200

"""Admin service management and the public service list."""

import math
import sqlite3

from flask import Blueprint, current_app, jsonify, request

import db as database
from routes.auth import current_user, error, role_required

bp = Blueprint("services", __name__, url_prefix="/api")

SERVICE_COLUMNS = "id, name, description, duration_min, price, active"


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _service_json(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "duration_min": row["duration_min"],
        "price": row["price"],
        "active": bool(row["active"]),
    }


def _validate(data, existing=None):
    """Return (values, error_message). Missing keys fall back to `existing`."""
    name = data.get("name", existing["name"] if existing else None)
    description = data.get(
        "description", existing["description"] if existing else None
    )
    duration = data.get(
        "duration_min", existing["duration_min"] if existing else None
    )
    price = data.get("price", existing["price"] if existing else None)
    active = data.get("active", existing["active"] if existing else 1)

    if not isinstance(name, str) or not name.strip():
        return None, "Name is required."
    name = name.strip()
    if len(name) > 100:
        return None, "Name must be 100 characters or fewer."

    if description is not None:
        if not isinstance(description, str):
            return None, "Description must be text."
        description = description.strip() or None
        if description and len(description) > 1000:
            return None, "Description must be 1000 characters or fewer."

    # bool is a subclass of int, so reject it explicitly.
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        return None, "Duration must be a positive whole number of minutes."
    step = current_app.config["SLOT_STEP_MIN"]
    if duration % step:
        return None, f"Duration must be a multiple of {step} minutes."

    if (
        isinstance(price, bool)
        or not isinstance(price, (int, float))
        or not math.isfinite(price)
        or price < 0
    ):
        return None, "Price must be zero or more."
    price = round(float(price), 2)

    if active not in (0, 1, True, False):
        return None, "Active must be true or false."

    return {
        "name": name,
        "description": description,
        "duration_min": duration,
        "price": price,
        "active": int(bool(active)),
    }, None


@bp.get("/services")
def list_services():
    user = current_user()
    # include_inactive is an admin convenience; silently ignore it for others
    # rather than turning a browse request into an error.
    include_inactive = (
        request.args.get("include_inactive") == "1"
        and user is not None
        and user["role"] == "admin"
    )
    sql = f"SELECT {SERVICE_COLUMNS} FROM services"
    if not include_inactive:
        sql += " WHERE active = 1"
    sql += " ORDER BY name"
    rows = database.get_db().execute(sql).fetchall()
    return jsonify([_service_json(row) for row in rows]), 200


@bp.post("/services")
@role_required("admin")
def create_service():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON object with the service details.", 400)

    values, message = _validate(data)
    if message:
        return error(message, 400)

    conn = database.get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO services (name, description, duration_min, price, active)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                values["name"],
                values["description"],
                values["duration_min"],
                values["price"],
                values["active"],
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # The UNIQUE name constraint is the final judge, including races.
        conn.rollback()
        return error("A service with that name already exists.", 409)

    row = conn.execute(
        f"SELECT {SERVICE_COLUMNS} FROM services WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify(_service_json(row)), 201


@bp.put("/services/<int:service_id>")
@role_required("admin")
def update_service(service_id):
    conn = database.get_db()
    existing = conn.execute(
        f"SELECT {SERVICE_COLUMNS} FROM services WHERE id = ?", (service_id,)
    ).fetchone()
    if existing is None:
        return error("Service not found.", 404)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON object with the service details.", 400)

    values, message = _validate(data, existing)
    if message:
        return error(message, 400)

    try:
        conn.execute(
            "UPDATE services SET name = ?, description = ?, duration_min = ?,"
            " price = ?, active = ? WHERE id = ?",
            (
                values["name"],
                values["description"],
                values["duration_min"],
                values["price"],
                values["active"],
                service_id,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return error("A service with that name already exists.", 409)

    row = conn.execute(
        f"SELECT {SERVICE_COLUMNS} FROM services WHERE id = ?", (service_id,)
    ).fetchone()
    return jsonify(_service_json(row)), 200

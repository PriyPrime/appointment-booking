"""Public slot lookup."""

from flask import Blueprint, current_app, jsonify, request

import db as database
from routes.auth import error
from services_logic.slots import generate_slots, parse_date

bp = Blueprint("slots", __name__, url_prefix="/api")


def _int_param(name):
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@bp.get("/slots")
def list_slots():
    provider_id = _int_param("provider_id")
    service_id = _int_param("service_id")
    if provider_id is None or service_id is None:
        return error("provider_id and service_id must be whole numbers.", 400)

    date_value = request.args.get("date")
    if parse_date(date_value) is None:
        return error("date must use YYYY-MM-DD.", 400)

    # Business rejections (inactive provider, time off, past date, ...) come
    # back as an empty slot list; only malformed input is a 400.
    slots = generate_slots(
        database.get_db(),
        provider_id,
        service_id,
        date_value,
        step_min=current_app.config["SLOT_STEP_MIN"],
        horizon_days=current_app.config["BOOKING_HORIZON_DAYS"],
    )
    return jsonify({"slots": slots}), 200

"""Admin reports: totals, revenue breakdowns and a CSV export.

Every report is read-only and admin-only. The date range is inclusive and
filters on the appointment date, not on when the row was created.
"""

import csv
import io
from datetime import date, timedelta

from flask import Blueprint, current_app, jsonify, request

import db as database
from routes.appointments import APPOINTMENT_SELECT, appointment_json
from routes.auth import error, role_required
from services_logic.slots import parse_date

bp = Blueprint("reports", __name__, url_prefix="/api/reports")

STATUSES = ("booked", "completed", "cancelled", "no_show")


def _money(value):
    # SQLite may return an int, a float or None depending on the aggregate.
    return round(float(value or 0), 2)


def _rate(part, whole):
    return round(part / whole, 4) if whole else 0.0


def _date_range():
    """Return (from_string, to_string, error_response).

    Missing bounds default to the last REPORT_DEFAULT_DAYS days including
    today. Both bounds are validated before any SQL runs.
    """
    today = date.today()
    default_from = today - timedelta(
        days=current_app.config["REPORT_DEFAULT_DAYS"] - 1
    )
    from_value = request.args.get("from") or default_from.isoformat()
    to_value = request.args.get("to") or today.isoformat()

    start = parse_date(from_value)
    if start is None:
        return None, None, error("from must use YYYY-MM-DD.", 400)
    end = parse_date(to_value)
    if end is None:
        return None, None, error("to must use YYYY-MM-DD.", 400)
    if start > end:
        return None, None, error("from must be on or before to.", 400)

    # +1 because both ends are included, so 1 Jan to 2 Jan is two days.
    span = (end - start).days + 1
    limit = current_app.config["REPORT_MAX_RANGE_DAYS"]
    if span > limit:
        return None, None, error(f"Report range must be {limit} days or fewer.", 400)

    return from_value, to_value, None


def _summary(conn, start, end):
    rows = conn.execute(
        "SELECT a.status, COUNT(*) AS appointments,"
        " COALESCE(SUM(CASE WHEN a.status = 'completed' THEN s.price ELSE 0 END), 0)"
        " AS revenue"
        " FROM appointments a JOIN services s ON s.id = a.service_id"
        " WHERE a.date >= ? AND a.date <= ?"
        " GROUP BY a.status",
        (start, end),
    ).fetchall()

    by_status = {status: 0 for status in STATUSES}
    revenue = 0.0
    for row in rows:
        by_status[row["status"]] = row["appointments"]
        revenue += row["revenue"]

    total = sum(by_status.values())
    # Cancellations were never visits, so they are left out of the miss rate.
    attended_or_missed = by_status["completed"] + by_status["no_show"]
    unique_customers = conn.execute(
        "SELECT COUNT(DISTINCT a.user_id) FROM appointments a"
        " WHERE a.date >= ? AND a.date <= ?",
        (start, end),
    ).fetchone()[0]

    return {
        "appointments": total,
        "by_status": by_status,
        "completed": by_status["completed"],
        "revenue": _money(revenue),
        "no_show_rate": _rate(by_status["no_show"], attended_or_missed),
        "unique_customers": unique_customers,
    }


def _by_service(conn, start, end):
    rows = conn.execute(
        "SELECT s.id AS service_id, s.name AS service_name,"
        " COUNT(*) AS appointments,"
        " SUM(CASE WHEN a.status = 'completed' THEN 1 ELSE 0 END) AS completed,"
        " SUM(CASE WHEN a.status = 'no_show' THEN 1 ELSE 0 END) AS no_show,"
        " COALESCE(SUM(CASE WHEN a.status = 'completed' THEN s.price ELSE 0 END), 0)"
        " AS revenue"
        " FROM appointments a JOIN services s ON s.id = a.service_id"
        " WHERE a.date >= ? AND a.date <= ?"
        " GROUP BY s.id, s.name"
        " ORDER BY revenue DESC, s.name",
        (start, end),
    ).fetchall()
    return [
        {
            "service_id": row["service_id"],
            "service_name": row["service_name"],
            "appointments": row["appointments"],
            "completed": row["completed"],
            "no_show": row["no_show"],
            "revenue": _money(row["revenue"]),
        }
        for row in rows
    ]


def _by_provider(conn, start, end):
    rows = conn.execute(
        "SELECT p.id AS provider_id, u.name AS provider_name,"
        " COUNT(*) AS appointments,"
        " SUM(CASE WHEN a.status = 'completed' THEN 1 ELSE 0 END) AS completed,"
        " SUM(CASE WHEN a.status = 'no_show' THEN 1 ELSE 0 END) AS no_show,"
        " COALESCE(SUM(CASE WHEN a.status = 'completed' THEN s.price ELSE 0 END), 0)"
        " AS revenue"
        " FROM appointments a"
        " JOIN providers p ON p.id = a.provider_id"
        " JOIN users u ON u.id = p.user_id"
        " JOIN services s ON s.id = a.service_id"
        " WHERE a.date >= ? AND a.date <= ?"
        " GROUP BY p.id, u.name"
        " ORDER BY revenue DESC, u.name",
        (start, end),
    ).fetchall()
    return [
        {
            "provider_id": row["provider_id"],
            "provider_name": row["provider_name"],
            "appointments": row["appointments"],
            "completed": row["completed"],
            "no_show": row["no_show"],
            "revenue": _money(row["revenue"]),
        }
        for row in rows
    ]


def _by_day(conn, start, end):
    # Only days with appointments appear. The frontend can skip zero rows
    # instead of drawing a long flat line.
    rows = conn.execute(
        "SELECT a.date, COUNT(*) AS appointments,"
        " SUM(CASE WHEN a.status = 'completed' THEN 1 ELSE 0 END) AS completed,"
        " SUM(CASE WHEN a.status = 'no_show' THEN 1 ELSE 0 END) AS no_show,"
        " COALESCE(SUM(CASE WHEN a.status = 'completed' THEN s.price ELSE 0 END), 0)"
        " AS revenue"
        " FROM appointments a JOIN services s ON s.id = a.service_id"
        " WHERE a.date >= ? AND a.date <= ?"
        " GROUP BY a.date"
        " ORDER BY a.date",
        (start, end),
    ).fetchall()
    return [
        {
            "date": row["date"],
            "appointments": row["appointments"],
            "completed": row["completed"],
            "no_show": row["no_show"],
            "revenue": _money(row["revenue"]),
        }
        for row in rows
    ]


@bp.get("/summary")
@role_required("admin")
def report_summary():
    start, end, failure = _date_range()
    if failure is not None:
        return failure

    conn = database.get_db()
    return jsonify(
        {
            "from": start,
            "to": end,
            "totals": _summary(conn, start, end),
            "by_service": _by_service(conn, start, end),
            "by_provider": _by_provider(conn, start, end),
            "by_day": _by_day(conn, start, end),
        }
    ), 200


def _appointment_filters():
    """Return (where_sql, params, start, end, error_response) for a report."""
    start, end, failure = _date_range()
    if failure is not None:
        return None, None, None, None, failure

    conditions = ["a.date >= ?", "a.date <= ?"]
    params = [start, end]

    status = request.args.get("status")
    if status not in (None, ""):
        if status not in STATUSES:
            return None, None, None, None, error("Unknown appointment status.", 400)
        conditions.append("a.status = ?")
        params.append(status)

    for name, column in (
        ("provider_id", "a.provider_id"),
        ("service_id", "a.service_id"),
    ):
        raw = request.args.get(name)
        if raw not in (None, ""):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return None, None, None, None, error(
                    f"{name} must be a whole number.", 400
                )
            if value <= 0:
                return None, None, None, None, error(
                    f"{name} must be a whole number.", 400
                )
            conditions.append(f"{column} = ?")
            params.append(value)

    return " WHERE " + " AND ".join(conditions), params, start, end, None


def _appointment_rows(conn, where, params):
    sql = (
        APPOINTMENT_SELECT
        + where
        + " ORDER BY a.date, a.start_time, a.id"
    )
    return conn.execute(sql, params).fetchall()


@bp.get("/appointments")
@role_required("admin")
def report_appointments():
    where, params, start, end, failure = _appointment_filters()
    if failure is not None:
        return failure

    rows = _appointment_rows(database.get_db(), where, params)
    return jsonify(
        {
            "from": start,
            "to": end,
            "count": len(rows),
            "appointments": [appointment_json(row) for row in rows],
        }
    ), 200


@bp.get("/export")
@role_required("admin")
def export_appointments():
    """Download the filtered appointments as CSV."""
    where, params, start, end, failure = _appointment_filters()
    if failure is not None:
        return failure

    conn = database.get_db()
    rows = _appointment_rows(conn, where, params)
    # Prices live on the service row, so join once more for the export rather
    # than returning a second query per appointment.
    prices = {
        row["id"]: row["price"]
        for row in conn.execute("SELECT id, price FROM services")
    }

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "id",
            "date",
            "start_time",
            "end_time",
            "status",
            "customer",
            "provider",
            "service",
            "price",
            "revenue",
        ]
    )
    for row in rows:
        price = prices.get(row["service_id"], 0)
        writer.writerow(
            [
                row["id"],
                row["date"],
                row["start_time"],
                row["end_time"],
                row["status"],
                row["customer_name"],
                row["provider_name"],
                row["service_name"],
                f"{price:.2f}",
                # Only a completed visit earns money.
                f"{price if row['status'] == 'completed' else 0:.2f}",
            ]
        )

    response = current_app.response_class(
        buffer.getvalue(), mimetype="text/csv"
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="appointments-{start}-to-{end}.csv"'
    )
    return response

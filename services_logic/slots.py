"""Time parsing and overlap helpers shared by routes and (later) slots.

Slot generation itself arrives in phase 4. Keeping the parsing and overlap
rules here now means validation and generation use one definition.
"""

import re
from datetime import date, datetime, timedelta

# Strict patterns: storage format is always YYYY-MM-DD and HH:MM (24-hour).
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def parse_date(value):
    """Return a date for a strict YYYY-MM-DD string, else None."""
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:  # catches impossible dates such as 2026-02-30
        return None


def valid_time(value):
    """True when value is a strict HH:MM string."""
    return isinstance(value, str) and bool(TIME_RE.match(value))


def to_minutes(value):
    """Convert HH:MM to minutes since midnight, for easy comparison."""
    hours, mins = value.split(":")
    return int(hours) * 60 + int(mins)


def to_hhmm(total):
    """Convert minutes since midnight back to HH:MM."""
    return f"{total // 60:02d}:{total % 60:02d}"


def overlaps(start_a, end_a, start_b, end_b):
    """True when two half-open ranges [start, end) overlap.

    Back-to-back ranges (one ends exactly when the next starts) do not overlap.
    All arguments are minutes since midnight.
    """
    return start_a < end_b and start_b < end_a


def generate_slots(
    conn,
    provider_id,
    service_id,
    date_value,
    step_min,
    horizon_days,
    today=None,
    now_hhmm=None,
):
    """Return bookable start times (HH:MM) for one provider, service and date.

    ``today`` and ``now_hhmm`` can be injected so tests do not depend on the
    real clock. Every business rejection returns an empty list, matching the
    spec's "reject" behaviour for slot lookup.
    """
    requested = parse_date(date_value)
    if requested is None:
        return []

    today = today if today is not None else date.today()
    if requested < today:
        return []
    if requested > today + timedelta(days=horizon_days):
        return []

    provider = conn.execute(
        "SELECT active FROM providers WHERE id = ?", (provider_id,)
    ).fetchone()
    if provider is None or not provider["active"]:
        return []

    service = conn.execute(
        "SELECT active, duration_min FROM services WHERE id = ?", (service_id,)
    ).fetchone()
    if service is None or not service["active"]:
        return []

    offered = conn.execute(
        "SELECT 1 FROM provider_services WHERE provider_id = ? AND service_id = ?",
        (provider_id, service_id),
    ).fetchone()
    if offered is None:
        return []

    # A whole time-off day has no slots at all.
    if conn.execute(
        "SELECT 1 FROM time_off WHERE provider_id = ? AND date = ?",
        (provider_id, date_value),
    ).fetchone():
        return []

    windows = conn.execute(
        "SELECT start_time, end_time FROM availability"
        " WHERE provider_id = ? AND weekday = ? ORDER BY start_time",
        (provider_id, requested.weekday()),
    ).fetchall()
    booked = conn.execute(
        "SELECT start_time, end_time FROM appointments"
        " WHERE provider_id = ? AND date = ? AND status = 'booked'",
        (provider_id, date_value),
    ).fetchall()
    booked_ranges = [
        (to_minutes(row["start_time"]), to_minutes(row["end_time"])) for row in booked
    ]

    if now_hhmm is None:
        now_hhmm = datetime.now().strftime("%H:%M")
    duration = service["duration_min"]

    slots = set()
    for window in windows:
        window_start = to_minutes(window["start_time"])
        window_end = to_minutes(window["end_time"])
        start = window_start
        # Step from the window start; a candidate must finish inside the window.
        while start + duration <= window_end:
            end = start + duration
            if not any(
                overlaps(start, end, booked_start, booked_end)
                for booked_start, booked_end in booked_ranges
            ):
                # Starting exactly "now" is too late, matching the spec's
                # "at or before the current time" rule.
                if not (requested == today and to_hhmm(start) <= now_hhmm):
                    slots.add(start)
            start += step_min

    return [to_hhmm(value) for value in sorted(slots)]

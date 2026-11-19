"""Queue reminders and deliver appointment emails.

Notifications are written to the database by the appointment routes. This
module turns them into emails and records whether delivery worked. Delivery is
kept out of the request handlers so a slow mail server can never delay a
booking.
"""

import logging
import smtplib
from datetime import datetime, time, timedelta
from email.message import EmailMessage

from flask import current_app

import db as database
from services_logic.slots import parse_date

logger = logging.getLogger(__name__)

SUBJECTS = {
    "confirmation": "Appointment confirmed: {service_name}",
    "reminder": "Reminder: {service_name} on {date}",
    "cancellation": "Appointment cancelled: {service_name}",
    "reschedule": "Appointment rescheduled: {service_name}",
}

OPENERS = {
    "confirmation": "Your appointment is confirmed.",
    "reminder": "This is a reminder about your upcoming appointment.",
    "cancellation": "Your appointment has been cancelled.",
    "reschedule": "Your appointment has been moved to the time below.",
}


def _appointment_datetime(row):
    """Stored date and time are local wall-clock values, so compare naively."""
    return datetime.combine(
        parse_date(row["date"]), time.fromisoformat(row["start_time"])
    )


def generate_reminders(conn, now=None):
    """Queue one reminder per booked appointment inside the reminder window.

    Returns the number of reminders created. The unique reminder index and
    ``INSERT OR IGNORE`` make repeated runs safe, including two workers running
    at the same time.
    """
    now = now if now is not None else datetime.now()
    window_end = now + timedelta(hours=current_app.config["REMINDER_HOURS_BEFORE"])
    created_at = database.utc_now()

    rows = conn.execute(
        "SELECT a.id, a.date, a.start_time FROM appointments a"
        " WHERE a.status = 'booked'"
        " AND NOT EXISTS (SELECT 1 FROM notifications n"
        "                 WHERE n.appointment_id = a.id AND n.type = 'reminder')"
    ).fetchall()

    created = 0
    for row in rows:
        start = _appointment_datetime(row)
        # Only appointments still in the future and close enough get a
        # reminder; old rows are left alone rather than emailed late.
        if not (now < start <= window_end):
            continue
        cursor = conn.execute(
            "INSERT OR IGNORE INTO notifications"
            " (appointment_id, type, status, created_at)"
            " VALUES (?, 'reminder', 'pending', ?)",
            (row["id"], created_at),
        )
        created += cursor.rowcount
    conn.commit()
    return created


def _pending_rows(conn, limit):
    return conn.execute(
        "SELECT n.id, n.type, n.appointment_id,"
        " a.date, a.start_time, a.end_time, a.notes, a.status AS appointment_status,"
        " cu.name AS customer_name, cu.email AS customer_email,"
        " pu.name AS provider_name, s.name AS service_name"
        " FROM notifications n"
        " JOIN appointments a ON a.id = n.appointment_id"
        " JOIN users cu ON cu.id = a.user_id"
        " JOIN providers p ON p.id = a.provider_id"
        " JOIN users pu ON pu.id = p.user_id"
        " JOIN services s ON s.id = a.service_id"
        " WHERE n.status = 'pending'"
        " ORDER BY n.id LIMIT ?",
        (limit,),
    ).fetchall()


def build_message(row):
    """Turn one notification row into a plain-text email."""
    subject = SUBJECTS[row["type"]].format(
        service_name=row["service_name"], date=row["date"]
    )
    lines = [
        f"Hi {row['customer_name']},",
        "",
        OPENERS[row["type"]],
        "",
        f"Service:  {row['service_name']}",
        f"Provider: {row['provider_name']}",
        f"Date:     {row['date']}",
        f"Time:     {row['start_time']} - {row['end_time']}",
        f"Reference: appointment #{row['appointment_id']}",
    ]
    if row["notes"]:
        lines += ["", f"Notes: {row['notes']}"]
    lines += ["", "Thank you."]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = current_app.config["MAIL_FROM"]
    message["To"] = row["customer_email"]
    message.set_content("\n".join(lines))
    return message


def send_email(message):
    """Deliver one message through SMTP, or print it when SMTP is not set up."""
    server = current_app.config.get("MAIL_SERVER")
    if not server:
        # Console delivery keeps a fresh clone usable without a mail account.
        # print() instead of logging because the default CLI log level would
        # hide INFO messages, and this is meant to be read by a developer.
        print(
            f"\n--- Email to {message['To']} ---\n"
            f"Subject: {message['Subject']}\n\n"
            f"{message.get_content()}"
            "--- end of email ---"
        )
        return

    with smtplib.SMTP(
        server,
        current_app.config["MAIL_PORT"],
        timeout=current_app.config["MAIL_TIMEOUT_SECONDS"],
    ) as smtp:
        smtp.ehlo()
        if current_app.config["MAIL_USE_TLS"]:
            smtp.starttls()
            smtp.ehlo()
        username = current_app.config.get("MAIL_USERNAME")
        if username:
            smtp.login(username, current_app.config.get("MAIL_PASSWORD", ""))
        smtp.send_message(message)


def send_pending(conn, sender=None, limit=100):
    """Send queued notifications and record the result.

    ``sender`` is injectable so tests can observe delivery without a network.
    A notification that raises is marked ``failed`` and is not retried
    automatically; that keeps a permanently bad address from blocking the
    queue on every run.
    """
    sender = sender or send_email
    rows = _pending_rows(conn, limit)
    sent = 0
    failed = 0

    for row in rows:
        # A confirmation or reminder queued before a cancellation is stale.
        # The cancellation email still goes out, but nothing else does.
        if row["appointment_status"] != "booked" and row["type"] != "cancellation":
            conn.execute(
                "UPDATE notifications SET status = 'failed'"
                " WHERE id = ? AND status = 'pending'",
                (row["id"],),
            )
            conn.commit()
            failed += 1
            continue

        try:
            sender(build_message(row))
        except Exception:  # noqa: BLE001 - one bad email must not stop the queue
            logger.exception("Could not send notification %s", row["id"])
            conn.execute(
                "UPDATE notifications SET status = 'failed'"
                " WHERE id = ? AND status = 'pending'",
                (row["id"],),
            )
            conn.commit()
            failed += 1
        else:
            conn.execute(
                "UPDATE notifications SET status = 'sent', sent_at = ?"
                " WHERE id = ? AND status = 'pending'",
                (database.utc_now(), row["id"]),
            )
            conn.commit()
            sent += 1

    return {"sent": sent, "failed": failed}


def run_all(conn, sender=None, limit=100, now=None):
    """Queue due reminders, then deliver everything that is pending."""
    reminders = generate_reminders(conn, now=now)
    result = send_pending(conn, sender=sender, limit=limit)
    return {"reminders": reminders, **result}


def _worker_connection():
    """Prepare the schema and open a connection for a CLI run.

    init_db and seed are both idempotent, so the CLI works on a fresh clone
    without requiring the web server to have been started first.
    """
    database.init_db()
    database.seed()
    return database.connect()


def register_cli(app):
    """Expose the worker as ``flask notifications ...`` commands."""

    @app.cli.group("notifications")
    def notifications_group():
        """Generate reminders and send queued appointment emails."""

    @notifications_group.command("reminders")
    def reminders_command():
        """Queue reminders for appointments inside the reminder window."""
        conn = _worker_connection()
        try:
            count = generate_reminders(conn)
        finally:
            conn.close()
        print(f"Queued {count} reminder(s).")

    @notifications_group.command("send")
    def send_command():
        """Send every pending notification once."""
        conn = _worker_connection()
        try:
            result = send_pending(conn)
        finally:
            conn.close()
        print(f"Sent {result['sent']} notification(s); {result['failed']} failed.")

    @notifications_group.command("run")
    def run_command():
        """Queue reminders and send them in one pass."""
        conn = _worker_connection()
        try:
            result = run_all(conn)
        finally:
            conn.close()
        print(
            f"Queued {result['reminders']} reminder(s); "
            f"sent {result['sent']}; {result['failed']} failed."
        )


def main():
    """Allow ``py -m services_logic.notifications`` without the Flask CLI."""
    from app import app

    with app.app_context():
        conn = _worker_connection()
        try:
            result = run_all(conn)
        finally:
            conn.close()
    print(
        f"Queued {result['reminders']} reminder(s); "
        f"sent {result['sent']}; {result['failed']} failed."
    )


if __name__ == "__main__":
    main()

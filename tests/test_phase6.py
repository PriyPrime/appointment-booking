"""Phase 6 acceptance checks: reminders and notification delivery."""

from datetime import datetime, time, timedelta

import db as database
from services_logic import notifications

# Fixed clock keeps the reminder window deterministic.
NOW = datetime(2030, 1, 7, 9, 0)


def _add_customer(app, email="notify@example.com"):
    with app.app_context():
        conn = database.connect()
        try:
            user_id = conn.execute(
                "INSERT INTO users (name, email, password_hash, role, active, created_at)"
                " VALUES ('Notify Customer', ?, 'x', 'customer', 1, ?)",
                (email, database.utc_now()),
            ).lastrowid
            conn.commit()
            return user_id
        finally:
            conn.close()


def _add_appointment(app, user_id, start, minutes=30, status="booked"):
    end = start + timedelta(minutes=minutes)
    if end.date() != start.date():
        # Keep both times on one date; 22:30-23:00 is still due soon.
        start = datetime.combine(start.date(), time(22, 30))
        end = datetime.combine(start.date(), time(23, 0))
    with app.app_context():
        conn = database.connect()
        try:
            now = database.utc_now()
            appointment_id = conn.execute(
                "INSERT INTO appointments (user_id, provider_id, service_id, date,"
                " start_time, end_time, status, created_at, updated_at)"
                " VALUES (?, 1, 1, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    start.date().isoformat(),
                    start.strftime("%H:%M"),
                    end.strftime("%H:%M"),
                    status,
                    now,
                    now,
                ),
            ).lastrowid
            conn.commit()
            return appointment_id
        finally:
            conn.close()


def _add_notification(app, appointment_id, kind="confirmation", status="pending"):
    with app.app_context():
        conn = database.connect()
        try:
            notification_id = conn.execute(
                "INSERT INTO notifications (appointment_id, type, status, created_at)"
                " VALUES (?, ?, ?, ?)",
                (appointment_id, kind, status, database.utc_now()),
            ).lastrowid
            conn.commit()
            return notification_id
        finally:
            conn.close()


def _notifications(app, appointment_id):
    with app.app_context():
        conn = database.connect()
        try:
            return conn.execute(
                "SELECT id, type, status, sent_at FROM notifications"
                " WHERE appointment_id = ? ORDER BY id",
                (appointment_id,),
            ).fetchall()
        finally:
            conn.close()


def _run(app, operation):
    with app.app_context():
        conn = database.connect()
        try:
            return operation(conn)
        finally:
            conn.close()


def test_generate_reminders_is_due_and_idempotent(app):
    user_id = _add_customer(app)
    due = _add_appointment(app, user_id, NOW + timedelta(hours=2))
    _add_appointment(app, user_id, NOW + timedelta(days=3))
    _add_appointment(app, user_id, NOW - timedelta(hours=2), status="cancelled")

    assert _run(app, lambda conn: notifications.generate_reminders(conn, now=NOW)) == 1
    assert _run(app, lambda conn: notifications.generate_reminders(conn, now=NOW)) == 0

    rows = _notifications(app, due)
    assert [row["type"] for row in rows] == ["reminder"]
    assert rows[0]["status"] == "pending"


def test_reminder_window_includes_exactly_twenty_four_hours(app):
    user_id = _add_customer(app)
    boundary = _add_appointment(app, user_id, NOW + timedelta(hours=24))
    too_far = _add_appointment(app, user_id, NOW + timedelta(hours=24, minutes=1))

    assert _run(app, lambda conn: notifications.generate_reminders(conn, now=NOW)) == 1
    assert [row["type"] for row in _notifications(app, boundary)] == ["reminder"]
    assert _notifications(app, too_far) == []


def test_send_pending_marks_sent_and_builds_a_useful_email(app):
    user_id = _add_customer(app)
    appointment_id = _add_appointment(app, user_id, NOW + timedelta(hours=2))
    notification_id = _add_notification(app, appointment_id)
    sent_messages = []

    result = _run(
        app,
        lambda conn: notifications.send_pending(conn, sender=sent_messages.append),
    )
    assert result == {"sent": 1, "failed": 0}
    assert len(sent_messages) == 1

    message = sent_messages[0]
    assert message["To"] == "notify@example.com"
    assert message["Subject"] == "Appointment confirmed: General Consultation"
    body = message.get_content()
    assert "Alice Chen" in body
    assert "2030-01-07" in body
    assert "11:00 - 11:30" in body

    row = _notifications(app, appointment_id)[0]
    assert row["id"] == notification_id
    assert row["status"] == "sent"
    assert row["sent_at"] is not None


def test_failed_delivery_is_recorded_and_not_retried(app):
    user_id = _add_customer(app)
    appointment_id = _add_appointment(app, user_id, NOW + timedelta(hours=2))
    _add_notification(app, appointment_id)

    def broken_sender(_message):
        raise OSError("mail server down")

    result = _run(
        app, lambda conn: notifications.send_pending(conn, sender=broken_sender)
    )
    assert result == {"sent": 0, "failed": 1}

    row = _notifications(app, appointment_id)[0]
    assert row["status"] == "failed"
    assert row["sent_at"] is None

    # Failed rows are not picked up again, so a bad address cannot block the
    # queue on every run.
    assert _run(app, lambda conn: notifications.send_pending(conn, sender=broken_sender)) == {
        "sent": 0,
        "failed": 0,
    }


def test_cancelled_appointment_skips_stale_confirmation(app):
    user_id = _add_customer(app)
    appointment_id = _add_appointment(
        app, user_id, NOW + timedelta(hours=2), status="cancelled"
    )
    _add_notification(app, appointment_id, kind="confirmation")
    _add_notification(app, appointment_id, kind="cancellation")
    sent_messages = []

    result = _run(
        app,
        lambda conn: notifications.send_pending(conn, sender=sent_messages.append),
    )
    assert result == {"sent": 1, "failed": 1}
    assert len(sent_messages) == 1
    assert sent_messages[0]["Subject"].startswith("Appointment cancelled")


def test_every_notification_type_has_its_own_subject(app):
    user_id = _add_customer(app)
    appointment_id = _add_appointment(app, user_id, NOW + timedelta(hours=2))
    for kind in ("confirmation", "cancellation", "reschedule", "reminder"):
        _add_notification(app, appointment_id, kind=kind)

    sent_messages = []
    result = _run(
        app,
        lambda conn: notifications.send_pending(conn, sender=sent_messages.append),
    )
    assert result == {"sent": 4, "failed": 0}

    subjects = [message["Subject"] for message in sent_messages]
    assert any(subject.startswith("Appointment confirmed") for subject in subjects)
    assert any(subject.startswith("Appointment cancelled") for subject in subjects)
    assert any(subject.startswith("Appointment rescheduled") for subject in subjects)
    assert any(subject.startswith("Reminder:") for subject in subjects)


def test_run_all_queues_then_sends_everything(app):
    user_id = _add_customer(app)
    appointment_id = _add_appointment(app, user_id, NOW + timedelta(hours=2))
    _add_notification(app, appointment_id)

    sent_messages = []
    result = _run(
        app,
        lambda conn: notifications.run_all(
            conn, sender=sent_messages.append, now=NOW
        ),
    )
    assert result == {"reminders": 1, "sent": 2, "failed": 0}

    types = {row["type"] for row in _notifications(app, appointment_id)}
    assert types == {"confirmation", "reminder"}
    assert all(row["status"] == "sent" for row in _notifications(app, appointment_id))


def test_console_transport_is_used_without_smtp(app, capsys):
    user_id = _add_customer(app)
    appointment_id = _add_appointment(app, user_id, NOW + timedelta(hours=2))
    _add_notification(app, appointment_id)

    with app.app_context():
        app.config["MAIL_SERVER"] = ""
        conn = database.connect()
        try:
            notifications.send_pending(conn)
        finally:
            conn.close()

    printed = capsys.readouterr().out
    assert "Subject: Appointment confirmed" in printed
    assert _notifications(app, appointment_id)[0]["status"] == "sent"


def test_cli_run_command_generates_and_sends(app):
    user_id = _add_customer(app)
    appointment_id = _add_appointment(app, user_id, datetime.now() + timedelta(hours=2))
    app.config["MAIL_SERVER"] = ""

    result = app.test_cli_runner().invoke(args=["notifications", "run"])
    assert result.exit_code == 0, result.output
    assert "Queued 1 reminder(s)" in result.output
    assert "sent 1" in result.output
    assert {row["status"] for row in _notifications(app, appointment_id)} == {"sent"}

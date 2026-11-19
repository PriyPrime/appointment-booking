"""Phase 5 acceptance checks: listing, detail, cancel, reschedule and status."""

from datetime import date, datetime, time, timedelta

import db as database

ADMIN = {"email": "admin@example.com", "password": "admin12345"}
ALICE = {"email": "alice@example.com", "password": "provider123"}
BOB = {"email": "bob@example.com", "password": "provider123"}


def _next_weekday(target=0):
    """Next real date whose weekday() is target, always in the future."""
    today = date.today()
    days = (target - today.weekday()) % 7
    if days == 0:
        days = 7
    return today + timedelta(days=days)


MONDAY = _next_weekday(0)
TUESDAY = _next_weekday(1)


def _login(client, credentials):
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200
    return client


def _customer(app, email):
    client = app.test_client()
    response = client.post(
        "/api/auth/register",
        json={"name": "Customer", "email": email, "password": "secret123"},
    )
    assert response.status_code == 201
    return client


def _me(client):
    return client.get("/api/auth/me").get_json()


def _book(client, on_date=MONDAY, start="09:00", provider_id=1, service_id=1):
    response = client.post(
        "/api/appointments",
        json={
            "provider_id": provider_id,
            "service_id": service_id,
            "date": on_date.isoformat(),
            "start_time": start,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _insert_appointment(
    app,
    user_id,
    on_date,
    start,
    end,
    provider_id=1,
    service_id=1,
    status="booked",
):
    """Insert directly so tests can create past or soon appointments."""
    with app.app_context():
        conn = database.connect()
        try:
            now = database.utc_now()
            appointment_id = conn.execute(
                "INSERT INTO appointments (user_id, provider_id, service_id, date,"
                " start_time, end_time, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    provider_id,
                    service_id,
                    on_date.isoformat(),
                    start,
                    end,
                    status,
                    now,
                    now,
                ),
            ).lastrowid
            conn.commit()
            return appointment_id
        finally:
            conn.close()


def _soon_appointment(app, user_id):
    """A booked appointment about one hour from now, inside the cutoff."""
    start = datetime.now() + timedelta(hours=1)
    end = start + timedelta(minutes=30)
    if start.date() != end.date():
        # Keep both times on one date; 23:30-23:59 is still inside the cutoff.
        start = datetime.combine(start.date(), time(23, 30))
        end = datetime.combine(start.date(), time(23, 59))
    return _insert_appointment(
        app, user_id, start.date(), start.strftime("%H:%M"), end.strftime("%H:%M")
    )


def _past_appointment(app, user_id):
    start = datetime.now() - timedelta(hours=1)
    end = start + timedelta(minutes=30)
    if start.date() != end.date():
        # Around midnight the range can cross into today; keep it on one date.
        start = datetime.combine(end.date(), time(0, 0))
        end = datetime.combine(end.date(), time(0, 30))
    return _insert_appointment(
        app, user_id, start.date(), start.strftime("%H:%M"), end.strftime("%H:%M")
    )


def _notification_types(app, appointment_id):
    with app.app_context():
        conn = database.connect()
        try:
            rows = conn.execute(
                "SELECT type FROM notifications WHERE appointment_id = ? ORDER BY id",
                (appointment_id,),
            ).fetchall()
            return [row["type"] for row in rows]
        finally:
            conn.close()


def _slots(app, on_date=MONDAY):
    public = app.test_client()
    response = public.get(
        "/api/slots",
        query_string={
            "provider_id": 1,
            "service_id": 1,
            "date": on_date.isoformat(),
        },
    )
    assert response.status_code == 200
    return response.get_json()["slots"]


# --- Listing and detail ----------------------------------------------------


def test_customers_see_only_their_own_appointments(app):
    first = _customer(app, "list-one@example.com")
    second = _customer(app, "list-two@example.com")
    first_booking = _book(first, MONDAY, "09:00")
    _book(second, MONDAY, "10:00")

    listed = first.get("/api/appointments").get_json()
    assert [row["id"] for row in listed] == [first_booking["id"]]

    second_booking = second.get("/api/appointments").get_json()
    assert len(second_booking) == 1
    assert second_booking[0]["id"] != first_booking["id"]
    assert first.get("/api/appointments").status_code == 200


def test_providers_see_their_own_schedule_and_admins_see_everything(app):
    customer = _customer(app, "scope@example.com")
    alice_booking = _book(customer, MONDAY, "09:00")
    bob_booking = _book(customer, MONDAY, "09:00", provider_id=2, service_id=4)

    alice = _login(app.test_client(), ALICE)
    alice_ids = [row["id"] for row in alice.get("/api/appointments").get_json()]
    assert alice_booking["id"] in alice_ids
    assert bob_booking["id"] not in alice_ids

    bob = _login(app.test_client(), BOB)
    bob_ids = [row["id"] for row in bob.get("/api/appointments").get_json()]
    assert bob_ids == [bob_booking["id"]]

    admin = _login(app.test_client(), ADMIN)
    admin_ids = {row["id"] for row in admin.get("/api/appointments").get_json()}
    assert {alice_booking["id"], bob_booking["id"]} <= admin_ids


def test_list_filters_validate_and_narrow_results(app):
    customer = _customer(app, "filters@example.com")
    monday_booking = _book(customer, MONDAY, "09:00")
    _book(customer, TUESDAY, "09:00")

    by_date = customer.get(
        "/api/appointments", query_string={"date": MONDAY.isoformat()}
    ).get_json()
    assert [row["id"] for row in by_date] == [monday_booking["id"]]

    in_range = customer.get(
        "/api/appointments",
        query_string={"from": MONDAY.isoformat(), "to": MONDAY.isoformat()},
    ).get_json()
    assert [row["id"] for row in in_range] == [monday_booking["id"]]

    booked = customer.get(
        "/api/appointments", query_string={"status": "booked"}
    ).get_json()
    assert len(booked) == 2
    assert (
        customer.get("/api/appointments", query_string={"status": "cancelled"})
        .get_json()
        == []
    )

    assert (
        customer.get("/api/appointments", query_string={"status": "nope"}).status_code
        == 400
    )
    assert (
        customer.get("/api/appointments", query_string={"date": "soon"}).status_code
        == 400
    )
    assert (
        customer.get(
            "/api/appointments",
            query_string={"from": TUESDAY.isoformat(), "to": MONDAY.isoformat()},
        ).status_code
        == 400
    )


def test_detail_respects_ownership_and_role(app):
    owner = _customer(app, "detail-owner@example.com")
    stranger = _customer(app, "detail-stranger@example.com")
    booking = _book(owner, MONDAY, "09:00")
    path = f"/api/appointments/{booking['id']}"

    assert owner.get(path).status_code == 200
    assert booking["provider_name"] == "Alice Chen"
    assert stranger.get(path).status_code == 403

    alice = _login(app.test_client(), ALICE)
    assert alice.get(path).status_code == 200

    admin = _login(app.test_client(), ADMIN)
    assert admin.get(path).status_code == 200

    assert owner.get("/api/appointments/9999").status_code == 404
    assert app.test_client().get(path).status_code == 401


# --- Cancellation ----------------------------------------------------------


def test_customer_cancels_and_the_slot_opens_again(app):
    customer = _customer(app, "cancel@example.com")
    booking = _book(customer, MONDAY, "09:00")
    assert "09:00" not in _slots(app)

    response = customer.patch(f"/api/appointments/{booking['id']}/cancel")
    assert response.status_code == 200
    assert response.get_json()["status"] == "cancelled"

    # A cancelled row is no longer `booked`, so the partial unique index and
    # the slot generator both stop counting it.
    assert "09:00" in _slots(app)
    assert "cancellation" in _notification_types(app, booking["id"])

    assert (
        customer.patch(f"/api/appointments/{booking['id']}/cancel").status_code == 409
    )


def test_cancel_permissions(app):
    owner = _customer(app, "cancel-owner@example.com")
    stranger = _customer(app, "cancel-stranger@example.com")
    booking = _book(owner, MONDAY, "09:00")
    path = f"/api/appointments/{booking['id']}/cancel"

    assert stranger.patch(path).status_code == 403
    assert app.test_client().patch(path).status_code == 401
    assert owner.patch(path).status_code == 200


def test_customer_cannot_cancel_inside_the_cutoff_but_a_provider_can(app):
    customer = _customer(app, "cutoff@example.com")
    appointment_id = _soon_appointment(app, _me(customer)["id"])

    refused = customer.patch(f"/api/appointments/{appointment_id}/cancel")
    assert refused.status_code == 409
    assert "hours in advance" in refused.get_json()["error"]

    alice = _login(app.test_client(), ALICE)
    assert (
        alice.patch(f"/api/appointments/{appointment_id}/cancel").status_code == 200
    )


def test_cancel_rejects_a_finished_appointment(app):
    customer = _customer(app, "finished@example.com")
    appointment_id = _past_appointment(app, _me(customer)["id"])
    with app.app_context():
        conn = database.connect()
        try:
            conn.execute(
                "UPDATE appointments SET status = 'completed' WHERE id = ?",
                (appointment_id,),
            )
            conn.commit()
        finally:
            conn.close()

    alice = _login(app.test_client(), ALICE)
    assert (
        alice.patch(f"/api/appointments/{appointment_id}/cancel").status_code == 409
    )


# --- Rescheduling ----------------------------------------------------------


def test_reschedule_moves_the_booking_and_queues_a_notification(app):
    customer = _customer(app, "move@example.com")
    booking = _book(customer, MONDAY, "09:00")

    response = customer.patch(
        f"/api/appointments/{booking['id']}/reschedule",
        json={"date": MONDAY.isoformat(), "start_time": "10:00"},
    )
    assert response.status_code == 200
    moved = response.get_json()
    assert moved["start_time"] == "10:00"
    assert moved["end_time"] == "10:30"

    slots = _slots(app)
    assert "09:00" in slots
    assert "10:00" not in slots
    assert "reschedule" in _notification_types(app, booking["id"])


def test_reschedule_to_a_taken_slot_is_refused(app):
    first = _customer(app, "move-first@example.com")
    second = _customer(app, "move-second@example.com")
    booking = _book(first, MONDAY, "09:00")
    _book(second, MONDAY, "10:00")

    response = first.patch(
        f"/api/appointments/{booking['id']}/reschedule",
        json={"date": MONDAY.isoformat(), "start_time": "10:00"},
    )
    assert response.status_code == 409


def test_reschedule_rules_for_cutoff_and_status(app):
    customer = _customer(app, "move-cutoff@example.com")
    soon_id = _soon_appointment(app, _me(customer)["id"])
    refused = customer.patch(
        f"/api/appointments/{soon_id}/reschedule",
        json={"date": date.today().isoformat(), "start_time": "10:00"},
    )
    assert refused.status_code == 409
    assert "hours in advance" in refused.get_json()["error"]

    booking = _book(customer, MONDAY, "09:00")
    customer.patch(f"/api/appointments/{booking['id']}/cancel")
    assert (
        customer.patch(
            f"/api/appointments/{booking['id']}/reschedule",
            json={"date": TUESDAY.isoformat(), "start_time": "09:00"},
        ).status_code
        == 409
    )


def test_only_the_owner_or_staff_can_reschedule(app):
    owner = _customer(app, "move-owner@example.com")
    stranger = _customer(app, "move-stranger@example.com")
    booking = _book(owner, MONDAY, "09:00")
    path = f"/api/appointments/{booking['id']}/reschedule"
    payload = {"date": MONDAY.isoformat(), "start_time": "11:00"}

    assert stranger.patch(path, json=payload).status_code == 403

    admin = _login(app.test_client(), ADMIN)
    assert admin.patch(path, json=payload).status_code == 200


# --- Status flow -----------------------------------------------------------


def test_provider_completes_a_started_appointment(app):
    customer = _customer(app, "status@example.com")
    appointment_id = _past_appointment(app, _me(customer)["id"])
    alice = _login(app.test_client(), ALICE)

    response = alice.patch(
        f"/api/appointments/{appointment_id}/status",
        json={"status": "completed"},
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "completed"

    # Terminal states cannot be changed again.
    assert (
        alice.patch(
            f"/api/appointments/{appointment_id}/status",
            json={"status": "no_show"},
        ).status_code
        == 409
    )


def test_status_rejects_future_and_unknown_values(app):
    customer = _customer(app, "status-future@example.com")
    booking = _book(customer, MONDAY, "09:00")
    alice = _login(app.test_client(), ALICE)
    path = f"/api/appointments/{booking['id']}/status"

    assert alice.patch(path, json={"status": "completed"}).status_code == 400
    assert alice.patch(path, json={"status": "booked"}).status_code == 400
    assert alice.patch(path, json={}).status_code == 400


def test_status_permissions_and_no_show(app):
    customer = _customer(app, "status-perms@example.com")
    appointment_id = _past_appointment(app, _me(customer)["id"])

    assert (
        customer.patch(
            f"/api/appointments/{appointment_id}/status",
            json={"status": "completed"},
        ).status_code
        == 403
    )

    bob = _login(app.test_client(), BOB)
    assert (
        bob.patch(
            f"/api/appointments/{appointment_id}/status",
            json={"status": "no_show"},
        ).status_code
        == 403
    )

    alice = _login(app.test_client(), ALICE)
    response = alice.patch(
        f"/api/appointments/{appointment_id}/status", json={"status": "no_show"}
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "no_show"


def test_staff_cancellation_through_status_queues_a_notification(app):
    customer = _customer(app, "status-cancel@example.com")
    booking = _book(customer, MONDAY, "09:00")
    alice = _login(app.test_client(), ALICE)

    response = alice.patch(
        f"/api/appointments/{booking['id']}/status", json={"status": "cancelled"}
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "cancelled"
    assert "cancellation" in _notification_types(app, booking["id"])
    assert "09:00" in _slots(app)

"""Phase 3 acceptance checks: services, providers, availability and time off."""

from datetime import date, timedelta

import db as database

ADMIN = {"email": "admin@example.com", "password": "admin12345"}
ALICE = {"email": "alice@example.com", "password": "provider123"}


def _login(client, credentials):
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200
    return response


def _service(client, **overrides):
    payload = {
        "name": "Test Service",
        "description": "A service used by tests.",
        "duration_min": 30,
        "price": 40,
    }
    payload.update(overrides)
    return client.post("/api/services", json=payload)


def _weekday_windows(morning=True, afternoon=True):
    windows = []
    for day in range(5):
        if morning:
            windows.append(
                {"weekday": day, "start_time": "09:00", "end_time": "12:00"}
            )
        if afternoon:
            windows.append(
                {"weekday": day, "start_time": "13:00", "end_time": "17:00"}
            )
    return windows


def _next_weekday(target=0):
    """Next date whose weekday() is target (0 = Monday), always in the future."""
    today = date.today()
    days = (target - today.weekday()) % 7
    if days == 0:
        days = 7
    return today + timedelta(days=days)


def _add_customer(app, email="phase3-customer@example.com"):
    with app.app_context():
        conn = database.connect()
        try:
            cursor = conn.execute(
                "INSERT INTO users (name, email, password_hash, role, active, created_at)"
                " VALUES ('Phase 3 Customer', ?, 'x', 'customer', 1, ?)",
                (email, database.utc_now()),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()


def _add_booked_appointment(app, user_id, on_date, start="09:30", end="10:00"):
    with app.app_context():
        conn = database.connect()
        try:
            now = database.utc_now()
            cursor = conn.execute(
                "INSERT INTO appointments (user_id, provider_id, service_id, date,"
                " start_time, end_time, status, created_at, updated_at)"
                " VALUES (?, 1, 1, ?, ?, ?, 'booked', ?, ?)",
                (user_id, on_date.isoformat(), start, end, now, now),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()


def test_service_crud_and_visibility(app):
    admin = app.test_client()
    _login(admin, ADMIN)
    created = _service(admin)
    assert created.status_code == 201
    service_id = created.get_json()["id"]

    public = app.test_client()
    assert "Test Service" in [s["name"] for s in public.get("/api/services").get_json()]

    deactivated = admin.put(f"/api/services/{service_id}", json={"active": 0})
    assert deactivated.status_code == 200
    assert deactivated.get_json()["active"] is False

    assert "Test Service" not in [
        s["name"] for s in public.get("/api/services").get_json()
    ]
    # Non-admins cannot use include_inactive to see hidden rows.
    assert "Test Service" not in [
        s["name"] for s in public.get("/api/services?include_inactive=1").get_json()
    ]
    assert "Test Service" in [
        s["name"] for s in admin.get("/api/services?include_inactive=1").get_json()
    ]


def test_service_validation(app):
    admin = app.test_client()
    _login(admin, ADMIN)
    assert _service(admin, duration_min=45).status_code == 400
    assert _service(admin, duration_min=0).status_code == 400
    assert _service(admin, price=-1).status_code == 400
    assert _service(admin, name="   ").status_code == 400
    assert _service(admin, name="General Consultation").status_code == 409


def test_only_admins_can_manage_services(app):
    payload = {"name": "Blocked", "duration_min": 30, "price": 10}

    anonymous = app.test_client()
    assert anonymous.post("/api/services", json=payload).status_code == 401

    customer = app.test_client()
    customer.post(
        "/api/auth/register",
        json={
            "name": "Customer",
            "email": "svc-customer@example.com",
            "password": "secret123",
        },
    )
    assert customer.post("/api/services", json=payload).status_code == 403

    admin = app.test_client()
    _login(admin, ADMIN)
    assert admin.post("/api/services", json=payload).status_code == 201


def test_admin_creates_provider_who_can_log_in(app):
    admin = app.test_client()
    _login(admin, ADMIN)
    response = admin.post(
        "/api/providers",
        json={
            "name": "Carol Lee",
            "email": "carol@example.com",
            "password": "carolpass1",
            "phone": "555-0202",
            "specialization": "Massage Therapist",
            "service_ids": [1, 2],
        },
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["specialization"] == "Massage Therapist"
    assert body["service_ids"] == [1, 2]
    assert body["email"] == "carol@example.com"

    provider = app.test_client()
    assert provider.post(
        "/api/auth/login",
        json={"email": "carol@example.com", "password": "carolpass1"},
    ).status_code == 200
    assert provider.get("/api/auth/me").get_json()["role"] == "provider"

    public = app.test_client()
    listed = public.get("/api/providers?service_id=1").get_json()
    match = next(provider_row for provider_row in listed if provider_row["id"] == body["id"])
    assert "email" not in match
    assert match["service_ids"] == [1, 2]


def test_provider_email_must_be_unique(app):
    admin = app.test_client()
    _login(admin, ADMIN)
    response = admin.post(
        "/api/providers",
        json={
            "name": "Copy Cat",
            "email": "alice@example.com",
            "password": "password1",
        },
    )
    assert response.status_code == 409


def test_public_provider_list_filters_by_service(app):
    public = app.test_client()
    assert [p["id"] for p in public.get("/api/providers?service_id=4").get_json()] == [2]
    assert public.get("/api/providers?service_id=not-a-number").status_code == 400


def test_admin_can_update_provider_services(app):
    admin = app.test_client()
    _login(admin, ADMIN)
    response = admin.put(
        "/api/providers/1", json={"service_ids": [3], "specialization": "Stylist"}
    )
    assert response.status_code == 200
    assert response.get_json()["service_ids"] == [3]
    assert response.get_json()["specialization"] == "Stylist"

    public = app.test_client()
    listed = public.get("/api/providers?service_id=3").get_json()
    assert any(provider["id"] == 1 for provider in listed)

    assert admin.put("/api/providers/1", json={"service_ids": [999]}).status_code == 400


def test_inactive_providers_are_hidden_but_admins_can_include_them(app):
    admin = app.test_client()
    _login(admin, ADMIN)
    assert admin.put("/api/providers/1", json={"active": 0}).status_code == 200

    public = app.test_client()
    assert all(p["id"] != 1 for p in public.get("/api/providers").get_json())
    assert any(
        p["id"] == 1 for p in admin.get("/api/providers?include_inactive=1").get_json()
    )
    assert all(
        p["id"] != 1
        for p in public.get("/api/providers?include_inactive=1").get_json()
    )


def test_provider_availability_ownership(app):
    alice = app.test_client()
    _login(alice, ALICE)
    own = alice.get("/api/providers/1/availability")
    assert own.status_code == 200
    assert len(own.get_json()["windows"]) == 10
    assert alice.get("/api/providers/2/availability").status_code == 403

    admin = app.test_client()
    _login(admin, ADMIN)
    assert admin.get("/api/providers/2/availability").status_code == 200

    customer = app.test_client()
    customer.post(
        "/api/auth/register",
        json={"name": "Viewer", "email": "viewer@example.com", "password": "secret123"},
    )
    assert customer.get("/api/providers/1/availability").status_code == 403

    assert app.test_client().get("/api/providers/1/availability").status_code == 401


def test_overlapping_availability_windows_are_rejected(app):
    alice = app.test_client()
    _login(alice, ALICE)

    overlapping = {
        "windows": [
            {"weekday": 0, "start_time": "09:00", "end_time": "12:00"},
            {"weekday": 0, "start_time": "11:00", "end_time": "13:00"},
        ]
    }
    assert alice.put("/api/providers/1/availability", json=overlapping).status_code == 400

    back_to_back = {
        "windows": [
            {"weekday": 0, "start_time": "09:00", "end_time": "12:00"},
            {"weekday": 0, "start_time": "12:00", "end_time": "13:00"},
        ]
    }
    accepted = alice.put("/api/providers/1/availability", json=back_to_back)
    assert accepted.status_code == 200
    assert len(accepted.get_json()["windows"]) == 2


def test_removing_hours_with_a_future_booking_is_refused(app):
    appointment_date = _next_weekday(0)
    customer_id = _add_customer(app)
    appointment_id = _add_booked_appointment(app, customer_id, appointment_date)

    alice = app.test_client()
    _login(alice, ALICE)

    afternoon_only = {"windows": _weekday_windows(morning=False)}
    response = alice.put("/api/providers/1/availability", json=afternoon_only)
    assert response.status_code == 409
    assert response.get_json()["conflicts"][0]["id"] == appointment_id

    # The refused request must leave the old schedule untouched.
    assert len(alice.get("/api/providers/1/availability").get_json()["windows"]) == 10

    assert alice.put(
        "/api/providers/1/availability", json={"windows": _weekday_windows()}
    ).status_code == 200


def test_time_off_conflicts_with_bookings_and_can_be_removed(app):
    appointment_date = _next_weekday(0)
    customer_id = _add_customer(app)
    _add_booked_appointment(app, customer_id, appointment_date)

    alice = app.test_client()
    _login(alice, ALICE)

    blocked = alice.post(
        "/api/providers/1/time-off",
        json={"date": appointment_date.isoformat(), "reason": "Sick"},
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["conflicts"]

    free_date = _next_weekday(1)
    created = alice.post(
        "/api/providers/1/time-off",
        json={"date": free_date.isoformat(), "reason": "Training"},
    )
    assert created.status_code == 201
    assert created.get_json()["date"] == free_date.isoformat()

    duplicate = alice.post(
        "/api/providers/1/time-off", json={"date": free_date.isoformat()}
    )
    assert duplicate.status_code == 409

    assert alice.delete(
        f"/api/providers/1/time-off/{free_date.isoformat()}"
    ).status_code == 200
    assert alice.delete(
        f"/api/providers/1/time-off/{free_date.isoformat()}"
    ).status_code == 404

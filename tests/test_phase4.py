"""Phase 4 acceptance checks: slot generation and booking."""

import itertools
import threading
from datetime import date, timedelta

import db as database
from services_logic.slots import generate_slots

# Fixed clock for the generator unit tests. BASE may be any date; TODAY is the
# Monday on or after it, so weekday-dependent availability is predictable.
BASE = date(2030, 1, 7)
TODAY = BASE + timedelta(days=(0 - BASE.weekday()) % 7)
NEXT_MONDAY = TODAY + timedelta(days=7)

_COUNTER = itertools.count()


def _next_weekday(target=0):
    """Next real date whose weekday() is target, always in the future."""
    today = date.today()
    days = (target - today.weekday()) % 7
    if days == 0:
        days = 7
    return today + timedelta(days=days)


API_MONDAY = _next_weekday(0)

ADMIN = {"email": "admin@example.com", "password": "admin12345"}
ALICE = {"email": "alice@example.com", "password": "provider123"}


def _slots(app, provider_id=1, service_id=1, on_date=None, today=TODAY, now_hhmm=None):
    on_date = on_date or NEXT_MONDAY
    with app.app_context():
        conn = database.connect()
        try:
            return generate_slots(
                conn,
                provider_id,
                service_id,
                on_date.isoformat(),
                step_min=30,
                horizon_days=60,
                today=today,
                now_hhmm=now_hhmm,
            )
        finally:
            conn.close()


def _add_booking(app, start, end, on_date=None, provider_id=1, service_id=1):
    on_date = on_date or NEXT_MONDAY
    email = f"slot-{next(_COUNTER)}@example.com"
    with app.app_context():
        conn = database.connect()
        try:
            now = database.utc_now()
            user_id = conn.execute(
                "INSERT INTO users (name, email, password_hash, role, active, created_at)"
                " VALUES ('Slot Customer', ?, 'x', 'customer', 1, ?)",
                (email, now),
            ).lastrowid
            conn.execute(
                "INSERT INTO appointments (user_id, provider_id, service_id, date,"
                " start_time, end_time, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'booked', ?, ?)",
                (user_id, provider_id, service_id, on_date.isoformat(), start, end, now, now),
            )
            conn.commit()
        finally:
            conn.close()


def _set_active(app, table, row_id, value):
    with app.app_context():
        conn = database.connect()
        try:
            conn.execute(f"UPDATE {table} SET active = ? WHERE id = ?", (value, row_id))
            conn.commit()
        finally:
            conn.close()


def _add_time_off(app, day):
    with app.app_context():
        conn = database.connect()
        try:
            conn.execute(
                "INSERT INTO time_off (provider_id, date, reason) VALUES (1, ?, 'Holiday')",
                (day.isoformat(),),
            )
            conn.commit()
        finally:
            conn.close()


def _customer(app, email):
    client = app.test_client()
    response = client.post(
        "/api/auth/register",
        json={"name": "Customer", "email": email, "password": "secret123"},
    )
    assert response.status_code == 201
    return client


def _payload(**overrides):
    payload = {
        "provider_id": 1,
        "service_id": 1,
        "date": API_MONDAY.isoformat(),
        "start_time": "09:00",
    }
    payload.update(overrides)
    return payload


# --- Slot generator (unit) -------------------------------------------------


def test_normal_day_slots(app):
    slots = _slots(app)
    assert slots[:3] == ["09:00", "09:30", "10:00"]
    assert "13:00" in slots
    assert slots == sorted(slots)


def test_lunch_break_split(app):
    slots = _slots(app)
    assert "11:30" in slots
    assert "12:00" not in slots
    assert "12:30" not in slots
    assert "13:00" in slots


def test_longer_service_uses_its_duration(app):
    # Bob (provider 2) offers the 60-minute Colour Treatment (service 4).
    slots = _slots(app, provider_id=2, service_id=4)
    assert "09:00" in slots
    assert "09:30" in slots
    assert "11:00" in slots
    assert "11:30" not in slots  # 11:30 + 60 minutes passes 12:00
    assert "13:00" in slots


def test_back_to_back_booking_is_allowed(app):
    _add_booking(app, "09:00", "09:30")
    slots = _slots(app)
    assert "09:00" not in slots
    assert "09:30" in slots


def test_overlap_with_longer_booking_is_excluded(app):
    _add_booking(app, "09:00", "10:00")
    slots = _slots(app)
    assert "09:00" not in slots
    assert "09:30" not in slots
    assert "10:00" in slots  # starts exactly when the booking ends


def test_today_past_times_are_removed(app):
    slots = _slots(app, on_date=TODAY, today=TODAY, now_hhmm="10:15")
    assert "09:00" not in slots
    assert "10:00" not in slots  # starts at or before "now"
    assert "10:30" in slots


def test_time_off_day_returns_no_slots(app):
    _add_time_off(app, NEXT_MONDAY)
    assert _slots(app) == []


def test_provider_not_offering_service_returns_no_slots(app):
    # Alice does not offer the Colour Treatment.
    assert _slots(app, provider_id=1, service_id=4) == []


def test_inactive_service_returns_no_slots(app):
    _set_active(app, "services", 1, 0)
    assert _slots(app) == []


def test_inactive_provider_returns_no_slots(app):
    _set_active(app, "providers", 1, 0)
    assert _slots(app) == []


# --- Slots API -------------------------------------------------------------


def test_slots_endpoint_returns_generated_slots(app):
    public = app.test_client()
    response = public.get(
        "/api/slots",
        query_string={
            "provider_id": 1,
            "service_id": 1,
            "date": API_MONDAY.isoformat(),
        },
    )
    assert response.status_code == 200
    slots = response.get_json()["slots"]
    assert "09:00" in slots
    assert "12:00" not in slots


def test_slots_requires_valid_parameters(app):
    public = app.test_client()
    assert public.get("/api/slots").status_code == 400
    assert (
        public.get(
            "/api/slots",
            query_string={"provider_id": 1, "service_id": 1, "date": "not-a-date"},
        ).status_code
        == 400
    )


# --- Booking API -----------------------------------------------------------


def test_booking_creates_appointment_and_confirmation(app):
    customer = _customer(app, "booker@example.com")
    response = customer.post("/api/appointments", json=_payload())
    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "booked"
    assert body["start_time"] == "09:00"
    assert body["end_time"] == "09:30"
    assert body["provider_name"] == "Alice Chen"
    assert body["service_name"] == "General Consultation"

    with app.app_context():
        conn = database.connect()
        try:
            notification = conn.execute(
                "SELECT type, status FROM notifications WHERE appointment_id = ?",
                (body["id"],),
            ).fetchone()
        finally:
            conn.close()
    assert notification["type"] == "confirmation"
    assert notification["status"] == "pending"


def test_taken_slot_returns_409(app):
    first = _customer(app, "first@example.com")
    second = _customer(app, "second@example.com")
    assert first.post("/api/appointments", json=_payload()).status_code == 201

    response = second.post("/api/appointments", json=_payload())
    assert response.status_code == 409
    assert response.get_json()["error"] == "That time was just taken. Pick another slot."


def test_past_date_is_rejected(app):
    customer = _customer(app, "past@example.com")
    response = customer.post(
        "/api/appointments",
        json=_payload(date=(date.today() - timedelta(days=1)).isoformat()),
    )
    assert response.status_code == 400


def test_beyond_horizon_is_rejected(app):
    customer = _customer(app, "far@example.com")
    response = customer.post(
        "/api/appointments",
        json=_payload(date=(date.today() + timedelta(days=61)).isoformat()),
    )
    assert response.status_code == 400


def test_time_off_booking_is_rejected(app):
    _add_time_off(app, API_MONDAY)
    customer = _customer(app, "off@example.com")
    assert customer.post("/api/appointments", json=_payload()).status_code == 400


def test_inactive_service_is_rejected(app):
    _set_active(app, "services", 1, 0)
    customer = _customer(app, "inactive@example.com")
    assert customer.post("/api/appointments", json=_payload()).status_code == 400


def test_wrong_provider_service_pair_is_rejected(app):
    customer = _customer(app, "pair@example.com")
    # Alice (provider 1) does not offer the Colour Treatment (service 4).
    assert customer.post(
        "/api/appointments", json=_payload(service_id=4)
    ).status_code == 400


def test_customer_overlap_is_rejected(app):
    customer = _customer(app, "overlap@example.com")
    payload = {
        "provider_id": 2,
        "service_id": 4,
        "date": API_MONDAY.isoformat(),
        "start_time": "09:00",
    }
    assert customer.post("/api/appointments", json=payload).status_code == 201
    payload["start_time"] = "09:30"
    assert customer.post("/api/appointments", json=payload).status_code == 409


def test_only_customers_can_book(app):
    assert app.test_client().post("/api/appointments", json=_payload()).status_code == 401

    provider = app.test_client()
    provider.post("/api/auth/login", json=ALICE)
    assert provider.post("/api/appointments", json=_payload()).status_code == 403

    admin = app.test_client()
    admin.post("/api/auth/login", json=ADMIN)
    assert admin.post("/api/appointments", json=_payload()).status_code == 403


def test_simultaneous_bookings_produce_exactly_one_success(app):
    first = _customer(app, "race1@example.com")
    second = _customer(app, "race2@example.com")
    payload = _payload()
    barrier = threading.Barrier(2)
    results = []

    def attempt(client):
        barrier.wait()
        results.append(client.post("/api/appointments", json=payload).status_code)

    threads = [
        threading.Thread(target=attempt, args=(client,))
        for client in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [201, 409]

"""Phase 8 checks: admin reports and the provider id on the session user."""

from datetime import date, timedelta

import db as database

ADMIN = {"email": "admin@example.com", "password": "admin12345"}
ALICE = {"email": "alice@example.com", "password": "provider123"}


def _login(client, credentials):
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200
    return client


def _customer(app, email="reporter@example.com"):
    client = app.test_client()
    response = client.post(
        "/api/auth/register",
        json={"name": "Report Customer", "email": email, "password": "secret123"},
    )
    assert response.status_code == 201
    return client, response.get_json()["id"]


def _insert(
    app,
    user_id,
    on_date,
    start,
    end,
    status,
    provider_id=1,
    service_id=1,
):
    """Insert directly so the report has known dates and statuses."""
    with app.app_context():
        conn = database.connect()
        try:
            now = database.utc_now()
            conn.execute(
                "INSERT INTO appointments (user_id, provider_id, service_id, date,"
                " start_time, end_time, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    provider_id,
                    service_id,
                    on_date,
                    start,
                    end,
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _build_report_data(app):
    """Five appointments in Feb 2026, plus one outside the tested range."""
    _client, user_id = _customer(app)
    _insert(app, user_id, "2026-02-01", "09:00", "09:30", "completed")
    _insert(app, user_id, "2026-02-01", "10:00", "10:30", "completed")
    # Bob's Haircut (service 3, $25).
    _insert(
        app,
        user_id,
        "2026-02-01",
        "11:00",
        "11:30",
        "completed",
        provider_id=2,
        service_id=3,
    )
    _insert(app, user_id, "2026-02-02", "09:00", "09:30", "no_show")
    _insert(app, user_id, "2026-02-03", "09:00", "09:30", "cancelled")
    _insert(app, user_id, "2026-02-04", "09:00", "09:30", "booked")
    return user_id


RANGE = {"from": "2026-02-01", "to": "2026-02-03"}


def test_reports_require_admin(app):
    summary = "/api/reports/summary?" + "&".join(f"{k}={v}" for k, v in RANGE.items())
    assert app.test_client().get(summary).status_code == 401

    customer, _ = _customer(app)
    assert customer.get(summary).status_code == 403

    provider = _login(app.test_client(), ALICE)
    assert provider.get(summary).status_code == 403

    admin = _login(app.test_client(), ADMIN)
    assert admin.get(summary).status_code == 200


def test_summary_totals_and_breakdowns(app):
    _build_report_data(app)
    admin = _login(app.test_client(), ADMIN)

    response = admin.get("/api/reports/summary", query_string=RANGE)
    assert response.status_code == 200
    data = response.get_json()

    assert data["from"] == "2026-02-01"
    assert data["to"] == "2026-02-03"

    totals = data["totals"]
    assert totals["appointments"] == 5
    assert totals["by_status"] == {
        "booked": 0,
        "completed": 3,
        "cancelled": 1,
        "no_show": 1,
    }
    assert totals["completed"] == 3
    assert totals["revenue"] == 125.0
    # One miss out of four visits that happened or were missed.
    assert totals["no_show_rate"] == 0.25
    assert totals["unique_customers"] == 1

    services = {row["service_id"]: row for row in data["by_service"]}
    assert services[1]["appointments"] == 4
    assert services[1]["completed"] == 2
    assert services[1]["no_show"] == 1
    assert services[1]["revenue"] == 100.0
    assert services[3]["appointments"] == 1
    assert services[3]["revenue"] == 25.0

    providers = {row["provider_id"]: row for row in data["by_provider"]}
    assert providers[1]["appointments"] == 4
    assert providers[1]["revenue"] == 100.0
    assert providers[2]["appointments"] == 1
    assert providers[2]["revenue"] == 25.0

    days = {row["date"]: row for row in data["by_day"]}
    assert days["2026-02-01"]["appointments"] == 3
    assert days["2026-02-01"]["revenue"] == 125.0
    assert days["2026-02-02"]["no_show"] == 1
    assert days["2026-02-03"]["completed"] == 0
    # The 4 February booking is outside the requested range.
    assert "2026-02-04" not in days


def test_summary_is_empty_for_a_quiet_range(app):
    _build_report_data(app)
    admin = _login(app.test_client(), ADMIN)

    response = admin.get(
        "/api/reports/summary",
        query_string={"from": "2025-01-01", "to": "2025-01-31"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["totals"]["appointments"] == 0
    assert data["totals"]["revenue"] == 0.0
    assert data["totals"]["no_show_rate"] == 0.0
    assert data["by_service"] == []
    assert data["by_provider"] == []
    assert data["by_day"] == []


def test_summary_defaults_to_the_last_thirty_days(app):
    admin = _login(app.test_client(), ADMIN)
    response = admin.get("/api/reports/summary")
    assert response.status_code == 200
    data = response.get_json()

    today = date.today()
    assert data["to"] == today.isoformat()
    assert data["from"] == (today - timedelta(days=29)).isoformat()


def test_report_range_validation(app):
    admin = _login(app.test_client(), ADMIN)

    assert admin.get(
        "/api/reports/summary", query_string={"from": "nope"}
    ).status_code == 400
    assert admin.get(
        "/api/reports/summary", query_string={"to": "2026-02-30"}
    ).status_code == 400
    assert admin.get(
        "/api/reports/summary",
        query_string={"from": "2026-03-01", "to": "2026-02-01"},
    ).status_code == 400
    # 2020-01-01 through 2026-01-01 is well over the 366-day cap.
    too_long = admin.get(
        "/api/reports/summary",
        query_string={"from": "2020-01-01", "to": "2026-01-01"},
    )
    assert too_long.status_code == 400
    assert "366 days" in too_long.get_json()["error"]


def test_appointments_report_filters(app):
    _build_report_data(app)
    admin = _login(app.test_client(), ADMIN)

    response = admin.get("/api/reports/appointments", query_string=RANGE)
    assert response.status_code == 200
    data = response.get_json()
    assert data["from"] == "2026-02-01"
    assert data["to"] == "2026-02-03"
    assert data["count"] == 5
    assert len(data["appointments"]) == 5

    completed = admin.get(
        "/api/reports/appointments",
        query_string={**RANGE, "status": "completed"},
    ).get_json()
    assert completed["count"] == 3

    bob = admin.get(
        "/api/reports/appointments",
        query_string={**RANGE, "provider_id": 2},
    ).get_json()
    assert bob["count"] == 1
    assert bob["appointments"][0]["provider_name"] == "Bob Rivera"

    bad_status = admin.get(
        "/api/reports/appointments", query_string={**RANGE, "status": "maybe"}
    )
    assert bad_status.status_code == 400

    bad_provider = admin.get(
        "/api/reports/appointments", query_string={**RANGE, "provider_id": "x"}
    )
    assert bad_provider.status_code == 400


def test_export_returns_csv(app):
    _build_report_data(app)
    admin = _login(app.test_client(), ADMIN)

    response = admin.get("/api/reports/export", query_string=RANGE)
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "appointments-2026-02-01-to-2026-02-03.csv" in response.headers[
        "Content-Disposition"
    ]

    lines = response.get_data(as_text=True).strip().splitlines()
    assert lines[0] == (
        "id,date,start_time,end_time,status,customer,provider,service,price,revenue"
    )
    assert len(lines) == 6  # header plus five appointments
    assert any(",completed," in line and ",50.00,50.00" in line for line in lines)
    assert any(",cancelled," in line and ",50.00,0.00" in line for line in lines)


def test_provider_session_includes_provider_id(app):
    customer, _ = _customer(app)
    assert "provider_id" not in customer.get("/api/auth/me").get_json()

    admin = _login(app.test_client(), ADMIN)
    assert "provider_id" not in admin.get("/api/auth/me").get_json()

    alice = _login(app.test_client(), ALICE)
    me = alice.get("/api/auth/me").get_json()
    assert me["role"] == "provider"
    assert me["provider_id"] == 1

    # The id is usable against the existing, ownership-checked endpoint.
    response = alice.get(f"/api/providers/{me['provider_id']}/availability")
    assert response.status_code == 200
    assert "windows" in response.get_json()


# ---------- Static staff frontend ----------


def test_staff_frontend_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    for marker in (
        'id="manage-view"',
        'id="availability-view"',
        'id="report-form"',
        'id="service-form"',
        'id="provider-form"',
        'id="availability-provider"',
        'data-view="manage"',
        'data-view="availability"',
        '<script type="module" src="/js/app.js"></script>',
    ):
        assert marker in html, marker

    for path in ("/js/staff.js", "/js/reports.js"):
        asset = client.get(path)
        assert asset.status_code == 200, path
        assert asset.get_data(), path

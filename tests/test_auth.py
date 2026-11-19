"""Phase 2 acceptance checks: registration, login, sessions and roles."""

import time

from werkzeug.security import check_password_hash

import db as database
from routes.auth import role_required

ADMIN = {"email": "admin@example.com", "password": "admin12345"}


def _register(client, **overrides):
    payload = {
        "name": "Test Customer",
        "email": "customer@example.com",
        "password": "secret123",
    }
    payload.update(overrides)
    return client.post("/api/auth/register", json=payload)


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_register_creates_customer_and_logs_in(client):
    response = _register(client, email="New.Customer@Example.com", phone="555-0100")
    assert response.status_code == 201
    body = response.get_json()
    assert body["role"] == "customer"
    assert body["email"] == "new.customer@example.com"
    assert body["active"] is True

    me = client.get("/api/auth/me").get_json()
    assert me is not None
    assert me["id"] == body["id"]


def test_duplicate_email_is_rejected_case_insensitively(client):
    assert _register(client, email="dup@example.com").status_code == 201
    response = _register(client, email="DUP@Example.com")
    assert response.status_code == 409
    assert "error" in response.get_json()


def test_short_password_is_rejected(client):
    response = _register(client, password="short")
    assert response.status_code == 400


def test_unknown_email_and_wrong_password_return_same_message(client):
    unknown = _login(client, "nobody@example.com", "whatever123")
    wrong = _login(client, "admin@example.com", "not-the-password")
    assert unknown.status_code == 401
    assert wrong.status_code == 401
    assert unknown.get_json()["error"] == wrong.get_json()["error"]


def test_passwords_are_never_stored_in_plain_text(app, client):
    assert _register(client, email="hashme@example.com", password="plaintext123").status_code == 201
    with app.app_context():
        conn = database.connect()
        try:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE email = ?",
                ("hashme@example.com",),
            ).fetchone()
        finally:
            conn.close()
    assert row["password_hash"] != "plaintext123"
    assert row["password_hash"] not in ("plaintext123", "secret123")
    assert check_password_hash(row["password_hash"], "plaintext123")


def test_session_cookie_is_httponly_and_same_site(client):
    response = _login(client, **ADMIN)
    assert response.status_code == 200
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_logout_clears_the_session(client):
    assert _login(client, **ADMIN).status_code == 200
    assert client.get("/api/auth/me").get_json() is not None
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").get_json() is None


def test_expired_session_is_treated_as_logged_out(client):
    assert _login(client, **ADMIN).status_code == 200
    # Pretend the cookie was issued nine hours ago (the lifetime is eight).
    with client.session_transaction() as sess:
        sess["issued_at"] = time.time() - 9 * 3600
    assert client.get("/api/auth/me").get_json() is None


def test_anonymous_cannot_reach_a_protected_route(app, client):
    @app.get("/api/admin/probe")
    @role_required("admin")
    def probe():
        return {"ok": True}

    response = client.get("/api/admin/probe")
    assert response.status_code == 401


def test_customer_cannot_reach_an_admin_route(app, client):
    @app.get("/api/admin/probe")
    @role_required("admin")
    def probe():
        return {"ok": True}

    assert _register(client, email="customer2@example.com").status_code == 201
    response = client.get("/api/admin/probe")
    assert response.status_code == 403


def test_admin_can_reach_an_admin_route(app, client):
    @app.get("/api/admin/probe")
    @role_required("admin")
    def probe():
        return {"ok": True}

    assert _login(client, **ADMIN).status_code == 200
    response = client.get("/api/admin/probe")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_repeated_failed_logins_are_rate_limited(client):
    for _ in range(5):
        assert _login(client, ADMIN["email"], "wrong-password").status_code == 401
    response = _login(client, **ADMIN)
    assert response.status_code == 401
    assert "Too many" in response.get_json()["error"]

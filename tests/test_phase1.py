"""Phase 1 acceptance checks: schema, foreign keys, seeding, static serving."""

import sqlite3
from datetime import datetime, timedelta

import pytest

import db as database


def _count(app, table):
    with app.app_context():
        conn = database.connect()
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()


def _add_customer(conn):
    now = database.utc_now()
    cursor = conn.execute(
        "INSERT INTO users (name, email, password_hash, role, active, created_at)"
        " VALUES ('Customer', 'customer@example.com', 'x', 'customer', 1, ?)",
        (now,),
    )
    return cursor.lastrowid


def _add_appointment(conn, user_id, start, status="booked", minutes=30):
    start_dt = datetime.strptime(start, "%H:%M")
    end = (start_dt + timedelta(minutes=minutes)).strftime("%H:%M")
    now = database.utc_now()
    conn.execute(
        "INSERT INTO appointments (user_id, provider_id, service_id, date, start_time,"
        " end_time, status, created_at, updated_at)"
        " VALUES (?, 1, 1, '2030-01-07', ?, ?, ?, ?, ?)",
        (user_id, start, end, status, now, now),
    )


def test_seed_data_loaded_once(app):
    assert _count(app, "users") == 3  # one admin, two providers
    assert _count(app, "services") == 4
    assert _count(app, "providers") == 2
    assert _count(app, "provider_services") == 4
    assert _count(app, "availability") == 20  # 2 windows x 5 days x 2 providers


def test_second_seed_is_a_no_op(app):
    with app.app_context():
        assert database.seed() is False
    assert _count(app, "users") == 3


def test_foreign_keys_are_enabled_and_enforced(app):
    with app.app_context():
        conn = database.connect()
        try:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            now = database.utc_now()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO appointments (user_id, provider_id, service_id, date,"
                    " start_time, end_time, created_at, updated_at)"
                    " VALUES (99999, 1, 1, '2030-01-07', '09:00', '09:30', ?, ?)",
                    (now, now),
                )
        finally:
            conn.close()


def test_database_blocks_double_booking(app):
    with app.app_context():
        conn = database.connect()
        try:
            user_id = _add_customer(conn)
            _add_appointment(conn, user_id, "09:00")
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                _add_appointment(conn, user_id, "09:00")
                conn.commit()
            conn.rollback()
        finally:
            conn.close()


def test_non_booked_row_does_not_block_a_slot(app):
    with app.app_context():
        conn = database.connect()
        try:
            user_id = _add_customer(conn)
            _add_appointment(conn, user_id, "10:00", status="cancelled")
            _add_appointment(conn, user_id, "10:00", status="booked")
            conn.commit()
        finally:
            conn.close()
    assert _count(app, "appointments") == 2


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    # The placeholder was replaced by the real frontend in phase 7.
    assert b"Appointly" in response.data
    assert b"/js/app.js" in response.data


def test_unknown_api_route_returns_json(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.get_json()["error"]

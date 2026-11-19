"""Shared pytest fixtures.

Every test run gets a fresh temporary SQLite file, so tests never touch the
development database.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db as database  # noqa: E402
from app import create_app  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    # tmp_path is a fresh directory per test, so tests cannot see each other's
    # data and the development database is never touched.
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DB_PATH": str(tmp_path / "test.db"),
        }
    )
    # An app context lets db helpers read current_app.config outside a request.
    with application.app_context():
        database.init_db()
        database.seed()
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()

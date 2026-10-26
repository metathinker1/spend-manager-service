"""Tests for /health and /health/db (FD v2 §1.10)."""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from spend_manager.config import Settings, get_settings
from spend_manager.db.backup import create_backup
from spend_manager.db.schema import MIGRATIONS, ensure_schema, open_db
from spend_manager.main import app

REQUIRED = MIGRATIONS[-1][0]


@pytest.fixture
def client_for(tmp_path):
    def make(**values) -> TestClient:
        settings = Settings(spend_manager_db_path=str(tmp_path / "sm.db"), **values)
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    try:
        yield make
    finally:
        app.dependency_overrides.clear()


def _migrated(path):
    conn = open_db(path)
    ensure_schema(conn)
    conn.close()


def test_health_reports_environment(client_for, tmp_path):
    client = client_for(
        spend_manager_env="production", spend_manager_backup_dir=str(tmp_path / "b")
    )
    assert client.get("/health").json() == {"status": "ok", "env": "production"}


def test_db_ok_with_last_backup(client_for, tmp_path):
    _migrated(tmp_path / "sm.db")
    create_backup(
        tmp_path / "sm.db",
        tmp_path / "b",
        "development",
        now=datetime.datetime(2026, 10, 22, 14, 30, tzinfo=datetime.UTC),
    )
    client = client_for(spend_manager_backup_dir=str(tmp_path / "b"))

    response = client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "env": "development",
        "database": "sm.db",
        "schema_version": REQUIRED,
        "required_schema_version": REQUIRED,
        "last_backup_at": "2026-10-22T14:30:00+00:00",
        "last_backup_file": "spend_manager_development_20261022T143000Z.db.gz",
        "reason": None,
    }


def test_db_missing(client_for):
    response = client_for().get("/health/db")
    assert response.status_code == 503
    body = response.json()
    assert (body["status"], body["reason"], body["schema_version"]) == ("error", "missing", None)
    assert body["last_backup_file"] is None


def test_db_schema_outdated_and_read_only(client_for, tmp_path):
    db = tmp_path / "sm.db"
    open_db(db).close()  # empty database, never migrated

    response = client_for().get("/health/db")

    assert response.status_code == 503
    assert response.json()["reason"] == "schema_outdated"
    conn = open_db(db)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0  # no DDL
    conn.close()


def test_db_schema_newer(client_for, tmp_path):
    _migrated(tmp_path / "sm.db")
    conn = open_db(tmp_path / "sm.db")
    conn.execute("INSERT INTO schema_version VALUES (99, 'x')")
    conn.close()
    assert client_for().get("/health/db").json()["reason"] == "schema_newer"


def test_db_unreadable(client_for, tmp_path):
    (tmp_path / "sm.db").write_bytes(b"not a database" * 100)
    response = client_for().get("/health/db")
    assert response.status_code == 503
    assert response.json()["reason"] == "unreadable"
    assert str(tmp_path) not in response.text

"""API tests for ``POST /imports/moneywell-store`` (FR-1, FR-12, FR-13, FR-14).

``get_settings`` is overridden to per-test tmp directories, so no test
touches the real MoneyWell data or the project's ``data/`` directory.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from moneywell_fixture import BUDGET_ID, activity, add_transaction, default_store, write_store

from spend_manager.config import Settings, get_settings
from spend_manager.main import app
from spend_manager.services import moneywell_store_import

URL = "/imports/moneywell-store"


@pytest.fixture
def api(tmp_path):
    root = tmp_path / "MoneyWell_Data"
    root.mkdir()
    settings = Settings(
        moneywell_store_root=str(root), spend_manager_db_path=str(tmp_path / "sm.db")
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app, raise_server_exceptions=False), root, settings
    finally:
        app.dependency_overrides.clear()


def test_import_returns_201_with_summary(api):
    client, root, _ = api
    write_store(default_store(), root / "SampleData")

    response = client.post(URL, json={"budget_folder": "SampleData"})

    assert response.status_code == 201
    body = response.json()
    assert body["moneywell_budget_id"] == BUDGET_ID
    assert body["counts"]["transactions"] == {"inserted": 7, "skipped_existing": 0}
    assert body["counts"]["transaction_split_tags"] == {"inserted": 1, "skipped_existing": 0}
    assert set(body["counts"]) == {
        "accounts",
        "categories",
        "tags",
        "transactions",
        "transaction_splits",
        "transaction_tags",
        "transaction_split_tags",
    }
    assert len(body["warnings"]) == 2


def test_reimport_returns_skipped_counts(api):
    client, root, _ = api
    write_store(default_store(), root / "SampleData")
    client.post(URL, json={"budget_folder": "SampleData"})

    body = client.post(URL, json={"budget_folder": "SampleData"}).json()

    assert body["counts"]["transactions"] == {"inserted": 0, "skipped_existing": 7}


def test_missing_folder_returns_404(api):
    client, _, _ = api
    response = client.post(URL, json={"budget_folder": "Nope"})
    assert response.status_code == 404
    assert response.json() == {"detail": "budget folder not found: Nope"}


def test_path_escape_returns_400(api):
    client, _, _ = api
    response = client.post(URL, json={"budget_folder": "../etc"})
    assert response.status_code == 400


def test_validation_failure_returns_400(api):
    client, root, _ = api
    store = default_store()
    activity(store, "SPL-3")["ZAMOUNT"] = -11
    write_store(store, root / "Bad")

    response = client.post(URL, json={"budget_folder": "Bad"})

    assert response.status_code == 400
    assert "V-10" in response.json()["detail"]


def test_budget_mismatch_returns_400(api):
    client, root, _ = api
    write_store(default_store(), root / "Mine")
    other = default_store()
    other["budget_ids"] = ["OTHER"]
    write_store(other, root / "Other")
    client.post(URL, json={"budget_folder": "Mine"})

    response = client.post(URL, json={"budget_folder": "Other"})

    assert response.status_code == 400
    assert "OTHER" in response.json()["detail"]


def test_split_conflict_returns_400(api):
    client, root, _ = api
    store = default_store()
    write_store(store, root / "V1")
    client.post(URL, json={"budget_folder": "V1"})
    add_transaction(store, 80, "SPL-80", ZAMOUNT=-500, ZSPLITPARENT=3, ZDATEYMD=20260107)
    write_store(store, root / "V2")

    response = client.post(URL, json={"budget_folder": "V2"})

    assert response.status_code == 400
    assert "TXN-3" in response.json()["detail"]


@pytest.mark.parametrize("body", [{}, {"budget_folder": ""}, {"budget_folder": 5}])
def test_malformed_body_returns_422(api, body):
    client, _, _ = api
    assert client.post(URL, json=body).status_code == 422


def test_unexpected_database_error_returns_500(api, monkeypatch):
    client, root, settings = api
    write_store(default_store(), root / "SampleData")

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(moneywell_store_import, "_write", boom)
    response = client.post(URL, json={"budget_folder": "SampleData"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


def test_newer_schema_returns_500(api):
    client, root, settings = api
    write_store(default_store(), root / "SampleData")
    conn = sqlite3.connect(settings.db_path)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
    conn.execute("INSERT INTO schema_version VALUES (99, 'x')")
    conn.commit()
    conn.close()

    response = client.post(URL, json={"budget_folder": "SampleData"})

    assert response.status_code == 500


def test_text_export_endpoint_is_deprecated_in_openapi(api):
    client, _, _ = api
    paths = client.get("/openapi.json").json()["paths"]
    assert paths["/imports/{year}/{month}"]["post"]["deprecated"] is True
    assert not paths[URL]["post"].get("deprecated", False)


def test_directory_db_path_returns_500_and_logs_the_cause(api, tmp_path, caplog):
    # HP-1 regression: SPEND_MANAGER_DB_PATH set to a folder.
    client, root, _ = api
    write_store(default_store(), root / "SampleData")
    db_dir = tmp_path / "dev"
    db_dir.mkdir()
    app.dependency_overrides[get_settings] = lambda: Settings(
        moneywell_store_root=str(root), spend_manager_db_path=str(db_dir)
    )

    response = client.post(URL, json={"budget_folder": "SampleData"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "must be a database file path, not a directory" in caplog.text
    assert "NoneType: None" not in caplog.text


def test_unexpected_database_error_is_logged_with_traceback(api, monkeypatch, caplog):
    # HP-1 regression: the log must name the real error, not "NoneType: None".
    client, root, _ = api
    write_store(default_store(), root / "SampleData")

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(moneywell_store_import, "_write", boom)
    client.post(URL, json={"budget_folder": "SampleData"})

    record = next(r for r in caplog.records if "Unhandled database error" in r.getMessage())
    assert "unable to open database file" in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is not None
    assert "NoneType: None" not in caplog.text


def test_unexpected_os_error_is_logged_with_traceback(api, monkeypatch, caplog):
    client, root, _ = api
    write_store(default_store(), root / "SampleData")

    def boom(*args, **kwargs):
        raise PermissionError("permission denied: persistentStore")

    monkeypatch.setattr(moneywell_store_import, "read_store", boom)
    response = client.post(URL, json={"budget_folder": "SampleData"})

    assert response.status_code == 500
    record = next(r for r in caplog.records if "Unhandled OS error" in r.getMessage())
    assert "permission denied" in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is not None

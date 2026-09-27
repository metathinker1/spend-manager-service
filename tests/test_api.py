"""API tests for spend_manager.main / spend_manager.api.imports.

Uses FastAPI's TestClient with the ``get_settings`` dependency overridden
to point at per-test tmp directories, so no test touches real Google Drive
paths or the project's own ``data/`` directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spend_manager.config import Settings, get_settings
from spend_manager.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_202501.txt"


@pytest.fixture
def api_client(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()

    def _override_settings() -> Settings:
        return Settings(moneywell_source_dir=str(source_dir), output_dir=str(output_dir))

    app.dependency_overrides[get_settings] = _override_settings
    client = TestClient(app)
    try:
        yield client, source_dir, output_dir
    finally:
        app.dependency_overrides.clear()


def test_health_check():
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_successful_import_returns_201_with_output_file(api_client):
    client, source_dir, output_dir = api_client
    (source_dir / "RobBudget_202501_Details.txt").write_bytes(FIXTURE.read_bytes())

    response = client.post("/imports/2025/01")

    assert response.status_code == 201
    body = response.json()
    expected_path = output_dir / "transactions_202501.csv"
    assert body == {"output_file": str(expected_path)}
    assert expected_path.is_file()


def test_missing_source_file_returns_404_with_filename_only(api_client):
    client, source_dir, _output_dir = api_client

    response = client.post("/imports/2025/01")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "RobBudget_202501_Details.txt" in detail
    # The 404 body must not leak the local filesystem layout.
    assert str(source_dir) not in detail


def test_malformed_source_file_returns_400(api_client):
    client, source_dir, _output_dir = api_client
    (source_dir / "RobBudget_202501_Details.txt").write_text("wrong\theader\n")

    response = client.post("/imports/2025/01")

    assert response.status_code == 400
    assert "header" in response.json()["detail"]


@pytest.mark.parametrize("month", ["13", "1", "00", "abc"])
def test_invalid_month_returns_422(api_client, month):
    client, _source_dir, _output_dir = api_client

    response = client.post(f"/imports/2025/{month}")

    assert response.status_code == 422


@pytest.mark.parametrize("year", ["25", "20255", "abcd"])
def test_invalid_year_returns_422(api_client, year):
    client, _source_dir, _output_dir = api_client

    response = client.post(f"/imports/{year}/10")

    assert response.status_code == 422


def test_empty_path_segments_do_not_match_route(api_client):
    # An empty year or month segment never reaches our {year}/{month}
    # pattern validation — Starlette's router itself doesn't match a
    # trailing/empty path segment, so this correctly 404s rather than 422.
    client, _source_dir, _output_dir = api_client

    assert client.post("/imports/2025/").status_code == 404
    assert client.post("/imports//10").status_code == 404


def test_reimport_overwrites_prior_csv(api_client):
    client, source_dir, output_dir = api_client
    (source_dir / "RobBudget_202501_Details.txt").write_bytes(FIXTURE.read_bytes())

    first = client.post("/imports/2025/01")
    second = client.post("/imports/2025/01")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()

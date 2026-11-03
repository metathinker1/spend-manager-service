"""API tests for the read endpoints under ``/api`` (2026-10-31-spend-manager-ui).

``get_settings`` is overridden to per-test tmp paths, so no test touches the
real database or MoneyWell data.
"""

from __future__ import annotations

import csv
import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient
from read_fixture import CARD, CHECKING, DINING, GROCERIES, TRANSFER, build_read_db

from spend_manager.api import reports as reports_api
from spend_manager.config import Settings, get_settings
from spend_manager.db.schema import ensure_schema, open_db
from spend_manager.main import app


@pytest.fixture
def make_client(tmp_path):
    def make(db_path):
        root = tmp_path / "MoneyWell_Data"
        root.mkdir(exist_ok=True)
        settings = Settings(moneywell_store_root=str(root), spend_manager_db_path=str(db_path))
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app, raise_server_exceptions=False), root

    try:
        yield make
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(make_client, tmp_path):
    return make_client(build_read_db(tmp_path / "sm.db"))[0]


def test_accounts(client):
    response = client.get("/api/accounts")

    assert response.status_code == 200
    assert response.json() == [
        {"id": CHECKING, "name": "Checking", "type": "checking", "is_hidden": False},
        {"id": CARD, "name": "Card", "type": "credit_card", "is_hidden": False},
    ]


def test_categories(client):
    body = client.get("/api/categories").json()

    assert [(c["name"], c["type"]) for c in body] == [
        ("Groceries", "expense"),
        ("Household", "expense"),
        ("Dining", "expense"),
        ("Transfer", "expense"),
        ("Salary", "income"),
    ]
    assert next(c for c in body if c["id"] == DINING)["is_hidden"] is True


def test_transactions_with_repeated_params(client):
    response = client.get(
        "/api/transactions",
        params={"category_id": [GROCERIES, DINING], "account_id": [CARD], "page_size": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [108, 106]
    assert body["page"] == 1 and body["page_size"] == 2
    assert body["total_transactions"] == 3
    assert body["matching_line_count"] == 3  # T108, S201, T102
    assert body["matching_total_cents"] == 1500 - 8000 - 2500
    split_txn = body["items"][1]
    assert split_txn["date"] == "2026-02-14"
    assert [(s["id"], s["matches"], s["is_transfer"]) for s in split_txn["splits"]] == [
        (201, True, False),
        (202, False, False),
    ]


def test_transactions_include_transfers(client):
    body = client.get(
        "/api/transactions", params={"category_id": TRANSFER, "include_transfers": "true"}
    ).json()

    assert [(i["id"], i["is_transfer"]) for i in body["items"]] == [(107, False), (105, True)]


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-02-01", "date_to": "2026-01-31"},
        {"date_from": "2026-13-01"},
        {"page": 0},
        {"page_size": 0},
        {"page_size": 201},
        {"q": "x" * 101},
        {"account_id": list(range(101))},
        {"category_id": "abc"},
        {"uncategorized": "maybe"},
    ],
)
def test_transactions_invalid_params(client, params):
    assert client.get("/api/transactions", params=params).status_code == 422


def test_export_csv(client):
    response = client.get(
        "/api/transactions/export.csv", params={"date_from": "2026-02-01", "date_to": "2026-02-28"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="transactions_2026-02-01_2026-02-28.csv"'
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [(r["transaction_id"], r["split_id"], r["amount"]) for r in rows] == [
        ("106", "201", "-80.00"),
        ("106", "202", "-40.00"),
        ("104", "", "-12.34"),
    ]
    assert rows[2]["payee"] == "'=HYPERLINK(x)"
    assert rows[2]["memo"] == "'@risk"
    assert rows[1]["memo"] == "paper towels"


def test_export_csv_with_transfers(client):
    response = client.get(
        "/api/transactions/export.csv",
        params={"category_id": TRANSFER, "include_transfers": "true"},
    )

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [(r["transaction_id"], r["is_transfer"]) for r in rows] == [
        ("107", "true"),
        ("105", "true"),
    ]
    assert response.headers["content-disposition"].endswith('"transactions_all_all.csv"')


def test_export_csv_invalid_params(client):
    response = client.get("/api/transactions/export.csv", params={"q": "x" * 101})
    assert response.status_code == 422


def test_spending_by_category(client):
    body = client.get("/api/reports/spending-by-category", params={"year": 2026}).json()

    assert body["period"] == {
        "kind": "year",
        "date_from": "2026-01-01",
        "date_to": "2026-12-31",
        "label": "2026",
        "months": [f"2026-{m:02d}" for m in range(1, 13)],
    }
    assert body["rows"][-1] == {
        "category_id": None,
        "category_name": "Uncategorized",
        "spending_cents": 6234,
        "line_count": 2,
    }
    assert body["total_cents"] == 25234


def test_spending_by_category_month(client):
    body = client.get("/api/reports/spending-by-category", params={"year": 2026, "month": 3}).json()

    assert body["period"]["label"] == "2026-03"
    assert body["total_cents"] == 3500


@pytest.mark.parametrize(
    "params",
    [{}, {"year": 1899}, {"year": 2101}, {"year": 2026, "month": 0}, {"year": 2026, "month": 13}],
)
def test_spending_by_category_invalid(client, params):
    assert client.get("/api/reports/spending-by-category", params=params).status_code == 422


def test_category_by_month_year(client):
    body = client.get("/api/reports/category-by-month", params={"year": 2025}).json()

    assert body["months"][0] == "2025-01"
    assert [(r["category_name"], r["total_cents"]) for r in body["rows"]] == [
        ("Household", 7000),
        ("Groceries", 999),
    ]
    assert len(body["column_totals_cents"]) == 12
    assert body["grand_total_cents"] == 7999


def test_category_by_month_last12(client, monkeypatch):
    monkeypatch.setattr(reports_api, "today", lambda: dt.date(2026, 3, 20))

    body = client.get("/api/reports/category-by-month", params={"range": "last12"}).json()

    assert body["period"]["kind"] == "last12"
    assert (body["months"][0], body["months"][-1]) == ("2025-04", "2026-03")
    assert body["grand_total_cents"] == 33233


def test_category_by_month_uses_the_real_date_by_default(client):
    body = client.get("/api/reports/category-by-month", params={"range": "last12"}).json()
    assert len(body["months"]) == 12


@pytest.mark.parametrize(
    "params",
    [{}, {"year": 2026, "range": "last12"}, {"range": "last6"}, {"year": 1800}],
)
def test_category_by_month_invalid(client, params):
    response = client.get("/api/reports/category-by-month", params=params)
    assert response.status_code == 422


def test_category_by_month_names_the_conflict(client):
    detail = client.get(
        "/api/reports/category-by-month", params={"year": 2026, "range": "last12"}
    ).json()["detail"]
    assert detail[0]["msg"] == "give exactly one of year and range"


def test_snapshots(make_client, tmp_path):
    client, root = make_client(tmp_path / "sm.db")
    (root / "Budget_20260821").mkdir()
    (root / "Budget_20260101.zip").write_bytes(b"")

    response = client.get("/api/moneywell-snapshots")

    assert response.status_code == 200
    assert response.json() == [
        {"name": "Budget_20260821", "kind": "folder", "snapshot_date": "2026-08-21"},
        {"name": "Budget_20260101.zip", "kind": "zip", "snapshot_date": "2026-01-01"},
    ]


def test_snapshots_missing_root(make_client, tmp_path):
    client, root = make_client(tmp_path / "sm.db")
    root.rmdir()

    response = client.get("/api/moneywell-snapshots")

    assert response.status_code == 404
    assert response.json() == {"detail": "MoneyWell store root directory not found"}


READ_URLS = [
    "/api/accounts",
    "/api/categories",
    "/api/transactions",
    "/api/transactions/export.csv",
    "/api/reports/spending-by-category?year=2026",
    "/api/reports/category-by-month?year=2026",
]


@pytest.mark.parametrize("url", READ_URLS)
def test_missing_database_is_500_and_not_created(make_client, tmp_path, url, caplog):
    db_path = tmp_path / "missing" / "sm.db"
    client, _ = make_client(db_path)

    response = client.get(url)

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert not db_path.parent.exists()
    assert "database file not found: sm.db" in caplog.text


@pytest.mark.parametrize("url", READ_URLS)
def test_outdated_schema_is_500(make_client, tmp_path, url, caplog):
    db_path = tmp_path / "old.db"
    conn = open_db(db_path)
    ensure_schema(conn)
    conn.execute("DELETE FROM schema_version WHERE version = 2")
    conn.close()
    client, _ = make_client(db_path)

    response = client.get(url)

    assert response.status_code == 500
    assert "code requires v2" in caplog.text


def test_newer_schema_is_500(make_client, tmp_path):
    db_path = tmp_path / "new.db"
    conn = open_db(db_path)
    ensure_schema(conn)
    conn.execute("INSERT INTO schema_version VALUES (99, '2099-01-01T00:00:00+00:00')")
    conn.close()
    client, _ = make_client(db_path)

    assert client.get("/api/accounts").status_code == 500


def test_not_a_database_is_500(make_client, tmp_path):
    db_path = tmp_path / "junk.db"
    db_path.write_bytes(b"this is not a database" * 100)
    client, _ = make_client(db_path)

    assert client.get("/api/accounts").status_code == 500


def test_reads_never_write(client, tmp_path):
    db_file = tmp_path / "sm.db"
    before = db_file.read_bytes()

    for url in READ_URLS:
        assert client.get(url).status_code == 200, url

    assert db_file.read_bytes() == before


def test_openapi_exposes_read_models(client):
    spec = client.get("/openapi.json").json()

    assert spec["info"]["version"] == "0.3.0"
    schemas = spec["components"]["schemas"]
    for name in (
        "TransactionPage",
        "TransactionRow",
        "SplitRow",
        "SpendingByCategoryReport",
        "CategoryByMonthReport",
        "Period",
        "Account",
        "Category",
        "Snapshot",
    ):
        assert name in schemas, name
    params = {p["name"]: p for p in spec["paths"]["/api/transactions"]["get"]["parameters"]}
    assert params["account_id"]["schema"]["type"] == "array"
    assert {"date_from", "q", "page", "page_size", "include_transfers"} <= set(params)
    export = spec["paths"]["/api/transactions/export.csv"]["get"]["responses"]["200"]
    assert "text/csv" in export["content"]

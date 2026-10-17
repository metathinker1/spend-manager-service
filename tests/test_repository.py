"""Tests for spend_manager.db.repository."""

from __future__ import annotations

import sqlite3

import pytest

from spend_manager.db import repository as repo
from spend_manager.db.schema import ensure_schema, open_db
from spend_manager.moneywell_store.convert import NewAccount, NewCategory


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "sm.db")
    ensure_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


ACCOUNT = NewAccount("ACC-1", "Checking", "checking", "USD", False, True, 1, None)
CATEGORY = NewCategory("BKT-1", "Groceries", "expense", True, 2, "food", "USD")


def test_account_and_category_round_trip(conn):
    repo.insert_accounts(conn, [ACCOUNT])
    repo.insert_categories(conn, [CATEGORY])

    assert repo.existing_uids(conn, "account") == {"ACC-1": 1}
    assert conn.execute("SELECT name, type, is_hidden, is_debt FROM account").fetchone() == (
        "Checking",
        "checking",
        0,
        1,
    )
    assert conn.execute("SELECT type, is_hidden, memo FROM category").fetchone() == (
        "expense",
        1,
        "food",
    )


def test_existing_uids_skips_rows_without_uid(conn):
    conn.execute("INSERT INTO account (name, type) VALUES ('UI-created', 'checking')")
    repo.insert_accounts(conn, [ACCOUNT])
    assert repo.existing_uids(conn, "account") == {"ACC-1": 2}


def test_existing_uids_rejects_unknown_table(conn):
    with pytest.raises(ValueError):
        repo.existing_uids(conn, "tag; DROP TABLE account")


def test_transactions_splits_and_tags(conn):
    repo.insert_accounts(conn, [ACCOUNT])
    repo.insert_categories(conn, [CATEGORY])
    repo.insert_tags(conn, ["Christmas"])
    repo.insert_transactions(
        conn,
        [("TXN-1", 1, None, "2026-01-05", "withdrawal", -1000, "Shop", None, True)],
    )
    repo.insert_splits(conn, [("SPL-1", 1, 1, -1000, "memo", 1)])
    repo.insert_transaction_tags(conn, [(1, 1), (1, 1)])
    repo.insert_split_tags(conn, [(1, 1)])

    assert repo.existing_tags(conn) == {"Christmas": 1}
    assert repo.existing_uids(conn, "spend_transaction") == {"TXN-1": 1}
    assert repo.existing_uids(conn, "transaction_split") == {"SPL-1": 1}
    assert conn.execute("SELECT is_split FROM spend_transaction").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM transaction_tag").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM transaction_split_tag").fetchone() == (1,)


def test_duplicate_uid_is_rejected(conn):
    repo.insert_accounts(conn, [ACCOUNT])
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_accounts(conn, [ACCOUNT])


def test_import_metadata(conn):
    assert repo.get_bound_budget_id(conn) is None

    repo.record_import(conn, "B-1", "2026-01-01T00:00:00+00:00")
    repo.record_import(conn, "B-1", "2026-02-01T00:00:00+00:00")

    assert repo.get_bound_budget_id(conn) == "B-1"
    assert conn.execute(
        "SELECT first_imported_at, last_imported_at FROM import_metadata"
    ).fetchone() == ("2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")

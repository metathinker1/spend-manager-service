"""Tests for spend_manager.db.schema (Functional Design §2.2 / §2.3)."""

from __future__ import annotations

import sqlite3

import pytest

from spend_manager.db.schema import MIGRATIONS, current_version, ensure_schema, open_db
from spend_manager.exceptions import SchemaVersionError

EXPECTED_TABLES = {
    "schema_version",
    "import_metadata",
    "account",
    "category",
    "tag",
    "spend_transaction",
    "transaction_split",
    "transaction_tag",
    "transaction_split_tag",
}


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "nested" / "sm.db")
    try:
        yield connection
    finally:
        connection.close()


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def test_open_db_creates_parent_dir_and_enables_foreign_keys(tmp_path, conn):
    assert (tmp_path / "nested" / "sm.db").exists()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_fresh_database_gets_latest_schema(conn):
    assert ensure_schema(conn) == MIGRATIONS[-1][0]
    assert _tables(conn) == EXPECTED_TABLES


def test_ensure_schema_is_idempotent(conn):
    ensure_schema(conn)
    ensure_schema(conn)
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert rows == [(v,) for v, _ in MIGRATIONS]


def test_newer_database_version_is_rejected(conn):
    ensure_schema(conn)
    conn.execute("INSERT INTO schema_version VALUES (99, '2099-01-01T00:00:00+00:00')")
    with pytest.raises(SchemaVersionError):
        ensure_schema(conn)


def test_current_version_is_zero_for_empty_database(conn):
    assert current_version(conn) == 0


def _seed(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    conn.execute("INSERT INTO account (id, name, type) VALUES (1, 'Checking', 'checking')")
    conn.execute("INSERT INTO category (id, name, type) VALUES (1, 'Food', 'expense')")


def test_split_transaction_cannot_have_category(conn):
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO spend_transaction (account_id, category_id, date, type, amount_cents,"
            " is_split) VALUES (1, 1, '2026-01-01', 'withdrawal', -100, 1)"
        )


def test_transaction_type_is_constrained(conn):
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO spend_transaction (account_id, date, type, amount_cents)"
            " VALUES (1, '2026-01-01', 'refund', 100)"
        )


def test_category_type_is_constrained(conn):
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO category (name, type) VALUES ('X', 'transfer')")


def test_foreign_keys_are_enforced(conn):
    _seed(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO spend_transaction (account_id, date, type, amount_cents)"
            " VALUES (42, '2026-01-01', 'deposit', 100)"
        )


def test_moneywell_uid_is_unique_but_nullable(conn):
    _seed(conn)
    conn.execute("INSERT INTO account (name, type) VALUES ('UI-created 1', 'checking')")
    conn.execute("INSERT INTO account (name, type) VALUES ('UI-created 2', 'checking')")
    conn.execute("INSERT INTO account (moneywell_uid, name, type) VALUES ('U1', 'A', 'checking')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO account (moneywell_uid, name, type) VALUES ('U1', 'B', 'checking')"
        )


def test_migration_applied_concurrently_is_not_reapplied(tmp_path, monkeypatch):
    # Simulates the race in which another connection applies v1 after this
    # connection read version 0 but before it acquired the write lock.
    path = tmp_path / "race.db"
    other = open_db(path)
    ensure_schema(other)
    other.close()

    conn = open_db(path)
    from spend_manager.db import schema

    real_current_version = schema.current_version
    stale_reads = [0]  # first read is stale; later reads see the real version

    def current_version(c):
        return stale_reads.pop() if stale_reads else real_current_version(c)

    monkeypatch.setattr(schema, "current_version", current_version)
    try:
        assert ensure_schema(conn) == MIGRATIONS[-1][0]
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == len(MIGRATIONS)
    finally:
        conn.close()


def test_v1_database_is_upgraded_to_v2(tmp_path, monkeypatch):
    from spend_manager.db import schema

    conn = open_db(tmp_path / "v1.db")
    try:
        monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:1])
        assert ensure_schema(conn) == 1
        conn.execute("INSERT INTO account (id, name, type) VALUES (1, 'Checking', 'checking')")
        monkeypatch.undo()

        assert ensure_schema(conn) == 2
        assert conn.execute("SELECT name FROM account").fetchall() == [("Checking",)]
        views = conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall()
        assert views == [("category_line",)]
    finally:
        conn.close()


def test_category_line_view_is_split_aware(conn):
    _seed(conn)
    conn.execute("INSERT INTO category (id, name, type) VALUES (2, 'Fuel', 'expense')")
    conn.executemany(
        "INSERT INTO spend_transaction (id, account_id, category_id, date, type, amount_cents,"
        " payee, memo, is_split) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, "2026-01-05", "withdrawal", -3109, "Grocer", "weekly", 0),
            (2, None, "2026-01-06", "withdrawal", -10000, "Costco", "parent memo", 1),
            (3, 1, "2026-01-07", "deposit", 4273, "Grocer", None, 0),  # refund
            (4, None, "2026-01-08", "withdrawal", -500, "Card payment", None, 0),
        ],
    )
    conn.executemany(
        "INSERT INTO transaction_split (id, spend_transaction_id, category_id, amount_cents,"
        " memo, sequence) VALUES (?, 2, ?, ?, ?, ?)",
        [(10, 1, -6000, "food", 1), (11, 2, -4000, "gas", 2)],
    )

    rows = conn.execute(
        "SELECT spend_transaction_id, transaction_split_id, date, category_id, payee, memo,"
        " amount_cents, amount FROM category_line ORDER BY date, transaction_split_id"
    ).fetchall()
    assert rows == [
        (1, None, "2026-01-05", 1, "Grocer", "weekly", -3109, -31.09),
        (2, 10, "2026-01-06", 1, "Costco", "food", -6000, -60.0),
        (2, 11, "2026-01-06", 2, "Costco", "gas", -4000, -40.0),
        (3, None, "2026-01-07", 1, "Grocer", None, 4273, 42.73),
        (4, None, "2026-01-08", None, "Card payment", None, -500, -5.0),
    ]

    totals = conn.execute(
        "SELECT category_id, printf('%.2f', SUM(amount_cents) / 100.0) FROM category_line"
        " GROUP BY category_id ORDER BY category_id"
    ).fetchall()
    # Groceries: -31.09 - 60.00 + 42.73 refund; split parent never counted.
    assert totals == [(None, "-5.00"), (1, "-48.36"), (2, "-40.00")]


def test_category_line_view_is_read_only(conn):
    ensure_schema(conn)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM category_line")


def test_open_db_rejects_a_directory_path(tmp_path):
    from spend_manager.exceptions import DatabasePathError

    with pytest.raises(DatabasePathError, match="must be a database file path, not a directory"):
        open_db(tmp_path)


def test_open_db_rejects_a_file_as_parent(tmp_path):
    from spend_manager.exceptions import DatabasePathError

    (tmp_path / "not_a_dir").write_text("x")
    with pytest.raises(DatabasePathError, match="parent is not a directory"):
        open_db(tmp_path / "not_a_dir" / "sm.db")


def test_read_schema_version_never_creates_tables(tmp_path):
    from spend_manager.db.schema import read_schema_version

    conn = open_db(tmp_path / "empty.db")
    try:
        assert read_schema_version(conn) == 0
        tables = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        assert tables == 0
    finally:
        conn.close()


def test_require_current_schema(tmp_path):
    from spend_manager.db.schema import require_current_schema, required_version
    from spend_manager.exceptions import SchemaOutdatedError

    conn = open_db(tmp_path / "sm.db")
    try:
        with pytest.raises(SchemaOutdatedError) as exc_info:
            require_current_schema(conn)
        assert "production schema is v0, code requires v" in str(exc_info.value)
        assert "spend-manager-db migrate --env production" in str(exc_info.value)
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0

        ensure_schema(conn)
        assert require_current_schema(conn) == required_version()

        conn.execute("INSERT INTO schema_version VALUES (99, 'x')")
        with pytest.raises(SchemaVersionError):
            require_current_schema(conn)
    finally:
        conn.close()


def test_read_schema_version_on_read_only_connection(tmp_path):
    from spend_manager.db.schema import read_schema_version

    path = tmp_path / "sm.db"
    conn = open_db(path)
    ensure_schema(conn)
    conn.close()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert read_schema_version(ro) == MIGRATIONS[-1][0]
    finally:
        ro.close()

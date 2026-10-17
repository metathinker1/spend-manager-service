"""Tests for spend_manager.moneywell_store.reader (Functional Design §1.3)."""

from __future__ import annotations

import sqlite3

import pytest
from moneywell_fixture import BUDGET_ID, default_store, write_store

from spend_manager.exceptions import InvalidSourceError, UnsupportedStoreError
from spend_manager.moneywell_store.reader import read_store


def test_reads_selected_entities(tmp_path):
    snapshot = read_store(write_store(default_store(), tmp_path))

    assert snapshot.budget_ids == [BUDGET_ID]
    assert [a.uid for a in snapshot.accounts] == ["ACC-1", "ACC-2"]
    assert snapshot.accounts[1].is_hidden is True
    assert snapshot.accounts[1].is_debt is False
    assert [b.name for b in snapshot.buckets] == ["Salary", "Groceries", "Auto: Fuel"]
    assert [t.name for t in snapshot.tags] == ["Christmas", "Haircut", " Christmas "]
    assert [t.uid for t in snapshot.transactions] == [
        "TXN-1",
        "TXN-2",
        "TXN-3",
        "TXN-4",
        "TXN-5",
        "TXN-6",
        "TXN-7",
    ]
    assert [s.uid for s in snapshot.splits] == ["SPL-1", "SPL-2", "SPL-3", "SPL-4", "SPL-5"]
    assert snapshot.splits[0].split_parent_pk == 5
    assert snapshot.tag_links == [(2, 3), (6, 2), (9, 1), (10, 1)]
    assert snapshot.non_transaction_activity_pks == frozenset({10})


def test_float_amount_is_returned_raw(tmp_path):
    snapshot = read_store(write_store(default_store(), tmp_path))
    txn2 = next(t for t in snapshot.transactions if t.uid == "TXN-2")
    assert isinstance(txn2.amount, float)


def test_sensitive_account_columns_are_not_read(tmp_path):
    snapshot = read_store(write_store(default_store(), tmp_path))
    rendered = repr(snapshot.accounts)
    assert "000123456789" not in rendered
    assert "4111111111111111" not in rendered
    assert "1234.56" not in rendered


def test_rows_only_in_wal_are_read(tmp_path):
    path = write_store(default_store(), tmp_path, wal=True)

    # Sanity: the main file alone has no tables — everything is in the WAL.
    main_only = tmp_path / "main_only"
    main_only.write_bytes(path.read_bytes())
    conn = sqlite3.connect(main_only)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    conn.close()

    assert len(read_store(path).transactions) == 7


def test_not_a_sqlite_file(tmp_path):
    path = tmp_path / "persistentStore"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(InvalidSourceError, match="not a SQLite"):
        read_store(path)


def _drop(path, sql: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(sql)
    conn.commit()
    conn.close()


def test_missing_primary_key_table(tmp_path):
    path = write_store(default_store(), tmp_path)
    _drop(path, "DROP TABLE Z_PRIMARYKEY")
    with pytest.raises(UnsupportedStoreError, match="Z_PRIMARYKEY"):
        read_store(path)


def test_missing_entity(tmp_path):
    path = write_store(default_store(), tmp_path)
    _drop(path, "DELETE FROM Z_PRIMARYKEY WHERE Z_NAME = 'Tag'")
    with pytest.raises(UnsupportedStoreError, match="Tag"):
        read_store(path)


def test_missing_table(tmp_path):
    path = write_store(default_store(), tmp_path)
    _drop(path, "DROP TABLE Z_3TAGS")
    with pytest.raises(UnsupportedStoreError, match="Z_3TAGS missing"):
        read_store(path)


def test_missing_column(tmp_path):
    path = write_store(default_store(), tmp_path)
    _drop(path, "ALTER TABLE ZACTIVITY DROP COLUMN ZBUCKET2")
    with pytest.raises(UnsupportedStoreError, match=r"ZACTIVITY\.ZBUCKET2 missing"):
        read_store(path)


def test_entity_numbers_are_discovered_not_hard_coded(tmp_path):
    path = write_store(default_store(), tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE Z_PRIMARYKEY SET Z_ENT = 70 WHERE Z_NAME = 'Transaction'")
    conn.execute("UPDATE ZACTIVITY SET Z_ENT = 70 WHERE Z_ENT = 7")
    conn.commit()
    conn.close()

    assert len(read_store(path).transactions) == 7

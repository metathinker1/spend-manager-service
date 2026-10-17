"""Tests for spend_manager.services.moneywell_store_import (FD §1.1, §1.4, §3.3, §3.4)."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest
from moneywell_fixture import activity, add_transaction, default_store, write_store

from spend_manager.config import Settings
from spend_manager.db import repository
from spend_manager.exceptions import (
    BudgetMismatchError,
    SplitConflictError,
    StoreValidationError,
)
from spend_manager.services.moneywell_store_import import EntityCount, import_store

FIRST_IMPORT = {
    "accounts": EntityCount(2, 0),
    "categories": EntityCount(3, 0),
    "tags": EntityCount(2, 0),
    "transactions": EntityCount(7, 0),
    "transaction_splits": EntityCount(5, 0),
    "transaction_tags": EntityCount(2, 0),
    "transaction_split_tags": EntityCount(1, 0),
}


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "MoneyWell_Data"
    settings = Settings(
        moneywell_store_root=str(root), spend_manager_db_path=str(tmp_path / "db" / "sm.db")
    )
    return root, settings


def _query(settings: Settings, sql: str) -> list[tuple]:
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _table_counts(settings: Settings) -> dict[str, int]:
    tables = [
        "account",
        "category",
        "tag",
        "spend_transaction",
        "transaction_split",
        "transaction_tag",
        "transaction_split_tag",
        "import_metadata",
    ]
    return {t: _query(settings, f"SELECT COUNT(*) FROM {t}")[0][0] for t in tables}


def test_first_import(env):
    root, settings = env
    write_store(default_store(), root / "Budget", wal=True)

    summary = import_store("Budget", settings)

    assert summary.counts == FIRST_IMPORT
    assert summary.warnings == [
        "1 non-transaction activities (favorites/scheduled) ignored",
        "1 tag link(s) on non-transaction activities ignored",
    ]
    rows = _query(
        settings,
        "SELECT t.moneywell_uid, a.moneywell_uid, c.name, t.date, t.type, t.amount_cents,"
        " t.is_split FROM spend_transaction t JOIN account a ON a.id = t.account_id"
        " LEFT JOIN category c ON c.id = t.category_id ORDER BY t.moneywell_uid",
    )
    assert rows[1] == ("TXN-2", "ACC-1", "Groceries", "2026-01-05", "withdrawal", -3109, 0)
    assert rows[4] == ("TXN-5", "ACC-2", None, "2026-01-10", "withdrawal", -10000, 1)
    assert rows[5] == ("TXN-6", "ACC-2", "Groceries", "2026-01-05", "deposit", 4273, 0)

    splits = _query(
        settings,
        "SELECT s.moneywell_uid, t.moneywell_uid, s.amount_cents FROM transaction_split s"
        " JOIN spend_transaction t ON t.id = s.spend_transaction_id ORDER BY s.moneywell_uid",
    )
    assert splits[:3] == [
        ("SPL-1", "TXN-5", -6000),
        ("SPL-2", "TXN-5", -3000),
        ("SPL-3", "TXN-5", -1000),
    ]
    split_tags = _query(
        settings,
        "SELECT s.moneywell_uid, g.name FROM transaction_split_tag x"
        " JOIN transaction_split s ON s.id = x.transaction_split_id JOIN tag g ON g.id = x.tag_id",
    )
    assert split_tags == [("SPL-1", "Haircut")]


def test_reimport_of_unchanged_store_inserts_nothing(env):
    root, settings = env
    write_store(default_store(), root / "Budget")
    import_store("Budget", settings)

    summary = import_store("Budget", settings)

    assert all(c.inserted == 0 for c in summary.counts.values())
    assert summary.counts["transactions"] == EntityCount(0, 7)
    assert summary.counts["transaction_split_tags"] == EntityCount(0, 1)


def test_reimport_adds_only_new_records_and_never_updates(env):
    root, settings = env
    store = default_store()
    write_store(store, root / "V1")
    import_store("V1", settings)

    # MoneyWell later: a rename, an amount edit, a new tag on an old
    # transaction (all ignored), and a brand-new split transaction with a tag.
    store["accounts"][0]["ZNAME"] = "Renamed Checking"
    activity(store, "TXN-1")["ZAMOUNT"] = 9999
    store["tags"].append({"Z_PK": 4, "ZNAME": "Vacation"})
    store["tag_links"] += [(1, 4)]
    add_transaction(store, 60, "TXN-60", ZAMOUNT=-50, ZDATEYMD=20260201)
    add_transaction(
        store, 61, "SPL-61", ZAMOUNT=-50, ZBUCKET2=2, ZSPLITPARENT=60, ZDATEYMD=20260201
    )
    store["tag_links"] += [(61, 4)]
    write_store(store, root / "V2")

    summary = import_store("V2", settings)

    assert summary.counts["accounts"] == EntityCount(0, 2)
    assert summary.counts["tags"] == EntityCount(1, 2)
    assert summary.counts["transactions"] == EntityCount(1, 7)
    assert summary.counts["transaction_splits"] == EntityCount(1, 5)
    assert summary.counts["transaction_tags"] == EntityCount(0, 3)
    assert summary.counts["transaction_split_tags"] == EntityCount(1, 1)
    assert _query(settings, "SELECT name FROM account WHERE moneywell_uid = 'ACC-1'") == [
        ("Checking",)
    ]
    assert _query(
        settings, "SELECT amount_cents FROM spend_transaction WHERE moneywell_uid = 'TXN-1'"
    ) == [(250000,)]


def test_records_created_in_ui_are_left_alone(env):
    root, settings = env
    write_store(default_store(), root / "Budget")
    import_store("Budget", settings)
    conn = sqlite3.connect(settings.db_path)
    conn.execute("INSERT INTO account (name, type) VALUES ('Cash', 'cash')")
    conn.commit()
    conn.close()

    import_store("Budget", settings)

    assert _query(settings, "SELECT COUNT(*) FROM account")[0][0] == 3


def test_budget_mismatch_is_rejected_and_nothing_written(env):
    root, settings = env
    write_store(default_store(), root / "Mine")
    import_store("Mine", settings)
    before = _table_counts(settings)

    other = default_store()
    other["budget_ids"] = ["OTHER-BUDGET"]
    add_transaction(other, 70, "TXN-70", ZAMOUNT=-1)
    write_store(other, root / "Other")

    with pytest.raises(BudgetMismatchError, match="OTHER-BUDGET"):
        import_store("Other", settings)
    assert _table_counts(settings) == before


def test_split_conflict_is_rejected_and_nothing_written(env):
    root, settings = env
    store = default_store()
    write_store(store, root / "V1")
    import_store("V1", settings)
    before = _table_counts(settings)

    # TXN-3 (imported unsplit) is split in MoneyWell afterwards.
    activity(store, "TXN-3")["ZBUCKET2"] = None
    add_transaction(
        store, 80, "SPL-80", ZAMOUNT=-500, ZBUCKET2=2, ZSPLITPARENT=3, ZDATEYMD=20260107
    )
    add_transaction(store, 81, "TXN-81", ZAMOUNT=-1)  # an otherwise-new transaction
    write_store(store, root / "V2")

    with pytest.raises(SplitConflictError, match="SPL-80 -> already-imported transaction TXN-3"):
        import_store("V2", settings)
    assert _table_counts(settings) == before


def test_validation_failure_writes_nothing(env):
    root, settings = env
    store = default_store()
    activity(store, "TXN-1")["ZDATEYMD"] = 0
    write_store(store, root / "Bad")

    with pytest.raises(StoreValidationError):
        import_store("Bad", settings)
    assert not settings.db_path.exists()


def test_failure_mid_write_rolls_back(env, monkeypatch):
    root, settings = env
    write_store(default_store(), root / "Budget")

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(repository, "insert_splits", boom)
    with pytest.raises(sqlite3.OperationalError):
        import_store("Budget", settings)

    counts = _table_counts(settings)
    assert all(n == 0 for n in counts.values()), counts


def test_source_is_not_modified(env):
    root, settings = env
    folder = root / "Budget" / "data"
    write_store(default_store(), folder, wal=True)
    digest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}

    import_store("Budget", settings)

    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()} == digest


def test_category_line_view_matches_imported_data(env):
    root, settings = env
    write_store(default_store(), root / "Budget")
    import_store("Budget", settings)

    # 7 transactions - 2 split parents + 5 split rows = 10 categorizable lines.
    assert _query(settings, "SELECT COUNT(*) FROM category_line") == [(10,)]
    totals = dict(
        _query(
            settings,
            "SELECT COALESCE(c.name, '(no bucket)'), printf('%.2f', SUM(l.amount_cents) / 100.0)"
            " FROM category_line l LEFT JOIN category c ON c.id = l.category_id GROUP BY 1",
        )
    )
    assert totals == {
        "Salary": "2500.00",
        # -31.09 - 120 (check) - 60 (split) + 42.73 (refund) - 25 + 5 (split refund)
        "Groceries": "-188.36",
        "Auto: Fuel": "-30.00",
        "(no bucket)": "-510.00",  # card payment + uncategorized split row
    }
    assert _query(
        settings, "SELECT printf('%.2f', SUM(amount_cents) / 100.0) FROM category_line"
    ) == [("1771.64",)]

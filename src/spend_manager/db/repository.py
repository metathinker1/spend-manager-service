"""Data access for the Spend Manager database.

Thin, parameterized SQL helpers used by the MoneyWell store import
(Functional Design §1.4 / §1.5 / §3.3). Callers own the transaction.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from spend_manager.moneywell_store.convert import NewAccount, NewCategory

# Tables whose rows carry a MoneyWell unique id. Whitelisted because a table
# name cannot be a bound parameter.
UID_TABLES = frozenset({"account", "category", "spend_transaction", "transaction_split"})


def existing_uids(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    """``moneywell_uid -> id`` for every row of ``table`` that has a uid."""
    if table not in UID_TABLES:
        raise ValueError(f"not a moneywell_uid table: {table}")
    rows = conn.execute(f"SELECT moneywell_uid, id FROM {table} WHERE moneywell_uid IS NOT NULL")
    return dict(rows.fetchall())


def existing_tags(conn: sqlite3.Connection) -> dict[str, int]:
    """``name -> id`` for every tag."""
    return dict(conn.execute("SELECT name, id FROM tag").fetchall())


def insert_accounts(conn: sqlite3.Connection, accounts: Iterable[NewAccount]) -> None:
    conn.executemany(
        "INSERT INTO account (moneywell_uid, name, type, currency_code, is_hidden, is_debt,"
        " sequence, memo) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                a.uid,
                a.name,
                a.type,
                a.currency_code,
                int(a.is_hidden),
                int(a.is_debt),
                a.sequence,
                a.memo,
            )
            for a in accounts
        ],
    )


def insert_categories(conn: sqlite3.Connection, categories: Iterable[NewCategory]) -> None:
    conn.executemany(
        "INSERT INTO category (moneywell_uid, name, type, is_hidden, sequence, memo,"
        " currency_code) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (c.uid, c.name, c.type, int(c.is_hidden), c.sequence, c.memo, c.currency_code)
            for c in categories
        ],
    )


def insert_tags(conn: sqlite3.Connection, names: Iterable[str]) -> None:
    conn.executemany("INSERT INTO tag (name) VALUES (?)", [(n,) for n in names])


def insert_transactions(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[str, int, int | None, str, str, int, str | None, str | None, bool]],
) -> None:
    """Rows: (uid, account_id, category_id, date, type, amount_cents, payee, memo, is_split)."""
    conn.executemany(
        "INSERT INTO spend_transaction (moneywell_uid, account_id, category_id, date, type,"
        " amount_cents, payee, memo, is_split) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(*row[:8], int(row[8])) for row in rows],
    )


def insert_splits(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[str, int, int | None, int, str | None, int | None]],
) -> None:
    """Rows: (uid, spend_transaction_id, category_id, amount_cents, memo, sequence)."""
    conn.executemany(
        "INSERT INTO transaction_split (moneywell_uid, spend_transaction_id, category_id,"
        " amount_cents, memo, sequence) VALUES (?, ?, ?, ?, ?, ?)",
        list(rows),
    )


def insert_transaction_tags(conn: sqlite3.Connection, pairs: Iterable[tuple[int, int]]) -> None:
    """Pairs: (spend_transaction_id, tag_id); duplicates are ignored."""
    conn.executemany(
        "INSERT OR IGNORE INTO transaction_tag (spend_transaction_id, tag_id) VALUES (?, ?)",
        list(pairs),
    )


def insert_split_tags(conn: sqlite3.Connection, pairs: Iterable[tuple[int, int]]) -> None:
    """Pairs: (transaction_split_id, tag_id); duplicates are ignored."""
    conn.executemany(
        "INSERT OR IGNORE INTO transaction_split_tag (transaction_split_id, tag_id) VALUES (?, ?)",
        list(pairs),
    )


def get_bound_budget_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT moneywell_budget_id FROM import_metadata WHERE id = 1").fetchone()
    return row[0] if row else None


def record_import(conn: sqlite3.Connection, budget_id: str, now: str) -> None:
    """Bind the DB to ``budget_id`` on first import; bump ``last_imported_at`` afterwards."""
    conn.execute(
        "INSERT INTO import_metadata (id, moneywell_budget_id, first_imported_at,"
        " last_imported_at) VALUES (1, ?, ?, ?)"
        " ON CONFLICT (id) DO UPDATE SET last_imported_at = excluded.last_imported_at",
        (budget_id, now, now),
    )

"""Performance smoke test for the read API (Requirements: sub-second responses).

About 10× today's data (~45k lines). Limits are generous to avoid flaky
failures; the point is to catch a query that stops using its indexes.
"""

from __future__ import annotations

import datetime as dt
import random
import time

import pytest

from spend_manager.api.models import TransactionFilter
from spend_manager.db.schema import ensure_schema, open_db, open_read_db
from spend_manager.services.reports import (
    category_by_month,
    last_12_months,
    resolve_period,
    spending_by_category,
)
from spend_manager.services.transactions_query import get_transaction_page

LIMIT_SECONDS = 1.0
TRANSACTIONS = 40_000
SPLIT_TRANSACTIONS = 2_500  # two splits each → 45,000 lines in total
CATEGORIES = 66


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    path = tmp_path_factory.mktemp("perf") / "sm.db"
    rng = random.Random(42)
    start = dt.date(2013, 9, 1).toordinal()
    span = dt.date(2026, 8, 21).toordinal() - start
    writer = open_db(path)
    ensure_schema(writer)
    writer.execute("BEGIN")
    writer.executemany(
        "INSERT INTO account (id, name, type) VALUES (?, ?, 'checking')",
        [(i, f"Account {i}") for i in range(1, 5)],
    )
    writer.executemany(
        "INSERT INTO category (id, name, type) VALUES (?, ?, 'expense')",
        [(i, f"Category {i}") for i in range(1, CATEGORIES + 1)],
    )
    writer.execute("INSERT INTO category (id, name, type) VALUES (100, 'Transfer', 'expense')")
    writer.execute("INSERT INTO category (id, name, type) VALUES (101, 'Salary', 'income')")
    category_choices = [*range(1, CATEGORIES + 1), 100, 101, None, None, None]

    def date():
        return dt.date.fromordinal(start + rng.randrange(span)).isoformat()

    writer.executemany(
        "INSERT INTO spend_transaction (id, account_id, category_id, date, type, amount_cents,"
        " payee, memo, is_split) VALUES (?, ?, ?, ?, 'withdrawal', ?, ?, ?, ?)",
        [
            (
                i,
                rng.randint(1, 4),
                None if i > TRANSACTIONS else rng.choice(category_choices),
                date(),
                -rng.randint(100, 50_000),
                f"PAYEE {rng.randint(1, 2000)}",
                f"memo {i}" if i % 3 == 0 else None,
                1 if i > TRANSACTIONS else 0,
            )
            for i in range(1, TRANSACTIONS + SPLIT_TRANSACTIONS + 1)
        ],
    )
    writer.executemany(
        "INSERT INTO transaction_split (spend_transaction_id, category_id, amount_cents, memo,"
        " sequence) VALUES (?, ?, ?, ?, ?)",
        [
            (i, rng.choice(category_choices), -rng.randint(100, 20_000), None, seq)
            for i in range(TRANSACTIONS + 1, TRANSACTIONS + SPLIT_TRANSACTIONS + 1)
            for seq in (1, 2)
        ],
    )
    writer.execute("COMMIT")
    writer.close()
    connection = open_read_db(path)
    yield connection
    connection.close()


def timed(fn):
    started = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - started


def test_data_size(conn):
    assert conn.execute("SELECT COUNT(*) FROM category_line").fetchone()[0] == 45_000


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"page": 400},
        {"date_from": "2025-01-01", "date_to": "2025-12-31"},
        {"category_id": [1, 2, 3], "uncategorized": True},
        {"account_id": [2], "q": "payee 12", "include_transfers": True},
    ],
    ids=["default", "deep-page", "year", "categories", "account-search"],
)
def test_transaction_page(conn, params):
    result, seconds = timed(lambda: get_transaction_page(conn, TransactionFilter(**params)))

    assert result.total_transactions > 0
    assert seconds < LIMIT_SECONDS


def test_spending_by_category(conn):
    report, seconds = timed(lambda: spending_by_category(conn, resolve_period(2025)))

    assert report.rows
    assert seconds < LIMIT_SECONDS


def test_category_by_month(conn):
    report, seconds = timed(lambda: category_by_month(conn, last_12_months(dt.date(2026, 8, 21))))

    assert report.rows
    assert seconds < LIMIT_SECONDS

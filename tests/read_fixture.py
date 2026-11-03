"""A small schema-v2 database for the read API tests.

Lines (``category_line``), by id — T = transaction, S = split:

    T101 2026-01-05 acct 1  Groceries      -5000  Safeway
    T102 2026-01-20 acct 2  Dining         -2500  Cafe Rio, memo "lunch with team"
    T111 2026-01-20 acct 1  Dining         -1000  Taco Stand (same date as T102)
    T103 2026-01-31 acct 1  Salary (inc) +300000  ACME Payroll
    T104 2026-02-03 acct 1  (none)         -1234  payee "=HYPERLINK(x)", memo "@risk"
    T105 2026-02-10 acct 1  Transfer     -100000  Online Transfer to Savings
    T106 2026-02-14 acct 2  split          -12000 COSTCO, memo "big shop"
      S201                  Groceries       -8000
      S202                  Household       -4000  memo "paper towels"
    T107 2026-03-01 acct 1  split          -50000 Bank 100% Match
      S203                  Transfer       -45000  memo "to savings"
      S204                  (none)          -5000
    T108 2026-03-15 acct 2  Groceries      +1500  Safeway, memo "refund"
    T109 2025-12-28 acct 1  Household      -7000  Target
    T110 2025-06-01 acct 1  Groceries       -999  (no payee), type check
"""

from __future__ import annotations

from pathlib import Path

from spend_manager.db.schema import ensure_schema, open_db

CHECKING, CARD = 1, 2
GROCERIES, HOUSEHOLD, DINING, TRANSFER, SALARY = 10, 11, 12, 13, 20
EXPENSE_CATEGORIES = (GROCERIES, HOUSEHOLD, DINING, TRANSFER)

ACCOUNTS = [
    # id, name, type, is_hidden, sequence
    (CHECKING, "Checking", "checking", 0, 1),
    (CARD, "Card", "credit_card", 0, 2),
]
CATEGORIES = [
    # id, name, type, is_hidden, sequence
    (GROCERIES, "Groceries", "expense", 0, 1),
    (HOUSEHOLD, "Household", "expense", 0, 2),
    (DINING, "Dining", "expense", 1, 3),
    (TRANSFER, "Transfer", "expense", 0, 4),
    (SALARY, "Salary", "income", 0, 1),
]
TRANSACTIONS = [
    # id, account, category, date, type, amount_cents, payee, memo, is_split
    (101, CHECKING, GROCERIES, "2026-01-05", "withdrawal", -5000, "Safeway", None, 0),
    (102, CARD, DINING, "2026-01-20", "withdrawal", -2500, "Cafe Rio", "lunch with team", 0),
    (111, CHECKING, DINING, "2026-01-20", "withdrawal", -1000, "Taco Stand", None, 0),
    (103, CHECKING, SALARY, "2026-01-31", "deposit", 300000, "ACME Payroll", None, 0),
    (104, CHECKING, None, "2026-02-03", "withdrawal", -1234, "=HYPERLINK(x)", "@risk", 0),
    (
        105,
        CHECKING,
        TRANSFER,
        "2026-02-10",
        "withdrawal",
        -100000,
        "Online Transfer to Savings",
        None,
        0,
    ),
    (106, CARD, None, "2026-02-14", "withdrawal", -12000, "COSTCO", "big shop", 1),
    (107, CHECKING, None, "2026-03-01", "withdrawal", -50000, "Bank 100% Match", None, 1),
    (108, CARD, GROCERIES, "2026-03-15", "deposit", 1500, "Safeway", "refund", 0),
    (109, CHECKING, HOUSEHOLD, "2025-12-28", "withdrawal", -7000, "Target", None, 0),
    (110, CHECKING, GROCERIES, "2025-06-01", "check", -999, None, None, 0),
]
SPLITS = [
    # id, transaction, category, amount_cents, memo, sequence
    (201, 106, GROCERIES, -8000, None, 1),
    (202, 106, HOUSEHOLD, -4000, "paper towels", 2),
    (203, 107, TRANSFER, -45000, "to savings", 1),
    (204, 107, None, -5000, None, 2),
]


def build_read_db(path: Path) -> Path:
    """Create the database above at ``path`` (schema at the current version)."""
    conn = open_db(path)
    try:
        ensure_schema(conn)
        conn.execute("BEGIN")
        conn.executemany(
            "INSERT INTO account (id, name, type, is_hidden, sequence) VALUES (?, ?, ?, ?, ?)",
            ACCOUNTS,
        )
        conn.executemany(
            "INSERT INTO category (id, name, type, is_hidden, sequence) VALUES (?, ?, ?, ?, ?)",
            CATEGORIES,
        )
        conn.executemany(
            "INSERT INTO spend_transaction (id, account_id, category_id, date, type, amount_cents,"
            " payee, memo, is_split) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            TRANSACTIONS,
        )
        conn.executemany(
            "INSERT INTO transaction_split (id, spend_transaction_id, category_id, amount_cents,"
            " memo, sequence) VALUES (?, ?, ?, ?, ?, ?)",
            SPLITS,
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    return path

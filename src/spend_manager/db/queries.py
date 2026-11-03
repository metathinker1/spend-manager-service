"""Read-only SQL for the read API (2026-10-31-spend-manager-ui Functional Design §2, §3).

Every query reads ``category_line`` (one row per non-split transaction or
split, never a split parent) through ``LINE_FROM`` and filters it with
``line_predicate`` — the single definition of which lines match a filter
(BR-1). The transaction page, its summary, the CSV export and the reports all
use it, so a report cell always equals the total of its drill-down list.

All values are bound as ``?`` parameters; WHERE clauses are assembled only
from the fixed fragments below. Functions return ``sqlite3.Row`` objects
(see ``open_read_db``) whose column names match the API model fields.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

LINE_FROM = """
FROM category_line l
JOIN spend_transaction t ON t.id = l.spend_transaction_id
LEFT JOIN category c ON c.id = l.category_id
"""

# A line is a transfer when its own category is named "Transfer" (CR-2).
IS_TRANSFER = "(CASE WHEN lower(c.name) = 'transfer' THEN 1 ELSE 0 END)"

_CSV_FETCH_SIZE = 500


@dataclass(frozen=True)
class LineFilter:
    """Which lines match (BR-1). Empty tuples / ``None`` mean "no condition"."""

    date_from: dt.date | None = None
    date_to: dt.date | None = None
    account_ids: tuple[int, ...] = ()
    category_ids: tuple[int, ...] = ()
    uncategorized: bool = False
    include_transfers: bool = False
    q: str | None = None


def line_predicate(f: LineFilter) -> tuple[str, list[object]]:
    """SQL condition over ``LINE_FROM`` aliases plus its parameters (BR-1)."""
    clauses: list[str] = []
    params: list[object] = []
    if f.date_from is not None:
        clauses.append("l.date >= ?")
        params.append(f.date_from.isoformat())
    if f.date_to is not None:
        clauses.append("l.date <= ?")
        params.append(f.date_to.isoformat())
    if f.account_ids:
        clauses.append(f"l.account_id IN ({_placeholders(f.account_ids)})")
        params.extend(f.account_ids)
    category_parts: list[str] = []
    if f.category_ids:
        category_parts.append(f"l.category_id IN ({_placeholders(f.category_ids)})")
        params.extend(f.category_ids)
    if f.uncategorized:
        category_parts.append("l.category_id IS NULL")
    if category_parts:
        clauses.append("(" + " OR ".join(category_parts) + ")")
    if not f.include_transfers:
        clauses.append(f"{IS_TRANSFER} = 0")
    if f.q:
        # instr, not LIKE: '%' and '_' in the search text are literal.
        clauses.append(
            "(instr(lower(t.payee), lower(?)) > 0"
            " OR instr(lower(t.memo), lower(?)) > 0"
            " OR instr(lower(l.memo), lower(?)) > 0)"
        )
        params.extend([f.q, f.q, f.q])
    return (" AND ".join(clauses) or "1 = 1"), params


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """Run several queries against one consistent view of the database."""
    conn.execute("BEGIN")
    try:
        yield
    finally:
        conn.execute("COMMIT")


def count_matching_transactions(conn: sqlite3.Connection, f: LineFilter) -> int:
    where, params = line_predicate(f)
    sql = f"SELECT COUNT(DISTINCT l.spend_transaction_id) {LINE_FROM} WHERE {where}"
    return conn.execute(sql, params).fetchone()[0]


def matching_transaction_ids(
    conn: sqlite3.Connection, f: LineFilter, limit: int, offset: int
) -> list[int]:
    """Ids of transactions with at least one matching line, newest first."""
    where, params = line_predicate(f)
    sql = f"""
        SELECT o.id FROM spend_transaction o
        WHERE o.id IN (SELECT l.spend_transaction_id {LINE_FROM} WHERE {where})
        ORDER BY o.date DESC, o.id DESC
        LIMIT ? OFFSET ?
    """
    return [row[0] for row in conn.execute(sql, [*params, limit, offset])]


def load_transactions(conn: sqlite3.Connection, ids: Sequence[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    sql = f"""
        SELECT t.id, t.date, t.account_id, a.name AS account_name, t.type, t.payee, t.memo,
               t.amount_cents, t.category_id, c.name AS category_name, t.is_split,
               (CASE WHEN t.is_split = 0 AND lower(c.name) = 'transfer' THEN 1 ELSE 0 END)
                   AS is_transfer
        FROM spend_transaction t
        JOIN account a ON a.id = t.account_id
        LEFT JOIN category c ON c.id = t.category_id
        WHERE t.id IN ({_placeholders(ids)})
        ORDER BY t.date DESC, t.id DESC
    """
    return conn.execute(sql, list(ids)).fetchall()


def load_splits(
    conn: sqlite3.Connection, transaction_ids: Sequence[int], f: LineFilter
) -> list[sqlite3.Row]:
    """All splits of the given transactions; ``matches`` = the split line matches ``f``."""
    if not transaction_ids:
        return []
    where, params = line_predicate(f)
    sql = f"""
        SELECT l.transaction_split_id AS id, l.spend_transaction_id, l.category_id,
               c.name AS category_name, l.memo, l.amount_cents,
               {IS_TRANSFER} AS is_transfer,
               (CASE WHEN {where} THEN 1 ELSE 0 END) AS matches
        {LINE_FROM}
        JOIN transaction_split s ON s.id = l.transaction_split_id
        WHERE l.spend_transaction_id IN ({_placeholders(transaction_ids)})
        ORDER BY l.spend_transaction_id, s.sequence, s.id
    """
    return conn.execute(sql, [*params, *transaction_ids]).fetchall()


def summarize_matching_lines(conn: sqlite3.Connection, f: LineFilter) -> tuple[int, int]:
    """(count, signed total in cents) of the matching lines."""
    where, params = line_predicate(f)
    sql = f"SELECT COUNT(*), COALESCE(SUM(l.amount_cents), 0) {LINE_FROM} WHERE {where}"
    count, total = conn.execute(sql, params).fetchone()
    return count, total


def iter_matching_lines(conn: sqlite3.Connection, f: LineFilter) -> Iterator[sqlite3.Row]:
    """Every matching line for the CSV export (BR-5), in list order."""
    where, params = line_predicate(f)
    sql = f"""
        SELECT l.date, a.name AS account, t.payee, t.type, c.name AS category, l.memo,
               l.amount_cents, l.spend_transaction_id AS transaction_id,
               l.transaction_split_id AS split_id, {IS_TRANSFER} AS is_transfer
        {LINE_FROM}
        JOIN account a ON a.id = l.account_id
        LEFT JOIN transaction_split s ON s.id = l.transaction_split_id
        WHERE {where}
        ORDER BY l.date DESC, l.spend_transaction_id DESC, s.sequence, s.id
    """
    cursor = conn.execute(sql, params)
    try:
        while rows := cursor.fetchmany(_CSV_FETCH_SIZE):
            yield from rows
    finally:
        cursor.close()


def spending_rows(
    conn: sqlite3.Connection, date_from: dt.date, date_to: dt.date, *, by_month: bool = False
) -> list[sqlite3.Row]:
    """Spending (−SUM of cents) per expense category or uncategorized (BR-3).

    Uses BR-1 with transfers excluded; income-category lines are left out.
    With ``by_month`` there is one row per category and ``YYYY-MM`` month.
    """
    where, params = line_predicate(LineFilter(date_from=date_from, date_to=date_to))
    month_column = ", substr(l.date, 1, 7) AS month" if by_month else ""
    month_group = ", substr(l.date, 1, 7)" if by_month else ""
    sql = f"""
        SELECT l.category_id, c.name AS category_name{month_column},
               -SUM(l.amount_cents) AS spending_cents, COUNT(*) AS line_count
        {LINE_FROM}
        WHERE {where} AND (l.category_id IS NULL OR c.type = 'expense')
        GROUP BY l.category_id{month_group}
    """
    return conn.execute(sql, params).fetchall()


def list_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, type, is_hidden FROM account ORDER BY sequence, name, id"
    ).fetchall()


def list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, type, is_hidden FROM category ORDER BY type, sequence, name, id"
    ).fetchall()


def _placeholders(values: Sequence[object]) -> str:
    return ", ".join("?" * len(values))

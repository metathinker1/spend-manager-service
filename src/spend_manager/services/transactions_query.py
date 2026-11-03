"""Transaction list and CSV lines for the read API (Functional Design §2.2, §2.5, §3.1)."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterator

from spend_manager.api.models import (
    LineFilterParams,
    SplitRow,
    TransactionFilter,
    TransactionPage,
    TransactionRow,
)
from spend_manager.db import queries


def get_transaction_page(conn: sqlite3.Connection, f: TransactionFilter) -> TransactionPage:
    """One page of transactions with at least one matching line, plus the line summary.

    Split transactions come with all their splits, each flagged ``matches``.
    """
    lines = f.to_line_filter()
    with queries.read_snapshot(conn):
        total = queries.count_matching_transactions(conn, lines)
        ids = queries.matching_transaction_ids(
            conn, lines, limit=f.page_size, offset=(f.page - 1) * f.page_size
        )
        splits: defaultdict[int, list[SplitRow]] = defaultdict(list)
        for row in queries.load_splits(conn, ids, lines):
            splits[row["spend_transaction_id"]].append(SplitRow.model_validate(dict(row)))
        items = [
            TransactionRow.model_validate({**row, "matches": True, "splits": splits[row["id"]]})
            for row in map(dict, queries.load_transactions(conn, ids))
        ]
        line_count, total_cents = queries.summarize_matching_lines(conn, lines)
    return TransactionPage(
        items=items,
        page=f.page,
        page_size=f.page_size,
        total_transactions=total,
        matching_line_count=line_count,
        matching_total_cents=total_cents,
    )


def iter_csv_lines(conn: sqlite3.Connection, f: LineFilterParams) -> Iterator[sqlite3.Row]:
    """Every matching line, unpaged, in list order (BR-5)."""
    return queries.iter_matching_lines(conn, f.to_line_filter())

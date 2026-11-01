"""Expense reports (Functional Design §2.3 BR-3, §3.2).

Spending is ``−SUM(amount_cents)`` over expense and uncategorized lines with
transfers excluded, so refunds reduce it. All arithmetic is on integer cents.
"""

from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
from collections import defaultdict

from spend_manager.api.models import (
    CategoryByMonthReport,
    MatrixRow,
    Period,
    SpendingByCategoryReport,
    SpendingRow,
)
from spend_manager.db import queries

UNCATEGORIZED = "Uncategorized"


def resolve_period(year: int, month: int | None = None) -> Period:
    """A calendar month, or the calendar year when ``month`` is ``None``."""
    if month is not None:
        last_day = calendar.monthrange(year, month)[1]
        label = f"{year:04d}-{month:02d}"
        return Period(
            kind="month",
            date_from=dt.date(year, month, 1),
            date_to=dt.date(year, month, last_day),
            label=label,
            months=[label],
        )
    return Period(
        kind="year",
        date_from=dt.date(year, 1, 1),
        date_to=dt.date(year, 12, 31),
        label=f"{year:04d}",
        months=_months(year, 1, 12),
    )


def last_12_months(today: dt.date) -> Period:
    """The month of ``today`` and the 11 months before it."""
    index = today.year * 12 + today.month - 1 - 11
    start_year, start_month = divmod(index, 12)
    months = _months(start_year, start_month + 1, 12)
    last_day = calendar.monthrange(today.year, today.month)[1]
    return Period(
        kind="last12",
        date_from=dt.date(start_year, start_month + 1, 1),
        date_to=dt.date(today.year, today.month, last_day),
        label=f"{months[0]} to {months[-1]}",
        months=months,
    )


def spending_by_category(conn: sqlite3.Connection, period: Period) -> SpendingByCategoryReport:
    rows = [
        SpendingRow(
            category_id=row["category_id"],
            category_name=row["category_name"] or UNCATEGORIZED,
            spending_cents=row["spending_cents"],
            line_count=row["line_count"],
        )
        for row in queries.spending_rows(conn, period.date_from, period.date_to)
    ]
    rows.sort(key=lambda r: _row_order(r.category_id, r.category_name, r.spending_cents))
    return SpendingByCategoryReport(
        period=period, rows=rows, total_cents=sum(r.spending_cents for r in rows)
    )


def category_by_month(conn: sqlite3.Connection, period: Period) -> CategoryByMonthReport:
    column = {month: i for i, month in enumerate(period.months)}
    cells: defaultdict[int | None, list[int]] = defaultdict(lambda: [0] * len(period.months))
    names: dict[int | None, str] = {}
    for row in queries.spending_rows(conn, period.date_from, period.date_to, by_month=True):
        category_id = row["category_id"]
        cells[category_id][column[row["month"]]] += row["spending_cents"]
        names[category_id] = row["category_name"] or UNCATEGORIZED
    rows = [
        MatrixRow(
            category_id=category_id,
            category_name=names[category_id],
            cells_cents=values,
            total_cents=sum(values),
        )
        for category_id, values in cells.items()
    ]
    rows.sort(key=lambda r: _row_order(r.category_id, r.category_name, r.total_cents))
    column_totals = [sum(r.cells_cents[i] for r in rows) for i in range(len(period.months))]
    return CategoryByMonthReport(
        period=period,
        months=period.months,
        rows=rows,
        column_totals_cents=column_totals,
        grand_total_cents=sum(column_totals),
    )


def _row_order(category_id: int | None, name: str, cents: int) -> tuple[bool, int, str, int]:
    # Highest spending first, then name; the Uncategorized row always last.
    return (category_id is None, -cents, name.casefold(), category_id or 0)


def _months(start_year: int, start_month: int, count: int) -> list[str]:
    index = start_year * 12 + start_month - 1
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(index, index + count)]

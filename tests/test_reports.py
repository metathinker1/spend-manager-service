"""Expense reports (2026-10-31-spend-manager-ui FD §2.3 BR-3)."""

from __future__ import annotations

import datetime as dt

import pytest
from read_fixture import DINING, GROCERIES, HOUSEHOLD, build_read_db

from spend_manager.api.models import TransactionFilter
from spend_manager.db import queries
from spend_manager.db.schema import open_read_db
from spend_manager.services.reports import (
    category_by_month,
    last_12_months,
    resolve_period,
    spending_by_category,
)
from spend_manager.services.transactions_query import get_transaction_page

TODAY = dt.date(2026, 3, 20)


@pytest.fixture
def conn(tmp_path):
    connection = open_read_db(build_read_db(tmp_path / "sm.db"))
    try:
        yield connection
    finally:
        connection.close()


def rows(report):
    return [(r.category_id, r.category_name, r.spending_cents, r.line_count) for r in report.rows]


class TestPeriods:
    def test_month(self):
        period = resolve_period(2024, 2)

        assert (period.kind, period.label, period.months) == ("month", "2024-02", ["2024-02"])
        assert (period.date_from, period.date_to) == (dt.date(2024, 2, 1), dt.date(2024, 2, 29))

    def test_year(self):
        period = resolve_period(2025)

        assert (period.kind, period.label) == ("year", "2025")
        assert (period.date_from, period.date_to) == (dt.date(2025, 1, 1), dt.date(2025, 12, 31))
        assert period.months == [f"2025-{m:02d}" for m in range(1, 13)]

    @pytest.mark.parametrize(
        ("today", "first", "last", "date_to"),
        [
            (dt.date(2026, 3, 20), "2025-04", "2026-03", dt.date(2026, 3, 31)),
            (dt.date(2026, 1, 1), "2025-02", "2026-01", dt.date(2026, 1, 31)),
            (dt.date(2025, 12, 31), "2025-01", "2025-12", dt.date(2025, 12, 31)),
            (dt.date(2024, 2, 10), "2023-03", "2024-02", dt.date(2024, 2, 29)),
        ],
    )
    def test_last_12_months(self, today, first, last, date_to):
        period = last_12_months(today)

        assert period.kind == "last12"
        assert len(period.months) == 12
        assert (period.months[0], period.months[-1]) == (first, last)
        assert period.date_from == dt.date.fromisoformat(first + "-01")
        assert period.date_to == date_to
        assert period.label == f"{first} to {last}"


def test_spending_by_category_year(conn):
    report = spending_by_category(conn, resolve_period(2026))

    # Income (Salary) and transfers (T105, S203) are excluded; the refund
    # T108 reduces Groceries; Uncategorized is last.
    assert rows(report) == [
        (GROCERIES, "Groceries", 11500, 3),
        (HOUSEHOLD, "Household", 4000, 1),
        (DINING, "Dining", 3500, 2),
        (None, "Uncategorized", 6234, 2),
    ]
    assert report.total_cents == 25234


def test_spending_can_be_negative(conn):
    report = spending_by_category(conn, resolve_period(2026, 3))

    assert rows(report) == [(GROCERIES, "Groceries", -1500, 1), (None, "Uncategorized", 5000, 1)]
    assert report.total_cents == 3500


def test_empty_period(conn):
    report = spending_by_category(conn, resolve_period(2030))

    assert report.rows == []
    assert report.total_cents == 0


def test_category_by_month_last12(conn):
    report = category_by_month(conn, last_12_months(TODAY))
    months = report.months

    def cells(**by_month):
        return [by_month.get("m" + m.replace("-", "_"), 0) for m in months]

    assert months == report.period.months
    assert [(r.category_id, r.cells_cents, r.total_cents) for r in report.rows] == [
        (GROCERIES, cells(m2025_06=999, m2026_01=5000, m2026_02=8000, m2026_03=-1500), 12499),
        (HOUSEHOLD, cells(m2025_12=7000, m2026_02=4000), 11000),
        (DINING, cells(m2026_01=3500), 3500),
        (None, cells(m2026_02=1234, m2026_03=5000), 6234),
    ]
    assert report.rows[-1].category_name == "Uncategorized"
    assert report.column_totals_cents == cells(
        m2025_06=999, m2025_12=7000, m2026_01=8500, m2026_02=13234, m2026_03=3500
    )
    assert report.grand_total_cents == 33233


def test_category_by_month_year_orders_by_total(conn):
    report = category_by_month(conn, resolve_period(2025))

    assert [(r.category_name, r.total_cents) for r in report.rows] == [
        ("Household", 7000),
        ("Groceries", 999),
    ]
    assert report.grand_total_cents == 7999


PERIODS = [
    resolve_period(2025),
    resolve_period(2026),
    resolve_period(2026, 1),
    resolve_period(2026, 2),
    resolve_period(2026, 3),
    last_12_months(TODAY),
]


def drill_down_total(conn, category_id, date_from, date_to):
    if category_id is None:
        f = TransactionFilter(uncategorized=True, date_from=date_from, date_to=date_to)
    else:
        f = TransactionFilter(category_id=[category_id], date_from=date_from, date_to=date_to)
    return -get_transaction_page(conn, f).matching_total_cents


def all_spending_filter(conn, date_from, date_to):
    # What the UI sends for a column or grand total: every expense category
    # except Transfer, plus uncategorized.
    expense_ids = [
        row["id"]
        for row in queries.list_categories(conn)
        if row["type"] == "expense" and row["name"].lower() != "transfer"
    ]
    return TransactionFilter(
        category_id=expense_ids, uncategorized=True, date_from=date_from, date_to=date_to
    )


def month_range(month):
    year, number = (int(part) for part in month.split("-"))
    period = resolve_period(year, number)
    return period.date_from, period.date_to


@pytest.mark.parametrize("period", PERIODS, ids=lambda p: p.label)
def test_every_report_value_equals_its_drill_down_total(conn, period):
    spending = spending_by_category(conn, period)
    for row in spending.rows:
        expected = drill_down_total(conn, row.category_id, period.date_from, period.date_to)
        assert row.spending_cents == expected, row
    total_filter = all_spending_filter(conn, period.date_from, period.date_to)
    assert spending.total_cents == -get_transaction_page(conn, total_filter).matching_total_cents

    matrix = category_by_month(conn, period)
    for row in matrix.rows:
        for month, cell in zip(matrix.months, row.cells_cents, strict=True):
            assert cell == drill_down_total(conn, row.category_id, *month_range(month)), (
                row,
                month,
            )
        expected = drill_down_total(conn, row.category_id, period.date_from, period.date_to)
        assert row.total_cents == expected
    for month, total in zip(matrix.months, matrix.column_totals_cents, strict=True):
        column = get_transaction_page(conn, all_spending_filter(conn, *month_range(month)))
        assert total == -column.matching_total_cents
    assert matrix.grand_total_cents == spending.total_cents

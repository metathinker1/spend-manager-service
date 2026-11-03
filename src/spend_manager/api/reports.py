"""``GET /api/reports/spending-by-category`` and ``GET /api/reports/category-by-month`` (BR-3)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError

from spend_manager.api.deps import ReadConnection
from spend_manager.api.models import CategoryByMonthReport, SpendingByCategoryReport
from spend_manager.services.reports import (
    category_by_month,
    last_12_months,
    resolve_period,
    spending_by_category,
)

router = APIRouter(prefix="/api/reports", tags=["read"])

Year = Annotated[int, Query(ge=1900, le=2100)]


def today() -> dt.date:
    # Module-level so tests can fix "today" for range=last12.
    return dt.date.today()


@router.get("/spending-by-category", response_model=SpendingByCategoryReport)
def get_spending_by_category(
    conn: ReadConnection,
    year: Year,
    month: Annotated[int | None, Query(ge=1, le=12, description="Omit for the whole year.")] = None,
) -> SpendingByCategoryReport:
    """Expense spending per category for a month or a calendar year."""
    return spending_by_category(conn, resolve_period(year, month))


@router.get("/category-by-month", response_model=CategoryByMonthReport)
def get_category_by_month(
    conn: ReadConnection,
    year: Annotated[int | None, Query(ge=1900, le=2100)] = None,
    range: Annotated[  # shadows the builtin; the query parameter is named "range"
        Literal["last12"] | None, Query(description="last12: this month and the 11 before.")
    ] = None,
) -> CategoryByMonthReport:
    """Expense spending per category and month, for a calendar year or the last 12 months.

    Exactly one of ``year`` and ``range`` must be given.
    """
    if (year is None) == (range is None):
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("query", "year" if year is None else "range"),
                    "msg": "give exactly one of year and range",
                    "input": None,
                }
            ]
        )
    period = resolve_period(year) if year is not None else last_12_months(today())
    return category_by_month(conn, period)

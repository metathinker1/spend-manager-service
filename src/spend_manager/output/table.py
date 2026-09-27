"""Builds the output table (a pandas DataFrame) from a parsed MoneyWell file.

See Functional Design §1.3 and Requirements FR-7 / FR-8.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pandas as pd

from spend_manager.models import ParsedFile

logger = logging.getLogger(__name__)

# Column order per FR-7. ``Reference`` is intentionally dropped (FR-7 / Q8c=B).
COLUMNS = ["Month", "Category", "Payee", "Memo", "Date", "Type", "Amount", "Currency"]

_CENTS = Decimal("0.01")


def build_table(parsed: ParsedFile, year: str, month: str) -> pd.DataFrame:
    """Build the output table for one month's parsed transactions.

    ``year`` and ``month`` are the validated, already-zero-padded strings
    from the request (e.g. ``"2025"``, ``"10"``); they define the ``Month``
    column and are not re-derived from transaction dates.
    """
    month_value = f"{year}-{month}"
    _warn_if_dates_outside_month(parsed, year, month)

    rows = [
        {
            "Month": month_value,
            "Category": txn.category,
            "Payee": txn.payee,
            "Memo": txn.memo,
            "Date": txn.date.isoformat(),
            "Type": txn.type,
            "Amount": str(txn.amount.quantize(_CENTS)),
            "Currency": txn.currency,
        }
        for txn in parsed.transactions
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def _warn_if_dates_outside_month(parsed: ParsedFile, year: str, month: str) -> None:
    # W-3: a transaction date outside the requested month is unusual for a
    # MoneyWell monthly export but does not fail the import — the request's
    # year/month always defines the Month column.
    expected_year, expected_month = int(year), int(month)
    for txn in parsed.transactions:
        if txn.date.year != expected_year or txn.date.month != expected_month:
            logger.warning(
                "Line %d: transaction date %s falls outside requested month %s-%s",
                txn.line_no,
                txn.date.isoformat(),
                year,
                month,
            )

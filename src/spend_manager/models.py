"""Domain entities for a parsed MoneyWell transaction export.

See Functional Design → Domain Entities for the design this implements.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Transaction:
    """A single transaction row from the source file.

    ``line_no`` is 1-indexed into the source file and is used only for
    error messages and logging; it is not part of the output table.
    """

    payee: str
    memo: str
    date: datetime.date
    type: str
    reference: str
    amount: Decimal
    currency: str
    category: str
    line_no: int


@dataclass(frozen=True)
class CategorySummary:
    """A category summary row: the label plus its reported and computed totals."""

    label: str
    reported_total: Decimal
    computed_total: Decimal
    txn_count: int


@dataclass(frozen=True)
class ParsedFile:
    """The full result of parsing one MoneyWell transaction export."""

    transactions: list[Transaction]
    categories: list[CategorySummary]
    report_total: Decimal | None

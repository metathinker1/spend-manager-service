"""Parser for MoneyWell tab-delimited transaction exports.

Implements the parsing algorithm and business rules (BR-1..BR-8, W-1..W-4)
from Functional Design → Business Logic Model / Business Rules.

The format is deliberately *not* read with the ``csv`` module: MoneyWell
never quotes fields, and a payee legitimately containing a comma or a
literal double-quote character would otherwise be mis-split or mangled.
A plain ``str.split("\\t")`` reproduces exactly what MoneyWell wrote.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from spend_manager.exceptions import ParseError
from spend_manager.models import CategorySummary, ParsedFile, Transaction

logger = logging.getLogger(__name__)

EXPECTED_HEADER = ["Payee", "Memo", "Date", "Type", "Reference", "Amount", "Currency"]
_REPORT_TOTAL_LABEL = "Report Total"
_DATE_FORMAT = "%m/%d/%y"


@dataclass(frozen=True)
class _RawTransaction:
    """A parsed transaction row whose category is not yet known.

    Held in the ``pending`` buffer until the next category summary row
    supplies the category label (see Functional Design §1.2).
    """

    payee: str
    memo: str
    date: datetime
    type: str
    reference: str
    amount: Decimal
    currency: str
    line_no: int


def parse(text: str) -> ParsedFile:
    """Parse the full contents of a MoneyWell transaction export.

    Raises:
        ParseError: the file does not conform to BR-1..BR-8.
    """
    content_lines = [
        (line_no, line)
        for line_no, line in enumerate(text.splitlines(), start=1)
        if line.strip() != ""
    ]
    if not content_lines:
        raise ParseError(None, "file is empty")

    header_line_no, header_line = content_lines[0]
    _validate_header(header_line_no, header_line)
    data_lines = content_lines[1:]

    transactions: list[Transaction] = []
    categories: list[CategorySummary] = []
    report_total: Decimal | None = None
    pending: list[_RawTransaction] = []

    last_index = len(data_lines) - 1
    for index, (line_no, line) in enumerate(data_lines):
        fields = line.split("\t")
        if len(fields) != 7:
            raise ParseError(line_no, f"expected 7 tab-separated fields, got {len(fields)}")
        payee, memo, date_s, type_s, reference, amount_s, currency = fields

        date_empty = date_s == ""
        type_empty = type_s == ""
        if date_empty != type_empty:
            raise ParseError(
                line_no,
                "row is neither a transaction nor a category summary "
                "(Date and Type must be both empty or both present)",
            )

        if date_empty and type_empty:
            # Category summary row (BR-5).
            label = payee.strip()
            if not label:
                raise ParseError(line_no, "category summary label is empty")
            subtotal = _parse_amount(line_no, amount_s)

            if index == last_index and label == _REPORT_TOTAL_LABEL:
                # BR-8: the grand-total row, recognized only as the very
                # last non-blank line. Excluded from the output (FR-6).
                report_total = subtotal
                continue

            computed = sum((raw.amount for raw in pending), Decimal("0"))
            if computed != subtotal:
                # W-1: warn only, never fail the import (NFR-4).
                logger.warning(
                    "Line %d: category %r reported total %s does not match "
                    "computed total %s from %d transaction(s)",
                    line_no,
                    label,
                    subtotal,
                    computed,
                    len(pending),
                )
            for raw in pending:
                transactions.append(
                    Transaction(
                        payee=raw.payee,
                        memo=raw.memo,
                        date=raw.date,
                        type=raw.type,
                        reference=raw.reference,
                        amount=raw.amount,
                        currency=raw.currency,
                        category=label,
                        line_no=raw.line_no,
                    )
                )
            categories.append(
                CategorySummary(
                    label=label,
                    reported_total=subtotal,
                    computed_total=computed,
                    txn_count=len(pending),
                )
            )
            pending = []
        else:
            # Transaction row.
            try:
                date = datetime.strptime(date_s, _DATE_FORMAT).date()
            except ValueError as exc:
                raise ParseError(line_no, f"invalid date {date_s!r}") from exc
            amount = _parse_amount(line_no, amount_s)
            pending.append(
                _RawTransaction(
                    payee=payee,
                    memo=memo,
                    date=date,
                    type=type_s,
                    reference=reference,
                    amount=amount,
                    currency=currency,
                    line_no=line_no,
                )
            )

    if pending:
        # BR-6: every transaction must be followed by a category summary row.
        raise ParseError(pending[-1].line_no, "transactions after the last category summary")

    if not transactions:
        # W-4: still a successful (empty) import.
        logger.warning("No transactions found in source file")

    _warn_if_totals_mismatch(categories, report_total)

    return ParsedFile(transactions=transactions, categories=categories, report_total=report_total)


def _validate_header(line_no: int, line: str) -> None:
    fields = line.split("\t")
    if fields != EXPECTED_HEADER:
        raise ParseError(line_no, f"unexpected header {fields!r}, expected {EXPECTED_HEADER!r}")


def _parse_amount(line_no: int, raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ParseError(line_no, f"invalid amount {raw!r}") from exc


def _warn_if_totals_mismatch(
    categories: list[CategorySummary], report_total: Decimal | None
) -> None:
    # W-2: sum of category subtotals should reconcile to the Report Total row.
    if report_total is None:
        if categories:
            logger.warning("No 'Report Total' row found in source file")
        return
    categories_sum = sum((c.reported_total for c in categories), Decimal("0"))
    if categories_sum != report_total:
        logger.warning(
            "Sum of category totals %s does not match Report Total %s",
            categories_sum,
            report_total,
        )

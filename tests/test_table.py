"""Unit tests for spend_manager.output.table.build_table."""

from __future__ import annotations

import datetime
from decimal import Decimal

from spend_manager.models import ParsedFile, Transaction
from spend_manager.output.table import COLUMNS, build_table


def _txn(**overrides) -> Transaction:
    defaults = dict(
        payee="PAYEE",
        memo="",
        date=datetime.date(2025, 10, 1),
        type="Withdrawal",
        reference="",
        amount=Decimal("-1.5"),
        currency="USD",
        category="Cat",
        line_no=2,
    )
    defaults.update(overrides)
    return Transaction(**defaults)


def test_column_order_and_names():
    df = build_table(ParsedFile(transactions=[], categories=[], report_total=None), "2025", "10")

    assert (
        list(df.columns)
        == COLUMNS
        == [
            "Month",
            "Category",
            "Payee",
            "Memo",
            "Date",
            "Type",
            "Amount",
            "Currency",
        ]
    )


def test_header_only_frame_for_zero_transactions():
    df = build_table(ParsedFile(transactions=[], categories=[], report_total=None), "2025", "10")

    assert len(df) == 0
    assert list(df.columns) == COLUMNS


def test_row_values_and_formatting():
    txn = _txn(
        payee="WHOLE FOODS MARKET",
        memo="",
        date=datetime.date(2025, 10, 1),
        type="Withdrawal",
        amount=Decimal("-60"),
        currency="USD",
        category="Groceries",
    )
    parsed = ParsedFile(transactions=[txn], categories=[], report_total=None)

    df = build_table(parsed, "2025", "10")
    row = df.iloc[0]

    assert row["Month"] == "2025-10"
    assert row["Category"] == "Groceries"
    assert row["Payee"] == "WHOLE FOODS MARKET"
    assert row["Memo"] == ""  # empty Memo must not become NaN
    assert row["Date"] == "2025-10-01"  # ISO format
    assert row["Type"] == "Withdrawal"
    assert row["Amount"] == "-60.00"  # Decimal quantized to 2 places
    assert row["Currency"] == "USD"


def test_amount_formatting_keeps_two_decimal_places():
    txn = _txn(amount=Decimal("2500.1"))
    parsed = ParsedFile(transactions=[txn], categories=[], report_total=None)

    df = build_table(parsed, "2025", "10")

    assert df.iloc[0]["Amount"] == "2500.10"


def test_month_column_uses_request_values_not_transaction_date():
    # A transaction dated in October, imported under a November request.
    txn = _txn(date=datetime.date(2025, 10, 31))
    parsed = ParsedFile(transactions=[txn], categories=[], report_total=None)

    df = build_table(parsed, "2025", "11")

    assert df.iloc[0]["Month"] == "2025-11"
    assert df.iloc[0]["Date"] == "2025-10-31"


def test_date_outside_requested_month_logs_warning(caplog):
    txn = _txn(date=datetime.date(2025, 9, 30))
    parsed = ParsedFile(transactions=[txn], categories=[], report_total=None)

    with caplog.at_level("WARNING"):
        build_table(parsed, "2025", "10")

    assert any("falls outside requested month" in message for message in caplog.messages)


def test_preserves_transaction_order():
    txns = [_txn(payee=f"P{i}", date=datetime.date(2025, 10, i + 1)) for i in range(3)]
    parsed = ParsedFile(transactions=txns, categories=[], report_total=None)

    df = build_table(parsed, "2025", "10")

    assert list(df["Payee"]) == ["P0", "P1", "P2"]

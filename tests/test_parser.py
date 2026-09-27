"""Unit tests for spend_manager.parsing.moneywell_parser.

Covers the happy path plus business rules BR-1..BR-8 and warnings
W-1, W-2, W-4 from Functional Design. See tests/fixtures/synthetic_202501.txt
for the happy-path fixture.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from spend_manager.exceptions import ParseError
from spend_manager.parsing.moneywell_parser import parse

HEADER = "Payee\tMemo\tDate\tType\tReference\tAmount\tCurrency"


def _row(payee="", memo="", date="", type_="", reference="", amount="", currency="USD") -> str:
    return "\t".join([payee, memo, date, type_, reference, amount, currency])


# --- Happy path (synthetic fixture) -----------------------------------------


def test_parses_synthetic_happy_path(synthetic_text):
    parsed = parse(synthetic_text)

    assert len(parsed.transactions) == 4
    assert [c.label for c in parsed.categories] == ["Groceries", "Salary", "Gifts", "Transfer"]
    assert parsed.report_total == Decimal("2332.40")


def test_category_assignment_order(synthetic_text):
    parsed = parse(synthetic_text)
    by_payee = {t.payee: t.category for t in parsed.transactions}

    assert by_payee["WHOLE FOODS MARKET"] == "Groceries"
    assert by_payee["TRADER JOE S #100"] == "Groceries"
    assert by_payee["EMPLOYER INC PAYROLL"] == "Salary"
    assert by_payee["Online Transfer to CHK ...1234 t"] == "Transfer"


def test_zero_transaction_category_is_kept_with_no_rows(synthetic_text):
    parsed = parse(synthetic_text)
    gifts = next(c for c in parsed.categories if c.label == "Gifts")

    assert gifts.txn_count == 0
    assert gifts.computed_total == Decimal("0")
    assert not any(t.category == "Gifts" for t in parsed.transactions)


def test_report_total_excluded_from_categories(synthetic_text):
    parsed = parse(synthetic_text)

    assert "Report Total" not in [c.label for c in parsed.categories]
    assert not any(t.category == "Report Total" for t in parsed.transactions)


def test_truncated_payee_spill_is_preserved_as_is(synthetic_text):
    # MoneyWell's 32-char payee truncation is imported verbatim, not repaired.
    parsed = parse(synthetic_text)
    txn = next(t for t in parsed.transactions if t.category == "Transfer")

    assert txn.payee == "Online Transfer to CHK ...1234 t"
    assert txn.memo == "ransaction#: 99999999999"


def test_deposit_and_withdrawal_amounts_preserve_sign(synthetic_text):
    parsed = parse(synthetic_text)
    salary = next(t for t in parsed.transactions if t.category == "Salary")
    groceries = [t for t in parsed.transactions if t.category == "Groceries"]

    assert salary.type == "Deposit"
    assert salary.amount == Decimal("2500.00")
    assert all(t.type == "Withdrawal" and t.amount < 0 for t in groceries)


def test_blank_lines_between_rows_are_skipped():
    text = "\n".join(
        [
            "",
            HEADER,
            "",
            _row("PAYEE A", date="1/1/25", type_="Withdrawal", amount="-10.00"),
            "",
            _row("Cat A", amount="-10.00"),
            "",
        ]
    )
    parsed = parse(text)

    assert len(parsed.transactions) == 1
    assert parsed.transactions[0].category == "Cat A"


# --- BR-1: header ------------------------------------------------------------


def test_missing_or_wrong_header_raises_parse_error():
    text = "Payee\tMemo\tDate\n" + _row("A", date="1/1/25", type_="Withdrawal", amount="-1.00")

    with pytest.raises(ParseError, match="header"):
        parse(text)


def test_empty_file_raises_parse_error():
    with pytest.raises(ParseError, match="empty"):
        parse("")


# --- BR-2: column count -------------------------------------------------------


def test_wrong_column_count_raises_parse_error():
    text = HEADER + "\n" + "Payee Only\tMemo\tDate\tType\tRef\tAmount"  # only 6 fields

    with pytest.raises(ParseError, match="7 tab-separated fields"):
        parse(text)


# --- BR-3: date ---------------------------------------------------------------


def test_invalid_date_raises_parse_error():
    text = HEADER + "\n" + _row("A", date="13/45/25", type_="Withdrawal", amount="-1.00")

    with pytest.raises(ParseError, match="invalid date"):
        parse(text)


# --- BR-4: amount --------------------------------------------------------------


def test_invalid_transaction_amount_raises_parse_error():
    text = HEADER + "\n" + _row("A", date="1/1/25", type_="Withdrawal", amount="not-a-number")

    with pytest.raises(ParseError, match="invalid amount"):
        parse(text)


def test_invalid_category_amount_raises_parse_error():
    text = HEADER + "\n" + _row("Cat A", amount="not-a-number")

    with pytest.raises(ParseError, match="invalid amount"):
        parse(text)


# --- BR-5: Date/Type must be both empty or both present ----------------------


@pytest.mark.parametrize(
    "date,type_",
    [("1/1/25", ""), ("", "Withdrawal")],
)
def test_date_type_mismatch_raises_parse_error(date, type_):
    text = HEADER + "\n" + _row("A", date=date, type_=type_, amount="-1.00")

    with pytest.raises(ParseError, match="neither a transaction nor a category summary"):
        parse(text)


# --- BR-6: trailing transactions with no closing summary ---------------------


def test_trailing_transactions_without_category_raises_parse_error():
    text = HEADER + "\n" + _row("A", date="1/1/25", type_="Withdrawal", amount="-1.00")

    with pytest.raises(ParseError, match="after the last category summary"):
        parse(text)


# --- BR-7: empty category label -----------------------------------------------


def test_empty_category_label_raises_parse_error():
    text = HEADER + "\n" + _row("  ", amount="-1.00")

    with pytest.raises(ParseError, match="empty"):
        parse(text)


# --- BR-8: Report Total recognized only as the last line ----------------------


def test_report_total_mid_file_is_treated_as_a_real_category():
    text = "\n".join(
        [
            HEADER,
            _row("A", date="1/1/25", type_="Withdrawal", amount="-1.00"),
            _row("Report Total", amount="-1.00"),
            _row("B", date="1/2/25", type_="Withdrawal", amount="-2.00"),
            _row("Cat B", amount="-2.00"),
        ]
    )
    parsed = parse(text)

    assert [c.label for c in parsed.categories] == ["Report Total", "Cat B"]
    assert parsed.report_total is None


# --- Warnings (logged, never raise) -------------------------------------------


def test_category_subtotal_mismatch_warns_but_does_not_raise(caplog):
    text = "\n".join(
        [
            HEADER,
            _row("A", date="1/1/25", type_="Withdrawal", amount="-1.00"),
            _row("Cat A", amount="-999.00"),
        ]
    )
    with caplog.at_level("WARNING"):
        parsed = parse(text)

    assert len(parsed.transactions) == 1
    assert any("does not match" in message for message in caplog.messages)


def test_report_total_mismatch_warns_but_does_not_raise(caplog):
    text = "\n".join(
        [
            HEADER,
            _row("A", date="1/1/25", type_="Withdrawal", amount="-1.00"),
            _row("Cat A", amount="-1.00"),
            _row("Report Total", amount="-999.00"),
        ]
    )
    with caplog.at_level("WARNING"):
        parsed = parse(text)

    assert parsed.report_total == Decimal("-999.00")
    assert any("does not match Report Total" in message for message in caplog.messages)


def test_missing_report_total_warns_but_does_not_raise(caplog):
    text = "\n".join(
        [
            HEADER,
            _row("A", date="1/1/25", type_="Withdrawal", amount="-1.00"),
            _row("Cat A", amount="-1.00"),
        ]
    )
    with caplog.at_level("WARNING"):
        parsed = parse(text)

    assert parsed.report_total is None
    assert any("No 'Report Total' row found" in message for message in caplog.messages)


def test_zero_transactions_warns_but_does_not_raise(caplog):
    text = HEADER + "\n" + _row("Report Total", amount="0.00")

    with caplog.at_level("WARNING"):
        parsed = parse(text)

    assert parsed.transactions == []
    assert any("No transactions found" in message for message in caplog.messages)

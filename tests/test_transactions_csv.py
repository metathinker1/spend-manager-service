"""CSV export formatting and streaming (2026-10-31-spend-manager-ui FD §2.5 BR-5)."""

from __future__ import annotations

import csv
import datetime as dt
import io
import sqlite3

import pytest
from read_fixture import build_read_db

from spend_manager.api.models import LineFilterParams
from spend_manager.api.transactions import _stream
from spend_manager.db.schema import open_read_db
from spend_manager.output.transactions_csv import (
    COLUMNS,
    export_filename,
    format_cents,
    format_row,
    guard_formula,
    iter_csv_text,
)


def line(**overrides):
    base = {
        "date": "2026-02-14",
        "account": "Card",
        "payee": "COSTCO",
        "type": "withdrawal",
        "category": "Groceries",
        "memo": None,
        "amount_cents": -8000,
        "transaction_id": 106,
        "split_id": 201,
        "is_transfer": 0,
    }
    return {**base, **overrides}


def parse(text):
    return list(csv.reader(io.StringIO(text)))


@pytest.mark.parametrize(
    ("cents", "text"),
    [(0, "0.00"), (5, "0.05"), (-5, "-0.05"), (-1230, "-12.30"), (123456, "1234.56")],
)
def test_format_cents(cents, text):
    assert format_cents(cents) == text


@pytest.mark.parametrize(
    ("text", "guarded"),
    [
        ("=SUM(A1)", "'=SUM(A1)"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@risk", "'@risk"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("Safe = fine", "Safe = fine"),
        ("", ""),
    ],
)
def test_guard_formula(text, guarded):
    assert guard_formula(text) == guarded


def test_format_row():
    assert format_row(line()) == [
        "2026-02-14",
        "Card",
        "COSTCO",
        "withdrawal",
        "Groceries",
        "",
        "-80.00",
        "106",
        "201",
        "false",
    ]


def test_format_row_non_split_uncategorized_transfer_and_guard():
    row = dict(
        zip(
            COLUMNS,
            format_row(
                line(category=None, split_id=None, is_transfer=1, payee="=HYPERLINK(x)", memo="@m")
            ),
            strict=True,
        )
    )

    assert row["category"] == ""
    assert row["split_id"] == ""
    assert row["is_transfer"] == "true"
    assert row["payee"] == "'=HYPERLINK(x)"
    assert row["memo"] == "'@m"
    assert row["amount"] == "-80.00"  # numbers are never guarded


def test_quoting():
    text = "".join(iter_csv_text([line(payee='Joe\'s "Diner", Inc.', memo="two\nlines")]))

    rows = parse(text)
    assert rows[0] == list(COLUMNS)
    assert rows[1][2] == 'Joe\'s "Diner", Inc.'
    assert rows[1][5] == "two\nlines"
    assert text.endswith("\r\n")


def test_header_only_when_empty():
    assert list(iter_csv_text([])) == [",".join(COLUMNS) + "\r\n"]


def test_chunks():
    chunks = list(iter_csv_text(line(transaction_id=i) for i in range(1200)))

    assert len(chunks) == 3
    rows = parse("".join(chunks))
    assert len(rows) == 1201
    assert rows[-1][7] == "1199"


@pytest.mark.parametrize(
    ("date_from", "date_to", "name"),
    [
        (dt.date(2026, 1, 1), dt.date(2026, 1, 31), "transactions_2026-01-01_2026-01-31.csv"),
        (None, dt.date(2026, 1, 31), "transactions_all_2026-01-31.csv"),
        (dt.date(2026, 1, 1), None, "transactions_2026-01-01_all.csv"),
        (None, None, "transactions_all_all.csv"),
    ],
)
def test_export_filename(date_from, date_to, name):
    assert export_filename(date_from, date_to) == name


@pytest.fixture
def conn(tmp_path):
    return open_read_db(build_read_db(tmp_path / "sm.db"))


def assert_closed(conn):
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_stream_closes_connection_when_done(conn):
    rows = parse("".join(_stream(conn, LineFilterParams())))

    assert len(rows) == 12  # header + 11 non-transfer lines
    assert_closed(conn)


def test_stream_closes_connection_when_abandoned(conn):
    stream = _stream(conn, LineFilterParams())
    next(stream)
    stream.close()

    assert_closed(conn)

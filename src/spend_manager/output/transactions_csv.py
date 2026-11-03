"""CSV export of transaction lines (Functional Design §2.5 BR-5).

One row per matching line, UTF-8 with a header row and RFC 4180 quoting.
Text cells that a spreadsheet would treat as a formula get a leading ``'``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Iterable, Iterator, Mapping

COLUMNS = (
    "date",
    "account",
    "payee",
    "type",
    "category",
    "memo",
    "amount",
    "transaction_id",
    "split_id",
    "is_transfer",
)
_TEXT_COLUMNS = ("account", "payee", "type", "category", "memo")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_ROWS_PER_CHUNK = 500


def format_cents(cents: int) -> str:
    """Integer cents as dollars with two decimals, e.g. ``-1230`` → ``-12.30``."""
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}{dollars}.{rest:02d}"


def guard_formula(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def format_row(line: Mapping[str, object]) -> list[str]:
    row = {
        "date": str(line["date"]),
        "amount": format_cents(int(line["amount_cents"])),
        "transaction_id": str(line["transaction_id"]),
        "split_id": "" if line["split_id"] is None else str(line["split_id"]),
        "is_transfer": "true" if line["is_transfer"] else "false",
    }
    for name in _TEXT_COLUMNS:
        value = line[name]
        row[name] = "" if value is None else guard_formula(str(value))
    return [row[name] for name in COLUMNS]


def iter_csv_text(lines: Iterable[Mapping[str, object]]) -> Iterator[str]:
    """CSV text in chunks: the header, then batches of rows."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    count = 0
    for line in lines:
        writer.writerow(format_row(line))
        count += 1
        if count % _ROWS_PER_CHUNK == 0:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue()


def export_filename(date_from: dt.date | None, date_to: dt.date | None) -> str:
    start = date_from.isoformat() if date_from else "all"
    end = date_to.isoformat() if date_to else "all"
    return f"transactions_{start}_{end}.csv"

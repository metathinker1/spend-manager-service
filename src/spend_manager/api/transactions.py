"""``GET /api/transactions`` and ``GET /api/transactions/export.csv`` (BR-1, BR-2, BR-5)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from spend_manager.api.deps import ReadConnection, SettingsDep, open_checked_read_db
from spend_manager.api.models import LineFilterParams, TransactionFilter, TransactionPage
from spend_manager.output.transactions_csv import export_filename, iter_csv_text
from spend_manager.services.transactions_query import get_transaction_page, iter_csv_lines

router = APIRouter(prefix="/api", tags=["read"])


@router.get("/transactions", response_model=TransactionPage)
def get_transactions(
    f: Annotated[TransactionFilter, Query()], conn: ReadConnection
) -> TransactionPage:
    """Transactions with at least one matching line, newest first, one page at a time.

    The summary counts only matching lines, so it equals the report value
    that was drilled into.
    """
    return get_transaction_page(conn, f)


@router.get(
    "/transactions/export.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "One row per matching line."}},
)
def export_transactions_csv(
    f: Annotated[LineFilterParams, Query()], settings: SettingsDep
) -> StreamingResponse:
    """Every matching line (no paging) as CSV."""
    # The connection outlives this function: the body is streamed afterwards,
    # so it is opened here (errors still become a normal 500) and closed by
    # the generator, or by the background task if streaming never starts.
    conn = open_checked_read_db(settings)
    return StreamingResponse(
        _stream(conn, f),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{export_filename(f.date_from, f.date_to)}"'
            )
        },
        background=BackgroundTask(conn.close),
    )


def _stream(conn: sqlite3.Connection, f: LineFilterParams) -> Iterator[str]:
    try:
        yield from iter_csv_text(iter_csv_lines(conn, f))
    finally:
        conn.close()

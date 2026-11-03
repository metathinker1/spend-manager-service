"""Shared dependencies of the read API (Functional Design §2.6 BR-6)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from spend_manager.config import Settings, get_settings
from spend_manager.db.schema import open_read_db, require_current_schema


def open_checked_read_db(settings: Settings) -> sqlite3.Connection:
    """A read-only connection to a database whose schema matches the code.

    Never creates the file and never migrates, in either environment; problems
    raise ``DatabaseSetupError`` (500, logged) and ``/health/db`` explains them.
    """
    conn = open_read_db(settings.db_path)
    try:
        require_current_schema(conn, settings.environment.value)
    except BaseException:
        conn.close()
        raise
    return conn


def read_connection(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[sqlite3.Connection]:
    """One read-only connection per request, closed afterwards."""
    conn = open_checked_read_db(settings)
    try:
        yield conn
    finally:
        conn.close()


ReadConnection = Annotated[sqlite3.Connection, Depends(read_connection)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

"""Read the selected entities from a (temporary copy of a) MoneyWell store.

See Functional Design §1.3. Entity numbers are discovered from
``Z_PRIMARYKEY`` rather than hard-coded, and every table/column the
importer relies on is verified up front so an unexpected MoneyWell model
version fails with a clear 400 instead of a SQL error. Sensitive account
columns (account/routing numbers, balances) are never selected (NFR-4).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from spend_manager.exceptions import InvalidSourceError, UnsupportedStoreError
from spend_manager.moneywell_store.models import (
    MoneyWellSnapshot,
    MwAccount,
    MwActivity,
    MwBucket,
    MwTag,
)

REQUIRED_ENTITIES = ("Account", "Activity", "Transaction", "Bucket", "Tag")

ACCOUNT_COLUMNS = (
    "Z_PK",
    "ZUNIQUEID",
    "ZNAME",
    "ZTYPE",
    "ZCURRENCYCODE",
    "ZISHIDDEN",
    "ZISDEBT",
    "ZSEQUENCE",
    "ZMEMO",
)
BUCKET_COLUMNS = (
    "Z_PK",
    "ZUNIQUEID",
    "ZNAME",
    "ZTYPE",
    "ZISHIDDEN",
    "ZSEQUENCE",
    "ZMEMO",
    "ZCURRENCYCODE",
)
TAG_COLUMNS = ("Z_PK", "ZNAME")
ACTIVITY_COLUMNS = (
    "Z_PK",
    "ZUNIQUEID",
    "ZACCOUNT2",
    "ZBUCKET2",
    "ZSPLITPARENT",
    "ZDATEYMD",
    "ZTYPE",
    "ZAMOUNT",
    "ZPAYEE",
    "ZMEMO",
    "ZSEQUENCE",
)

REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "ZBUDGETMETADATA": ("ZBUDGETID",),
    "ZACCOUNT": ACCOUNT_COLUMNS,
    "ZBUCKET": BUCKET_COLUMNS,
    "ZTAG": TAG_COLUMNS,
    "ZACTIVITY": (*ACTIVITY_COLUMNS, "Z_ENT"),
}

# Core Data many-to-many join tables/columns look like Z_3TAGS / Z_31TAGS.
_JOIN_IDENTIFIER = re.compile(r"^Z_\d+[A-Z]+$")


def read_store(path: Path) -> MoneyWellSnapshot:
    """Read accounts, buckets, tags, transactions/splits, and tag links.

    ``path`` must be a temporary copy (see ``source.resolve_store``): SQLite
    may checkpoint the WAL into it.

    Raises:
        InvalidSourceError: the file is not a SQLite database.
        UnsupportedStoreError: required entities/tables/columns are missing.
    """
    conn = sqlite3.connect(path)
    try:
        tables = _table_names(conn)
        entity_ids = _entity_ids(conn, tables)
        activity_ent = entity_ids["Activity"]
        tag_ent = entity_ids["Tag"]
        join_table = f"Z_{activity_ent}TAGS"
        join_activity_col = f"Z_{activity_ent}ACTIVITIES"
        join_tag_col = f"Z_{tag_ent}TAGS"
        for identifier in (join_table, join_activity_col, join_tag_col):
            if not _JOIN_IDENTIFIER.match(identifier):  # pragma: no cover - ints only
                raise UnsupportedStoreError(f"unexpected Core Data identifier {identifier!r}")

        required = {**REQUIRED_COLUMNS, join_table: (join_activity_col, join_tag_col)}
        _verify_columns(conn, tables, required)

        txn_ent = entity_ids["Transaction"]
        activities = [
            MwActivity(*row)
            for row in conn.execute(
                f"SELECT {', '.join(ACTIVITY_COLUMNS)} FROM ZACTIVITY WHERE Z_ENT = ?"
                " ORDER BY Z_PK",
                (txn_ent,),
            )
        ]
        non_txn_pks = frozenset(
            row[0]
            for row in conn.execute("SELECT Z_PK FROM ZACTIVITY WHERE Z_ENT <> ?", (txn_ent,))
        )

        return MoneyWellSnapshot(
            budget_ids=[
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT ZBUDGETID FROM ZBUDGETMETADATA"
                    " WHERE ZBUDGETID IS NOT NULL AND ZBUDGETID <> '' ORDER BY ZBUDGETID"
                )
            ],
            accounts=[
                MwAccount(
                    pk=pk,
                    uid=uid,
                    name=name,
                    type_code=type_code,
                    currency_code=currency,
                    is_hidden=bool(hidden),
                    is_debt=bool(debt),
                    sequence=seq,
                    memo=memo,
                )
                for pk, uid, name, type_code, currency, hidden, debt, seq, memo in conn.execute(
                    f"SELECT {', '.join(ACCOUNT_COLUMNS)} FROM ZACCOUNT ORDER BY Z_PK"
                )
            ],
            buckets=[
                MwBucket(
                    pk=pk,
                    uid=uid,
                    name=name,
                    type_code=type_code,
                    is_hidden=bool(hidden),
                    sequence=seq,
                    memo=memo,
                    currency_code=currency,
                )
                for pk, uid, name, type_code, hidden, seq, memo, currency in conn.execute(
                    f"SELECT {', '.join(BUCKET_COLUMNS)} FROM ZBUCKET ORDER BY Z_PK"
                )
            ],
            tags=[
                MwTag(pk=pk, name=name)
                for pk, name in conn.execute("SELECT Z_PK, ZNAME FROM ZTAG ORDER BY Z_PK")
            ],
            transactions=[a for a in activities if a.split_parent_pk is None],
            splits=[a for a in activities if a.split_parent_pk is not None],
            tag_links=[
                (activity_pk, tag_pk)
                for activity_pk, tag_pk in conn.execute(
                    f"SELECT {join_activity_col}, {join_tag_col} FROM {join_table}"
                    f" ORDER BY {join_activity_col}, {join_tag_col}"
                )
            ],
            non_transaction_activity_pks=non_txn_pks,
        )
    finally:
        conn.close()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    except sqlite3.DatabaseError as exc:
        raise InvalidSourceError("persistentStore is not a SQLite database") from exc
    return {row[0] for row in rows}


def _entity_ids(conn: sqlite3.Connection, tables: set[str]) -> dict[str, int]:
    if "Z_PRIMARYKEY" not in tables:
        raise UnsupportedStoreError("unsupported MoneyWell store layout: Z_PRIMARYKEY missing")
    ids = {name: ent for ent, name in conn.execute("SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY")}
    missing = [name for name in REQUIRED_ENTITIES if name not in ids]
    if missing:
        raise UnsupportedStoreError(
            f"unsupported MoneyWell store layout: entities missing: {', '.join(missing)}"
        )
    return {name: int(ids[name]) for name in REQUIRED_ENTITIES}


def _verify_columns(
    conn: sqlite3.Connection, tables: set[str], required: dict[str, tuple[str, ...]]
) -> None:
    for table, columns in required.items():
        if table not in tables:
            raise UnsupportedStoreError(f"unsupported MoneyWell store layout: {table} missing")
        present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column not in present:
                raise UnsupportedStoreError(
                    f"unsupported MoneyWell store layout: {table}.{column} missing"
                )

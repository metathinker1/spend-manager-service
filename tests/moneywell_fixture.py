"""Builder for a small, synthetic, Core-Data-shaped MoneyWell ``persistentStore``.

Only the tables/columns the importer reads (plus a few it must *not* read,
such as ``ZACCOUNTNUMBER``) are created. All data is invented — no real
financial data is ever committed (NFR-4 / Q19).

Tests get the default content from ``default_store()``, mutate the plain
dicts as needed, and write it with ``write_store()``.

Default content (see Functional Design §6):

- accounts: Checking (pk 1), Card (pk 2)
- buckets: Salary (income, pk 1), Groceries (expense, pk 2), Auto: Fuel (expense, pk 3)
- tags: Christmas (pk 1), Haircut (pk 2), a second "Christmas" (pk 3)
- transactions: salary deposit, grocery withdrawal with float noise,
  uncategorized card payment, check, 3-way split (one child uncategorized),
  refund (deposit into an expense bucket), split with a positive child
- one favorite template (non-Transaction activity)
- tag links: on the refund, on the grocery (duplicate-named tag), on a split
  child, and on the favorite (ignored)
"""

from __future__ import annotations

import copy
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

BUDGET_ID = "11111111-2222-3333-4444-555555555555"

ENT_ACCOUNT, ENT_ACTIVITY, ENT_FAVORITE, ENT_TRANSACTION = 1, 3, 4, 7
ENT_BUCKET, ENT_BUDGET_METADATA, ENT_TAG = 10, 14, 31

_ENTITIES = [
    (ENT_ACCOUNT, "Account", 0),
    (ENT_ACTIVITY, "Activity", 0),
    (ENT_FAVORITE, "FavoriteTransaction", ENT_ACTIVITY),
    (ENT_TRANSACTION, "Transaction", ENT_ACTIVITY),
    (ENT_BUCKET, "Bucket", 0),
    (ENT_BUDGET_METADATA, "BudgetMetadata", 0),
    (ENT_TAG, "Tag", 0),
]

_DDL = f"""
CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER PRIMARY KEY, Z_NAME VARCHAR, Z_SUPER INTEGER,
    Z_MAX INTEGER);
CREATE TABLE ZBUDGETMETADATA (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER,
    ZBUDGETID VARCHAR, ZCURRENCYCODE VARCHAR);
CREATE TABLE ZACCOUNT (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER,
    ZISDEBT INTEGER, ZISHIDDEN INTEGER, ZSEQUENCE INTEGER, ZTYPE INTEGER, ZBALANCE DECIMAL,
    ZACCOUNTNUMBER VARCHAR, ZCURRENCYCODE VARCHAR, ZMEMO VARCHAR, ZNAME VARCHAR,
    ZROUTINGNUMBER VARCHAR, ZUNIQUEID VARCHAR);
CREATE TABLE ZBUCKET (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER,
    ZISHIDDEN INTEGER, ZSEQUENCE INTEGER, ZTYPE INTEGER, ZBUCKETGROUP INTEGER,
    ZCURRENCYCODE VARCHAR, ZMEMO VARCHAR, ZNAME VARCHAR, ZUNIQUEID VARCHAR);
CREATE TABLE ZTAG (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZNAME VARCHAR);
CREATE TABLE ZACTIVITY (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER,
    ZDATEYMD INTEGER, ZSEQUENCE INTEGER, ZTYPE INTEGER, ZSPLITPARENT INTEGER,
    ZACCOUNT INTEGER, ZBUCKET INTEGER, ZACCOUNT2 INTEGER, ZBUCKET2 INTEGER,
    ZAMOUNT DECIMAL, ZCHECKREF DECIMAL, ZMEMO VARCHAR, ZPAYEE VARCHAR, ZUNIQUEID VARCHAR);
CREATE TABLE Z_{ENT_ACTIVITY}TAGS (Z_{ENT_ACTIVITY}ACTIVITIES INTEGER, Z_{ENT_TAG}TAGS INTEGER,
    PRIMARY KEY (Z_{ENT_ACTIVITY}ACTIVITIES, Z_{ENT_TAG}TAGS));
"""


def _txn(pk: int, uid: str, **fields: Any) -> dict[str, Any]:
    row = {
        "Z_PK": pk,
        "Z_ENT": ENT_TRANSACTION,
        "ZUNIQUEID": uid,
        "ZACCOUNT2": 1,
        "ZBUCKET2": None,
        "ZSPLITPARENT": None,
        "ZDATEYMD": 20260105,
        "ZTYPE": 1,
        "ZAMOUNT": 0,
        "ZPAYEE": f"Payee {uid}",
        "ZMEMO": None,
        "ZSEQUENCE": pk,
    }
    row.update(fields)
    return row


_DEFAULT: dict[str, Any] = {
    "budget_ids": [BUDGET_ID, BUDGET_ID],  # MoneyWell has two rows with the same id
    "accounts": [
        {
            "Z_PK": 1,
            "ZUNIQUEID": "ACC-1",
            "ZNAME": "Checking",
            "ZTYPE": 0,
            "ZCURRENCYCODE": "USD",
            "ZISHIDDEN": 0,
            "ZISDEBT": 0,
            "ZSEQUENCE": 1,
            "ZMEMO": None,
            "ZACCOUNTNUMBER": "000123456789",
            "ZROUTINGNUMBER": "999999999",
            "ZBALANCE": 1234.56,
        },
        {
            "Z_PK": 2,
            "ZUNIQUEID": "ACC-2",
            "ZNAME": "Card",
            "ZTYPE": 2,
            "ZCURRENCYCODE": "USD",
            "ZISHIDDEN": 1,
            "ZISDEBT": None,
            "ZSEQUENCE": 2,
            "ZMEMO": "rewards card",
            "ZACCOUNTNUMBER": "4111111111111111",
            "ZROUTINGNUMBER": None,
            "ZBALANCE": 0,
        },
    ],
    "buckets": [
        {"Z_PK": 1, "ZUNIQUEID": "BKT-1", "ZNAME": "Salary", "ZTYPE": 1, "ZSEQUENCE": 1},
        {"Z_PK": 2, "ZUNIQUEID": "BKT-2", "ZNAME": "Groceries", "ZTYPE": 2, "ZSEQUENCE": 1},
        {"Z_PK": 3, "ZUNIQUEID": "BKT-3", "ZNAME": "Auto: Fuel", "ZTYPE": 2, "ZSEQUENCE": 2},
    ],
    "tags": [
        {"Z_PK": 1, "ZNAME": "Christmas"},
        {"Z_PK": 2, "ZNAME": "Haircut"},
        {"Z_PK": 3, "ZNAME": " Christmas "},
    ],
    "activities": [
        _txn(1, "TXN-1", ZTYPE=0, ZAMOUNT=2500, ZBUCKET2=1, ZPAYEE="Employer"),
        # 31.089999999999996 is float noise around -31.09 (FD §3.1).
        _txn(2, "TXN-2", ZAMOUNT=-31.089999999999996, ZBUCKET2=2, ZMEMO="weekly shop"),
        _txn(3, "TXN-3", ZAMOUNT=-500, ZPAYEE="Card payment", ZDATEYMD=20260107),
        _txn(4, "TXN-4", ZTYPE=2, ZAMOUNT=-120, ZBUCKET2=2, ZCHECKREF=1001),
        # 3-way split; one child uncategorized.
        _txn(5, "TXN-5", ZACCOUNT2=2, ZAMOUNT=-100.0, ZDATEYMD=20260110),
        _txn(
            6,
            "SPL-1",
            ZACCOUNT2=2,
            ZAMOUNT=-60,
            ZBUCKET2=2,
            ZSPLITPARENT=5,
            ZDATEYMD=20260110,
            ZSEQUENCE=1,
        ),
        _txn(
            7,
            "SPL-2",
            ZACCOUNT2=2,
            ZAMOUNT=-30,
            ZBUCKET2=3,
            ZSPLITPARENT=5,
            ZDATEYMD=20260110,
            ZSEQUENCE=2,
            ZMEMO="fuel",
        ),
        _txn(8, "SPL-3", ZACCOUNT2=2, ZAMOUNT=-10, ZSPLITPARENT=5, ZDATEYMD=20260110, ZSEQUENCE=3),
        # Refund: a deposit into an expense bucket (FD §3.5).
        _txn(9, "TXN-6", ZACCOUNT2=2, ZTYPE=0, ZAMOUNT=42.73, ZBUCKET2=2, ZPAYEE="Store refund"),
        # Split with a positive (partial refund) child.
        _txn(11, "TXN-7", ZAMOUNT=-20, ZDATEYMD=20260120),
        _txn(12, "SPL-4", ZAMOUNT=-25, ZBUCKET2=2, ZSPLITPARENT=11, ZDATEYMD=20260120, ZSEQUENCE=1),
        _txn(
            13,
            "SPL-5",
            ZTYPE=0,
            ZAMOUNT=5,
            ZBUCKET2=2,
            ZSPLITPARENT=11,
            ZDATEYMD=20260120,
            ZSEQUENCE=2,
        ),
        # Favorite template: not a real transaction.
        {
            "Z_PK": 10,
            "Z_ENT": ENT_FAVORITE,
            "ZUNIQUEID": "FAV-1",
            "ZACCOUNT": 1,
            "ZBUCKET": 2,
            "ZDATEYMD": 0,
            "ZTYPE": 1,
            "ZAMOUNT": 0,
            "ZPAYEE": "Grocer",
        },
    ],
    "tag_links": [(9, 1), (2, 3), (6, 2), (10, 1)],
}


def default_store() -> dict[str, Any]:
    """A deep copy of the default content, safe to mutate."""
    return copy.deepcopy(_DEFAULT)


def activity(store: dict[str, Any], uid: str) -> dict[str, Any]:
    """The mutable activity row with the given unique id."""
    return next(a for a in store["activities"] if a.get("ZUNIQUEID") == uid)


def add_transaction(store: dict[str, Any], pk: int, uid: str, **fields: Any) -> dict[str, Any]:
    row = _txn(pk, uid, **fields)
    store["activities"].append(row)
    return row


def write_store(store: dict[str, Any], target_dir: Path, *, wal: bool = False) -> Path:
    """Write ``store`` as ``<target_dir>/persistentStore`` (plus -wal/-shm if ``wal``).

    With ``wal=True`` every row lives only in ``persistentStore-wal`` (the
    main file has no tables), proving the reader applies the WAL.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    if not wal:
        path = target_dir / "persistentStore"
        conn = sqlite3.connect(path)
        try:
            _populate(conn, store)
            conn.commit()
        finally:
            conn.close()
        return path

    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging) / "persistentStore"
        conn = sqlite3.connect(staged, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA wal_autocheckpoint = 0")
            conn.execute("BEGIN")
            _populate(conn, store)
            conn.execute("COMMIT")
            # Copy while the connection is open: closing would checkpoint the WAL.
            for suffix in ("", "-wal", "-shm"):
                shutil.copy2(f"{staged}{suffix}", target_dir / f"persistentStore{suffix}")
        finally:
            conn.close()
    return target_dir / "persistentStore"


def _populate(conn: sqlite3.Connection, store: dict[str, Any]) -> None:
    for statement in _DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.executemany("INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, 0)", _ENTITIES)
    conn.executemany(
        "INSERT INTO ZBUDGETMETADATA (Z_ENT, ZBUDGETID) VALUES (?, ?)",
        [(ENT_BUDGET_METADATA, b) for b in store["budget_ids"]],
    )
    _insert(conn, "ZACCOUNT", ENT_ACCOUNT, store["accounts"])
    _insert(conn, "ZBUCKET", ENT_BUCKET, store["buckets"])
    _insert(conn, "ZTAG", ENT_TAG, store["tags"])
    _insert(conn, "ZACTIVITY", ENT_TRANSACTION, store["activities"])
    conn.executemany(f"INSERT INTO Z_{ENT_ACTIVITY}TAGS VALUES (?, ?)", store["tag_links"])


def _insert(conn: sqlite3.Connection, table: str, ent: int, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        values = {"Z_ENT": ent, **row}
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values())
        )

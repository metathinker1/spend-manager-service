"""Spend Manager SQLite schema, migrations, and connection setup.

See Functional Design §2.2 (schema v1), §2.3 (schema management) and CR-1
(schema v2: ``category_line`` reporting view).
Migrations are plain DDL scripts applied in order; the applied version is
recorded in ``schema_version`` so later features (e.g. bucket groups as
``category_group``) can be added as new migrations (NFR-9).
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from spend_manager.exceptions import DatabasePathError, SchemaVersionError

V1_DDL = """
CREATE TABLE import_metadata (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    moneywell_budget_id  TEXT    NOT NULL,
    first_imported_at    TEXT    NOT NULL,
    last_imported_at     TEXT    NOT NULL
);

CREATE TABLE account (
    id             INTEGER PRIMARY KEY,
    moneywell_uid  TEXT    UNIQUE,
    name           TEXT    NOT NULL,
    type           TEXT    NOT NULL,
    currency_code  TEXT,
    is_hidden      INTEGER NOT NULL DEFAULT 0 CHECK (is_hidden IN (0, 1)),
    is_debt        INTEGER NOT NULL DEFAULT 0 CHECK (is_debt IN (0, 1)),
    sequence       INTEGER,
    memo           TEXT
);

CREATE TABLE category (
    id             INTEGER PRIMARY KEY,
    moneywell_uid  TEXT    UNIQUE,
    name           TEXT    NOT NULL,
    type           TEXT    NOT NULL CHECK (type IN ('income', 'expense')),
    is_hidden      INTEGER NOT NULL DEFAULT 0 CHECK (is_hidden IN (0, 1)),
    sequence       INTEGER,
    memo           TEXT,
    currency_code  TEXT
);

CREATE TABLE tag (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE spend_transaction (
    id             INTEGER PRIMARY KEY,
    moneywell_uid  TEXT    UNIQUE,
    account_id     INTEGER NOT NULL REFERENCES account (id),
    category_id    INTEGER REFERENCES category (id),
    date           TEXT    NOT NULL,
    type           TEXT    NOT NULL CHECK (type IN ('deposit', 'withdrawal', 'check')),
    amount_cents   INTEGER NOT NULL,
    payee          TEXT,
    memo           TEXT,
    is_split       INTEGER NOT NULL DEFAULT 0 CHECK (is_split IN (0, 1)),
    CHECK (is_split = 0 OR category_id IS NULL)
);
CREATE INDEX ix_spend_transaction_date     ON spend_transaction (date);
CREATE INDEX ix_spend_transaction_account  ON spend_transaction (account_id);
CREATE INDEX ix_spend_transaction_category ON spend_transaction (category_id);

CREATE TABLE transaction_split (
    id                    INTEGER PRIMARY KEY,
    moneywell_uid         TEXT    UNIQUE,
    spend_transaction_id  INTEGER NOT NULL REFERENCES spend_transaction (id) ON DELETE CASCADE,
    category_id           INTEGER REFERENCES category (id),
    amount_cents          INTEGER NOT NULL,
    memo                  TEXT,
    sequence              INTEGER
);
CREATE INDEX ix_transaction_split_txn      ON transaction_split (spend_transaction_id);
CREATE INDEX ix_transaction_split_category ON transaction_split (category_id);

CREATE TABLE transaction_tag (
    spend_transaction_id  INTEGER NOT NULL REFERENCES spend_transaction (id) ON DELETE CASCADE,
    tag_id                INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
    PRIMARY KEY (spend_transaction_id, tag_id)
);
CREATE INDEX ix_transaction_tag_tag ON transaction_tag (tag_id);

CREATE TABLE transaction_split_tag (
    transaction_split_id  INTEGER NOT NULL REFERENCES transaction_split (id) ON DELETE CASCADE,
    tag_id                INTEGER NOT NULL REFERENCES tag (id) ON DELETE CASCADE,
    PRIMARY KEY (transaction_split_id, tag_id)
);
CREATE INDEX ix_transaction_split_tag_tag ON transaction_split_tag (tag_id);
"""

# v2: read-only reporting view (Functional Design CR-1). One row per
# categorizable line: every non-split transaction plus every split row —
# never a split parent, so totals are neither undercounted nor
# double-counted. ``amount`` is dollars for display; for exact totals
# aggregate first, e.g. ``printf('%.2f', SUM(amount_cents) / 100.0)``.
V2_DDL = """
CREATE VIEW category_line AS
SELECT t.id                 AS spend_transaction_id,
       NULL                 AS transaction_split_id,
       t.date               AS date,
       t.account_id         AS account_id,
       t.category_id        AS category_id,
       t.payee              AS payee,
       t.memo               AS memo,
       t.amount_cents       AS amount_cents,
       t.amount_cents / 100.0 AS amount
FROM spend_transaction t
WHERE t.is_split = 0
UNION ALL
SELECT t.id,
       s.id,
       t.date,
       t.account_id,
       s.category_id,
       t.payee,
       s.memo,
       s.amount_cents,
       s.amount_cents / 100.0
FROM transaction_split s
JOIN spend_transaction t ON t.id = s.spend_transaction_id
"""

MIGRATIONS: list[tuple[int, str]] = [(1, V1_DDL), (2, V2_DDL)]

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT    NOT NULL
)
"""

BUSY_TIMEOUT_SECONDS = 5.0


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the Spend Manager database.

    The connection is in autocommit mode (``isolation_level=None``) so callers
    control transactions explicitly with ``BEGIN`` / ``COMMIT`` / ``ROLLBACK``.

    Raises:
        DatabasePathError: ``path`` is an existing directory, or its parent is
            not a directory (e.g. ``SPEND_MANAGER_DB_PATH`` set to a folder).
    """
    if path.is_dir():
        raise DatabasePathError(
            f"SPEND_MANAGER_DB_PATH must be a database file path, not a directory: {path} "
            f"(e.g. {path / 'spend_manager.db'})"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError) as exc:
        raise DatabasePathError(
            f"SPEND_MANAGER_DB_PATH parent is not a directory: {path.parent}"
        ) from exc
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(_SCHEMA_VERSION_DDL)
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def ensure_schema(conn: sqlite3.Connection) -> int:
    """Apply any pending migrations; return the resulting schema version.

    Idempotent and safe under concurrent callers. Each migration runs in its
    own transaction together with its ``schema_version`` row, so a failed
    migration leaves no partial schema.

    Raises:
        SchemaVersionError: the database was created by newer code.
    """
    version = current_version(conn)
    latest = MIGRATIONS[-1][0]
    if version > latest:
        raise SchemaVersionError(
            f"database schema version {version} is newer than supported version {latest}"
        )
    for target, ddl in MIGRATIONS:
        if target <= version:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Re-check under the write lock: a concurrent caller may have
            # applied this migration while we waited for the lock.
            if current_version(conn) >= target:
                conn.execute("COMMIT")
                version = target
                continue
            for statement in _split_statements(ddl):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (target, utc_now_iso()),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        version = target
    return version


def _split_statements(ddl: str) -> list[str]:
    # ``executescript`` would issue its own COMMIT, breaking the per-migration
    # transaction; the DDL above contains no ';' inside statements, so a
    # plain split is safe.
    return [s.strip() for s in ddl.split(";") if s.strip()]

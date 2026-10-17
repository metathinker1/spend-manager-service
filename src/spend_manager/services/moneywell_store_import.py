"""Import a MoneyWell ``persistentStore`` into the Spend Manager database.

Orchestrates Functional Design §1.1: resolve source → read → validate →
open DB → one transaction { budget guard → insert-only plan → writes } →
commit. Any error rolls the whole import back (NFR-2).

Re-imports are insert-only (FR-9): rows already present (by MoneyWell unique
id, or name for tags) are skipped, never updated or deleted, because the
Spend Manager database is the system of record after the first import.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from spend_manager.config import Settings
from spend_manager.db import repository as repo
from spend_manager.db.schema import ensure_schema, open_db, utc_now_iso
from spend_manager.exceptions import BudgetMismatchError, SplitConflictError
from spend_manager.moneywell_store.convert import ConvertedSnapshot, validate_snapshot
from spend_manager.moneywell_store.reader import read_store
from spend_manager.moneywell_store.source import resolve_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntityCount:
    inserted: int
    skipped_existing: int


@dataclass(frozen=True)
class ImportSummary:
    moneywell_budget_id: str
    counts: dict[str, EntityCount]
    warnings: list[str]


def import_store(budget_folder: str, settings: Settings) -> ImportSummary:
    """Import the MoneyWell store named by ``budget_folder``.

    Raises:
        SourceNotFoundError: the store could not be found (404).
        ImportRejectedError: invalid source, unsupported layout, validation
            anomalies, budget mismatch, or split conflict (400). Nothing is
            written.
    """
    with resolve_store(settings.store_root, budget_folder) as store_path:
        snapshot = read_store(store_path)
    converted = validate_snapshot(snapshot)

    conn = open_db(settings.db_path)
    try:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            _check_budget(conn, converted.budget_id)
            counts = _write(conn, converted)
            repo.record_import(conn, converted.budget_id, utc_now_iso())
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    logger.info(
        "Imported MoneyWell budget %s from %r: %s",
        converted.budget_id,
        budget_folder,
        ", ".join(f"{k} +{v.inserted}/={v.skipped_existing}" for k, v in counts.items()),
    )
    for warning in converted.warnings:
        logger.warning("MoneyWell import: %s", warning)
    return ImportSummary(converted.budget_id, counts, converted.warnings)


def _check_budget(conn: sqlite3.Connection, budget_id: str) -> None:
    bound = repo.get_bound_budget_id(conn)
    if bound is not None and bound != budget_id:
        raise BudgetMismatchError(budget_id, bound)


def _count(inserted: int, total: int) -> EntityCount:
    return EntityCount(inserted=inserted, skipped_existing=total - inserted)


def _write(conn: sqlite3.Connection, data: ConvertedSnapshot) -> dict[str, EntityCount]:
    """Insert new rows in FK order; return per-entity counts (FD §1.4 / §1.5)."""
    existing_txns = repo.existing_uids(conn, "spend_transaction")
    existing_splits = repo.existing_uids(conn, "transaction_split")

    # FR-10: a new split cannot be attached to a transaction imported earlier.
    conflicts = [
        (s.transaction_uid, s.uid)
        for s in data.splits
        if s.uid not in existing_splits and s.transaction_uid in existing_txns
    ]
    if conflicts:
        raise SplitConflictError(conflicts)

    counts: dict[str, EntityCount] = {}

    existing = repo.existing_uids(conn, "account")
    new_accounts = [a for a in data.accounts if a.uid not in existing]
    repo.insert_accounts(conn, new_accounts)
    counts["accounts"] = _count(len(new_accounts), len(data.accounts))
    account_ids = repo.existing_uids(conn, "account")

    existing = repo.existing_uids(conn, "category")
    new_categories = [c for c in data.categories if c.uid not in existing]
    repo.insert_categories(conn, new_categories)
    counts["categories"] = _count(len(new_categories), len(data.categories))
    category_ids = repo.existing_uids(conn, "category")

    existing_tag_names = repo.existing_tags(conn)
    new_tags = [n for n in data.tag_names if n not in existing_tag_names]
    repo.insert_tags(conn, new_tags)
    counts["tags"] = _count(len(new_tags), len(data.tag_names))
    tag_ids = repo.existing_tags(conn)

    def category_id(uid: str | None) -> int | None:
        return category_ids[uid] if uid is not None else None

    new_txns = [t for t in data.transactions if t.uid not in existing_txns]
    repo.insert_transactions(
        conn,
        [
            (
                t.uid,
                account_ids[t.account_uid],
                category_id(t.category_uid),
                t.date,
                t.type,
                t.amount_cents,
                t.payee,
                t.memo,
                t.is_split,
            )
            for t in new_txns
        ],
    )
    counts["transactions"] = _count(len(new_txns), len(data.transactions))
    txn_ids = repo.existing_uids(conn, "spend_transaction")

    new_splits = [s for s in data.splits if s.uid not in existing_splits]
    repo.insert_splits(
        conn,
        [
            (
                s.uid,
                txn_ids[s.transaction_uid],
                category_id(s.category_uid),
                s.amount_cents,
                s.memo,
                s.sequence,
            )
            for s in new_splits
        ],
    )
    counts["transaction_splits"] = _count(len(new_splits), len(data.splits))
    split_ids = repo.existing_uids(conn, "transaction_split")

    # C2: tag links only for rows inserted in this run.
    new_txn_uids = {t.uid for t in new_txns}
    txn_links = {
        (txn_ids[uid], tag_ids[name]) for uid, name in data.transaction_tags if uid in new_txn_uids
    }
    repo.insert_transaction_tags(conn, sorted(txn_links))
    all_txn_links = {(txn_ids[uid], tag_ids[name]) for uid, name in data.transaction_tags}
    counts["transaction_tags"] = _count(len(txn_links), len(all_txn_links))

    new_split_uids = {s.uid for s in new_splits}
    split_links = {
        (split_ids[uid], tag_ids[name]) for uid, name in data.split_tags if uid in new_split_uids
    }
    repo.insert_split_tags(conn, sorted(split_links))
    all_split_links = {(split_ids[uid], tag_ids[name]) for uid, name in data.split_tags}
    counts["transaction_split_tags"] = _count(len(split_links), len(all_split_links))

    return counts

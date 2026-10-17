"""Optional integration test against the newest real MoneyWell snapshot.

Snapshots live under ``MONEYWELL_STORE_ROOT`` (default
``~/Project.SpendManager/MoneyWell_Data``) as dated folders
``SampleData_YYYYMMDD``; the newest one is used. Skipped when none exists.

Expected counts are derived from the snapshot itself (independent SQL on a
copy), so the test keeps working as new snapshots are added. When a
same-dated ``CompareData_YYYYMMDD/FullSummary_*.txt`` (a MoneyWell Summary
Report export) exists, every category total and the report total are
reconciled to the cent through the ``category_line`` view.

Also checks NFR-1 (source unchanged), NFR-5 (< 5 s) and that a re-import
inserts nothing.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

import pytest

from spend_manager.config import Settings
from spend_manager.services.moneywell_store_import import EntityCount, import_store

STORE_ROOT = Path(
    os.environ.get("MONEYWELL_STORE_ROOT", "~/Project.SpendManager/MoneyWell_Data")
).expanduser()
_SNAPSHOT = re.compile(r"^SampleData_(\d{8})$")


def _store_dir(folder: Path) -> Path | None:
    for candidate in (folder / "data", folder):
        if (candidate / "persistentStore").is_file():
            return candidate
    return None


def _latest_snapshot() -> tuple[str, str] | None:
    """(folder name, YYYYMMDD) of the newest SampleData_YYYYMMDD with a store."""
    if not STORE_ROOT.is_dir():
        return None
    snapshots = [
        (match.group(1), path.name)
        for path in STORE_ROOT.iterdir()
        if (match := _SNAPSHOT.match(path.name)) and _store_dir(path)
    ]
    if not snapshots:
        return None
    date, name = max(snapshots)
    return name, date


SNAPSHOT = _latest_snapshot()

pytestmark = pytest.mark.skipif(
    SNAPSHOT is None, reason=f"no SampleData_YYYYMMDD snapshot found under {STORE_ROOT}"
)


def _digest(store_dir: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in store_dir.iterdir()
        if p.name.startswith("persistentStore")
    }


def _expected_counts(store_dir: Path, tmp_path: Path) -> dict[str, EntityCount]:
    """Count the in-scope records straight from a copy of the snapshot."""
    copy_dir = tmp_path / "expected"
    copy_dir.mkdir()
    for p in store_dir.iterdir():
        if p.name.startswith("persistentStore"):
            shutil.copy2(p, copy_dir / p.name)
    conn = sqlite3.connect(copy_dir / "persistentStore")
    try:
        txn_ent = conn.execute("SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME = 'Transaction'")
        txn_ent = txn_ent.fetchone()[0]

        def scalar(sql: str) -> int:
            return conn.execute(sql, {"t": txn_ent}).fetchone()[0]

        return {
            "accounts": EntityCount(scalar("SELECT COUNT(*) FROM ZACCOUNT"), 0),
            "categories": EntityCount(scalar("SELECT COUNT(*) FROM ZBUCKET"), 0),
            "tags": EntityCount(scalar("SELECT COUNT(DISTINCT TRIM(ZNAME)) FROM ZTAG"), 0),
            "transactions": EntityCount(
                scalar("SELECT COUNT(*) FROM ZACTIVITY WHERE Z_ENT = :t AND ZSPLITPARENT IS NULL"),
                0,
            ),
            "transaction_splits": EntityCount(
                scalar(
                    "SELECT COUNT(*) FROM ZACTIVITY WHERE Z_ENT = :t AND ZSPLITPARENT IS NOT NULL"
                ),
                0,
            ),
            "transaction_tags": EntityCount(
                scalar(
                    "SELECT COUNT(DISTINCT a.Z_PK || '|' || TRIM(g.ZNAME)) FROM Z_3TAGS l"
                    " JOIN ZACTIVITY a ON a.Z_PK = l.Z_3ACTIVITIES"
                    " JOIN ZTAG g ON g.Z_PK = l.Z_31TAGS"
                    " WHERE a.Z_ENT = :t AND a.ZSPLITPARENT IS NULL"
                ),
                0,
            ),
            "transaction_split_tags": EntityCount(
                scalar(
                    "SELECT COUNT(DISTINCT a.Z_PK || '|' || TRIM(g.ZNAME)) FROM Z_3TAGS l"
                    " JOIN ZACTIVITY a ON a.Z_PK = l.Z_3ACTIVITIES"
                    " JOIN ZTAG g ON g.Z_PK = l.Z_31TAGS"
                    " WHERE a.Z_ENT = :t AND a.ZSPLITPARENT IS NOT NULL"
                ),
                0,
            ),
        }
    finally:
        conn.close()


def _summary_report(date: str) -> dict[str, Decimal] | None:
    """Name -> amount from CompareData_<date>/FullSummary_*.txt, if present."""
    reports = sorted((STORE_ROOT / f"CompareData_{date}").glob("FullSummary_*.txt"))
    if not reports:
        return None
    lines = reports[-1].read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split("\t")[:2] == ["Name", "Amount"]
    return {
        name: Decimal(amount)
        for name, amount, *_ in (line.split("\t") for line in lines[1:] if line.strip())
    }


def test_real_snapshot_import(tmp_path):
    folder, date = SNAPSHOT
    store_dir = _store_dir(STORE_ROOT / folder)
    expected = _expected_counts(store_dir, tmp_path)
    settings = Settings(
        moneywell_store_root=str(STORE_ROOT), spend_manager_db_path=str(tmp_path / "sm.db")
    )
    before = _digest(store_dir)

    started = time.perf_counter()
    summary = import_store(folder, settings)
    elapsed = time.perf_counter() - started

    assert summary.counts == expected
    assert elapsed < 5.0
    assert _digest(store_dir) == before

    again = import_store(folder, settings)
    assert all(c.inserted == 0 for c in again.counts.values())
    assert _digest(store_dir) == before


def test_real_snapshot_matches_moneywell_summary_report(tmp_path):
    folder, date = SNAPSHOT
    report = _summary_report(date)
    if report is None:
        pytest.skip(f"no CompareData_{date}/FullSummary_*.txt to reconcile against")

    settings = Settings(
        moneywell_store_root=str(STORE_ROOT), spend_manager_db_path=str(tmp_path / "sm.db")
    )
    import_store(folder, settings)

    conn = sqlite3.connect(settings.db_path)
    try:
        rows = conn.execute(
            "SELECT COALESCE(c.name, '(no bucket)'), printf('%.2f', SUM(l.amount_cents) / 100.0)"
            " FROM category_line l LEFT JOIN category c ON c.id = l.category_id GROUP BY 1"
        ).fetchall()
        total = conn.execute(
            "SELECT printf('%.2f', SUM(amount_cents) / 100.0) FROM category_line"
        ).fetchone()[0]
    finally:
        conn.close()

    actual = {name: Decimal(amount) for name, amount in rows}
    actual["Report Total"] = Decimal(total)
    # MoneyWell omits categories with no activity; Spend Manager has no row for them either.
    assert actual == report

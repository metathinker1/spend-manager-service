"""Tests for restore and the run lock (FD v2 §1.5, §1.6, rules X-1..X-5)."""

from __future__ import annotations

import datetime
import gzip
import hashlib
import sqlite3
from pathlib import Path

import pytest

from spend_manager.db.backup import RunLock, create_backup, list_backups, restore_backup
from spend_manager.db.schema import MIGRATIONS, ensure_schema, open_db
from spend_manager.exceptions import DatabaseInUseError, RestoreError

T0 = datetime.datetime(2026, 10, 22, 14, 30, tzinfo=datetime.UTC)


def _db(path: Path, uids: list[str]) -> Path:
    conn = open_db(path)
    try:
        ensure_schema(conn)
        for uid in uids:
            conn.execute(
                "INSERT INTO account (moneywell_uid, name, type) VALUES (?, 'x', 'checking')",
                (uid,),
            )
    finally:
        conn.close()
    return path


def _uids(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT moneywell_uid FROM account ORDER BY id")]
    finally:
        conn.close()


@pytest.fixture
def setup(tmp_path):
    db = _db(tmp_path / "prod" / "spend_manager.db", ["A"])
    backups = tmp_path / "backups"
    original = create_backup(db, backups, "production", now=T0)
    # Later change that the restore should undo.
    conn = open_db(db)
    conn.execute("INSERT INTO account (moneywell_uid, name, type) VALUES ('B', 'x', 'checking')")
    conn.close()
    return db, backups, backups / original.file


def test_round_trip_restores_original_data(setup):
    db, backups, original = setup
    assert _uids(db) == ["A", "B"]

    result = restore_backup(original, db, backups, "production")

    assert _uids(db) == ["A"]
    assert result.restored_from == original.name
    assert result.schema_version == MIGRATIONS[-1][0]
    # X-3: a safety backup of the pre-restore state ("A", "B") was taken first.
    safety = backups / result.safety_backup
    out = db.parent / "safety-check.db"
    out.write_bytes(gzip.decompress(safety.read_bytes()))
    assert _uids(out) == ["A", "B"]
    assert len(list_backups(backups, "production")) == 2


def test_stale_wal_and_shm_are_removed(setup):
    db, backups, original = setup
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("INSERT INTO account (moneywell_uid, name, type) VALUES ('C', 'x', 'checking')")
    writer.close()  # last connection closes → SQLite checkpoints; recreate stale files:
    Path(f"{db}-wal").write_bytes(b"stale wal")
    Path(f"{db}-shm").write_bytes(b"stale shm")

    restore_backup(original, db, backups, "production")

    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()
    assert _uids(db) == ["A"]


def test_refused_while_service_holds_the_lock(setup):
    db, backups, original = setup
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    service_lock = RunLock(db).acquire_shared()
    try:
        with pytest.raises(DatabaseInUseError, match="stop the Spend Manager service first"):
            restore_backup(original, db, backups, "production")
    finally:
        service_lock.release()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    # The lock was released after the refusal: a restore now succeeds.
    restore_backup(original, db, backups, "production")


def test_service_cannot_start_during_restore(tmp_path):
    db = tmp_path / "sm.db"
    restore_lock = RunLock(db).acquire_exclusive()
    try:
        with pytest.raises(DatabaseInUseError, match="being restored"):
            RunLock(db).acquire_shared()
    finally:
        restore_lock.release()
    shared_a = RunLock(db).acquire_shared()
    shared_b = RunLock(db).acquire_shared()  # several service processes may share
    shared_a.release()
    shared_b.release()
    assert not shared_a.held


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not gzip at all", "not a valid gzip backup"),
        (gzip.compress(b"not sqlite" * 500), "not a SQLite database"),
    ],
)
def test_invalid_backups_leave_live_db_untouched(setup, content, message):
    db, backups, _ = setup
    bad = backups / "spend_manager_production_20261001T000000Z.db.gz"
    bad.write_bytes(content)
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    with pytest.raises(RestoreError, match=message):
        restore_backup(bad, db, backups, "production")

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not list(db.parent.glob(".restore-*"))  # temp file cleaned up
    RunLock(db).acquire_exclusive().release()  # lock released


def test_non_spend_manager_database_is_rejected(setup, tmp_path):
    db, backups, _ = setup
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    bad = backups / "other.db.gz"
    bad.write_bytes(gzip.compress(other.read_bytes()))

    with pytest.raises(RestoreError, match="not a Spend Manager database backup"):
        restore_backup(bad, db, backups, "production")


def test_wrong_file_name_or_missing_file(setup, tmp_path):
    db, backups, _ = setup
    (tmp_path / "x.zip").write_bytes(b"x")
    with pytest.raises(RestoreError, match="not a backup file"):
        restore_backup(tmp_path / "x.zip", db, backups, "production")
    with pytest.raises(RestoreError, match="not a backup file"):
        restore_backup(tmp_path / "missing.db.gz", db, backups, "production")


def test_safety_backup_failure_aborts_restore(setup, monkeypatch):
    from spend_manager.db import backup as backup_mod
    from spend_manager.exceptions import BackupError

    db, backups, original = setup

    def failing(*args, **kwargs):
        raise BackupError("disk full")

    monkeypatch.setattr(backup_mod, "create_backup", failing)
    with pytest.raises(RestoreError, match="safety backup failed, restore aborted: disk full"):
        restore_backup(original, db, backups, "production")
    assert _uids(db) == ["A", "B"]


def test_restore_into_missing_database_without_backup_dir(setup, tmp_path):
    _, _, original = setup
    fresh = tmp_path / "new" / "spend_manager.db"

    result = restore_backup(original, fresh, None, "development")

    assert _uids(fresh) == ["A"]
    assert result.safety_backup is None

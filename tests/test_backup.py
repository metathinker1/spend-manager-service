"""Tests for spend_manager.db.backup — creating backups (FD v2 §1.2, rules B-1..B-6)."""

from __future__ import annotations

import datetime
import gzip
import hashlib
import sqlite3
from pathlib import Path

import pytest

from spend_manager.db import backup as backup_mod
from spend_manager.db.backup import create_backup, latest_backup, list_backups
from spend_manager.db.schema import ensure_schema, open_db
from spend_manager.exceptions import BackupError

T0 = datetime.datetime(2026, 10, 22, 14, 30, 0, tzinfo=datetime.UTC)


def _make_db(path: Path, rows: int = 1) -> Path:
    conn = open_db(path)
    try:
        ensure_schema(conn)
        for i in range(rows):
            conn.execute(
                "INSERT INTO account (moneywell_uid, name, type) VALUES (?, ?, 'checking')",
                (f"ACC-{i}", f"Account {i}"),
            )
    finally:
        conn.close()
    return path


def _add_account(path: Path, uid: str) -> None:
    conn = open_db(path)
    try:
        conn.execute(
            "INSERT INTO account (moneywell_uid, name, type) VALUES (?, 'x', 'checking')", (uid,)
        )
    finally:
        conn.close()


def _accounts_in_backup(gz_path: Path, tmp_path: Path) -> list[str]:
    out = tmp_path / f"check-{gz_path.name}.db"
    out.write_bytes(gzip.decompress(gz_path.read_bytes()))
    conn = sqlite3.connect(out)
    try:
        return [r[0] for r in conn.execute("SELECT moneywell_uid FROM account ORDER BY id")]
    finally:
        conn.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_creates_named_gzip_backup(tmp_path):
    db = _make_db(tmp_path / "prod" / "spend_manager.db")
    backups = tmp_path / "Drive" / "SpendManagerBackups"

    result = create_backup(db, backups, "production", now=T0)

    assert result.status == "created"
    assert result.file == "spend_manager_production_20261022T143000Z.db.gz"
    assert (backups / result.file).is_file()
    assert _accounts_in_backup(backups / result.file, tmp_path) == ["ACC-0"]
    assert [p.name for p in backups.iterdir()] == [result.file]  # no .tmp left


def test_snapshot_includes_data_only_in_the_wal(tmp_path):
    db = _make_db(tmp_path / "sm.db")
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute(
        "INSERT INTO account (moneywell_uid, name, type) VALUES ('WAL', 'w', 'checking')"
    )
    try:
        assert Path(f"{db}-wal").stat().st_size > 0
        result = create_backup(db, tmp_path / "b", "production", now=T0)
    finally:
        writer.close()
    assert _accounts_in_backup(tmp_path / "b" / result.file, tmp_path) == ["ACC-0", "WAL"]


def test_live_database_is_not_modified(tmp_path):
    db = _make_db(tmp_path / "sm.db")
    before = _digest(db)
    create_backup(db, tmp_path / "b", "production", now=T0)
    assert _digest(db) == before


def test_unchanged_database_is_skipped(tmp_path):
    db = _make_db(tmp_path / "sm.db")
    first = create_backup(db, tmp_path / "b", "production", now=T0)

    second = create_backup(db, tmp_path / "b", "production", now=T0 + datetime.timedelta(hours=1))

    assert second.status == "skipped_unchanged"
    assert second.file == first.file
    assert len(list_backups(tmp_path / "b", "production")) == 1


def test_changed_database_creates_new_backup(tmp_path):
    db = _make_db(tmp_path / "sm.db")
    create_backup(db, tmp_path / "b", "production", now=T0)
    _add_account(db, "ACC-NEW")

    result = create_backup(db, tmp_path / "b", "production", now=T0 + datetime.timedelta(hours=1))

    assert result.status == "created"
    assert _accounts_in_backup(tmp_path / "b" / result.file, tmp_path) == ["ACC-0", "ACC-NEW"]


def test_force_writes_even_when_unchanged(tmp_path):
    db = _make_db(tmp_path / "sm.db")
    create_backup(db, tmp_path / "b", "production", now=T0)
    forced = create_backup(db, tmp_path / "b", "production", now=T0, force=True)
    assert forced.status == "created"
    assert forced.file == "spend_manager_production_20261022T143000Z-2.db.gz"  # B-5 suffix


def test_backups_are_per_environment(tmp_path):
    db = _make_db(tmp_path / "sm.db")
    create_backup(db, tmp_path / "b", "production", now=T0)
    dev = create_backup(db, tmp_path / "b", "development", now=T0)
    assert dev.status == "created"  # other env's backups never count as "unchanged"
    assert latest_backup(tmp_path / "b", "development").file == dev.file


def test_missing_database(tmp_path):
    with pytest.raises(BackupError, match="database file not found: nope.db"):
        create_backup(tmp_path / "nope.db", tmp_path / "b", "production")
    assert not (tmp_path / "b").exists()


def test_corrupt_database_writes_nothing(tmp_path):
    db = tmp_path / "sm.db"
    db.write_bytes(b"not a database" * 1000)
    with pytest.raises(BackupError, match="cannot read database sm.db"):
        create_backup(db, tmp_path / "b", "production")
    assert not (tmp_path / "b").exists()


def test_integrity_failure_writes_nothing(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "sm.db")
    monkeypatch.setattr(backup_mod, "_integrity_problems", lambda conn: ["*** page 3 corrupt"])
    with pytest.raises(BackupError, match=r"integrity check failed for sm.db: \*\*\* page 3"):
        create_backup(db, tmp_path / "b", "production")
    assert not (tmp_path / "b").exists()


def test_write_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "sm.db")

    def boom(src, dst):
        raise OSError("No space left on device")

    monkeypatch.setattr(backup_mod.os, "replace", boom)
    with pytest.raises(BackupError, match="No space left on device"):
        create_backup(db, tmp_path / "b", "production", now=T0)
    assert list((tmp_path / "b").iterdir()) == []


def test_unreadable_latest_backup_counts_as_changed(tmp_path):
    db = _make_db(tmp_path / "sm.db")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "spend_manager_production_20261001T000000Z.db.gz").write_bytes(b"garbage")
    assert create_backup(db, tmp_path / "b", "production", now=T0).status == "created"


def test_list_backups_ignores_foreign_files(tmp_path):
    b = tmp_path / "b"
    b.mkdir()
    for name in (
        "spend_manager_production_20261020T100000Z.db.gz",
        "spend_manager_production_20261021T100000Z-2.db.gz",
        "spend_manager_production_20261021T100000Z.db.gz",
        ".spend_manager_production_20261022T100000Z.db.gz.tmp",
        "spend_manager_production_2026.db.gz",
        "notes.txt",
    ):
        (b / name).write_bytes(b"x")
    (b / "spend_manager_production_20261023T100000Z.db.gz").mkdir()  # a directory, not a file

    assert [x.file for x in list_backups(b, "production")] == [
        "spend_manager_production_20261021T100000Z-2.db.gz",
        "spend_manager_production_20261021T100000Z.db.gz",
        "spend_manager_production_20261020T100000Z.db.gz",
    ]
    assert list_backups(tmp_path / "missing", "production") == []
    assert latest_backup(None, "production") is None

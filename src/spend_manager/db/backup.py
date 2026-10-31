"""Backups, retention, restore and the run lock for the Spend Manager database.

See Functional Design v2 (2026-10-17-sqlite-cloud-production-db) §1.2–§1.6
and rules B-1..B-6, R-1..R-3, X-1..X-5.

A backup is a consistent snapshot taken with SQLite's online backup API from
a read-only connection (never a copy of the live file, which could miss data
still in the ``-wal`` file or catch a half-written page), verified with
``PRAGMA integrity_check``, gzip-compressed and written atomically, so a
synced folder such as Google Drive only ever sees complete files.
"""

from __future__ import annotations

import datetime
import fcntl
import gzip
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from spend_manager.db.schema import read_schema_version
from spend_manager.exceptions import BackupError, DatabaseInUseError, RestoreError

logger = logging.getLogger(__name__)

BACKUP_NAME = re.compile(
    r"^spend_manager_(development|production)_(\d{8}T\d{6}Z)(?:-(\d+))?\.db\.gz$"
)
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_CHUNK = 1 << 20


@dataclass(frozen=True)
class BackupInfo:
    file: str
    path: Path
    created_at: datetime.datetime
    sequence: int


@dataclass(frozen=True)
class BackupResult:
    status: Literal["created", "skipped_unchanged"]
    file: str
    sha256: str
    size_bytes: int
    pruned: list[str]


@dataclass(frozen=True)
class RestoreResult:
    restored_from: str
    safety_backup: str | None
    schema_version: int


# --- Listing ----------------------------------------------------------------


def list_backups(backup_dir: Path | None, env: str) -> list[BackupInfo]:
    """Backups of ``env`` in ``backup_dir``, newest first. Other files are ignored."""
    if backup_dir is None or not backup_dir.is_dir():
        return []
    found: list[BackupInfo] = []
    for path in backup_dir.iterdir():
        match = BACKUP_NAME.match(path.name)
        if not match or match.group(1) != env or not path.is_file():
            continue
        created = datetime.datetime.strptime(match.group(2), _TIMESTAMP_FORMAT).replace(
            tzinfo=datetime.UTC
        )
        found.append(BackupInfo(path.name, path, created, int(match.group(3) or 1)))
    found.sort(key=lambda b: (b.created_at, b.sequence), reverse=True)
    return found


def latest_backup(backup_dir: Path | None, env: str) -> BackupInfo | None:
    backups = list_backups(backup_dir, env)
    return backups[0] if backups else None


# --- Backup -------------------------------------------------------------------


def create_backup(
    db_path: Path,
    backup_dir: Path,
    env: str,
    *,
    keep: int = 30,
    force: bool = False,
    now: datetime.datetime | None = None,
) -> BackupResult:
    """Back up ``db_path`` into ``backup_dir`` (FD §1.2).

    Skips writing when the snapshot is identical to the newest backup unless
    ``force``. Prunes old backups after a successful write.

    Raises:
        BackupError: the database is missing/unreadable, fails the integrity
            check, or the backup file cannot be written. Nothing partial is
            left in ``backup_dir``.
    """
    now = now or datetime.datetime.now(datetime.UTC)
    with tempfile.TemporaryDirectory(prefix="spend_manager_backup_") as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        _snapshot(db_path, snapshot)
        digest = _sha256_file(snapshot)
        size = snapshot.stat().st_size

        latest = latest_backup(backup_dir, env)
        if not force and latest is not None and _sha256_gzip(latest.path) == digest:
            return BackupResult("skipped_unchanged", latest.file, digest, size, [])

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            final = _unique_path(backup_dir, env, now)
            _write_gzip_atomically(snapshot, final)
        except OSError as exc:
            raise BackupError(f"cannot write backup to {backup_dir}: {exc}") from exc

    pruned = prune(backup_dir, env, keep)
    logger.info("Backup created: %s (%d bytes, pruned %d)", final.name, size, len(pruned))
    return BackupResult("created", final.name, digest, size, pruned)


def _snapshot(db_path: Path, destination: Path) -> None:
    if not db_path.is_file():
        raise BackupError(f"database file not found: {db_path.name}")
    try:
        source = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                problems = _integrity_problems(target)
            finally:
                target.close()
        finally:
            source.close()
    except sqlite3.Error as exc:
        raise BackupError(f"cannot read database {db_path.name}: {exc}") from exc
    if problems:
        raise BackupError(f"integrity check failed for {db_path.name}: {'; '.join(problems[:5])}")


def _integrity_problems(conn: sqlite3.Connection) -> list[str]:
    """``PRAGMA integrity_check`` findings; empty when the database is ok."""
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    return [] if rows == [("ok",)] else [str(row[0]) for row in rows]


def _unique_path(backup_dir: Path, env: str, now: datetime.datetime) -> Path:
    stamp = now.astimezone(datetime.UTC).strftime(_TIMESTAMP_FORMAT)
    candidate = backup_dir / f"spend_manager_{env}_{stamp}.db.gz"
    sequence = 2
    while candidate.exists():
        candidate = backup_dir / f"spend_manager_{env}_{stamp}-{sequence}.db.gz"
        sequence += 1
    return candidate


def _write_gzip_atomically(source: Path, final: Path) -> None:
    temp = final.with_name(f".{final.name}.tmp")
    try:
        with source.open("rb") as src, temp.open("wb") as raw:
            # filename="" / mtime=0: the gzip header carries no temp name or clock.
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                shutil.copyfileobj(src, gz, _CHUNK)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temp, final)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_gzip(path: Path) -> str | None:
    """Digest of a backup's uncompressed content; None if it cannot be read."""
    digest = hashlib.sha256()
    try:
        with gzip.open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                digest.update(chunk)
    except (OSError, EOFError):
        return None
    return digest.hexdigest()


# --- Retention ----------------------------------------------------------------


def prune(backup_dir: Path, env: str, keep: int) -> list[str]:
    """Delete old backups of ``env``: keep the newest ``keep`` plus the newest of each month.

    Only files matching the backup name pattern for ``env`` are ever deleted
    (R-2). Deletion problems are logged, not raised: the new backup exists.
    """
    backups = list_backups(backup_dir, env)
    keep_files = {b.file for b in backups[:keep]}
    months: set[str] = set()
    for backup in backups:  # newest first → first seen per month is the newest
        month = backup.created_at.strftime("%Y%m")
        if month not in months:
            months.add(month)
            keep_files.add(backup.file)

    pruned: list[str] = []
    for backup in backups:
        if backup.file in keep_files:
            continue
        try:
            backup.path.unlink()
            pruned.append(backup.file)
        except OSError as exc:
            logger.warning("Could not delete old backup %s: %s", backup.file, exc)
    return pruned


# --- Run lock -------------------------------------------------------------------


class RunLock:
    """Advisory lock file ``<db>.lock`` (FD §1.6).

    The service holds it *shared* while running; ``restore`` needs it
    *exclusive*, so a restore can never replace the file under a running service.
    """

    def __init__(self, db_path: Path) -> None:
        self.path = db_path.with_name(db_path.name + ".lock")
        self._fd: int | None = None

    def acquire_shared(self) -> RunLock:
        return self._acquire(fcntl.LOCK_SH, "database is being restored")

    def acquire_exclusive(self) -> RunLock:
        return self._acquire(
            fcntl.LOCK_EX, "database is in use (stop the Spend Manager service first)"
        )

    def _acquire(self, mode: int, busy_message: str) -> RunLock:
        if self._fd is not None:
            raise RuntimeError("lock already held")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise DatabaseInUseError(busy_message) from None
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None


# --- Restore --------------------------------------------------------------------


def restore_backup(
    backup_file: Path,
    db_path: Path,
    backup_dir: Path | None,
    env: str,
    *,
    keep: int = 30,
) -> RestoreResult:
    """Replace ``db_path`` with the contents of ``backup_file`` (FD §1.5).

    Raises:
        RestoreError: the backup is missing/invalid or the safety backup failed.
        DatabaseInUseError: the service (or another restore) holds the run lock.
    """
    if not backup_file.is_file() or not backup_file.name.endswith(".db.gz"):
        raise RestoreError(f"not a backup file (*.db.gz): {backup_file.name}")

    lock = RunLock(db_path).acquire_exclusive()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=db_path.parent, prefix=".restore-", suffix=".db")
        os.close(fd)
        temp = Path(temp_name)
        try:
            _gunzip(backup_file, temp)
            version = _verify_restorable(temp, backup_file.name)

            safety: str | None = None
            if db_path.exists() and backup_dir is not None:
                try:
                    safety = create_backup(db_path, backup_dir, env, keep=keep, force=True).file
                except BackupError as exc:
                    raise RestoreError(f"safety backup failed, restore aborted: {exc}") from exc

            # No connections can be open: the exclusive run lock is held.
            for suffix in ("-wal", "-shm"):
                Path(f"{db_path}{suffix}").unlink(missing_ok=True)
            os.replace(temp, db_path)
        finally:
            temp.unlink(missing_ok=True)
    finally:
        lock.release()

    logger.info("Restored %s from %s (safety backup: %s)", db_path.name, backup_file.name, safety)
    return RestoreResult(backup_file.name, safety, version)


def _gunzip(source: Path, destination: Path) -> None:
    try:
        with gzip.open(source, "rb") as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst, _CHUNK)
    except (OSError, EOFError) as exc:
        raise RestoreError(f"not a valid gzip backup: {source.name}") from exc


def _verify_restorable(path: Path, name: str) -> int:
    try:
        conn = sqlite3.connect(path)
        try:
            if _integrity_problems(conn):
                raise RestoreError(f"backup failed the integrity check: {name}")
            has_schema = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
            ).fetchone()
            if not has_schema:
                raise RestoreError(f"not a Spend Manager database backup: {name}")
            return read_schema_version(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"backup is not a SQLite database: {name}") from exc

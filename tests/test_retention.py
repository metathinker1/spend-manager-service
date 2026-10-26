"""Tests for spend_manager.db.backup.prune (FD v2 §1.4, rules R-1..R-3)."""

from __future__ import annotations

import datetime

from spend_manager.db.backup import list_backups, prune


def _name(env: str, when: datetime.datetime, seq: int = 1) -> str:
    suffix = f"-{seq}" if seq > 1 else ""
    return f"spend_manager_{env}_{when:%Y%m%dT%H%M%SZ}{suffix}.db.gz"


def _populate(backup_dir, env, stamps):
    backup_dir.mkdir(exist_ok=True)
    for stamp in stamps:
        (backup_dir / _name(env, stamp)).write_bytes(b"x")


def test_keeps_recent_plus_newest_per_month(tmp_path):
    # 45 backups: 15 in Aug, 15 in Sep, 15 in Oct 2026 (one per day, 09:00 UTC).
    stamps = [
        datetime.datetime(2026, month, day, 9, 0, 0) for month in (8, 9, 10) for day in range(1, 16)
    ]
    backups = tmp_path / "b"
    _populate(backups, "production", stamps)

    pruned = prune(backups, "production", keep=20)

    kept = {b.created_at.replace(tzinfo=None) for b in list_backups(backups, "production")}
    newest_20 = set(sorted(stamps)[-20:])  # all of Oct + Sep 11–15
    monthly = {datetime.datetime(2026, 8, 15, 9), datetime.datetime(2026, 9, 15, 9)}
    assert kept == newest_20 | monthly
    assert len(pruned) == 45 - len(newest_20 | monthly)


def test_monthly_backups_are_kept_indefinitely(tmp_path):
    stamps = [datetime.datetime(2024 + (m // 12), m % 12 + 1, 1, 9) for m in range(30)]
    backups = tmp_path / "b"
    _populate(backups, "production", stamps)

    assert prune(backups, "production", keep=3) == []  # one per month: nothing to delete


def test_only_matching_files_of_the_same_environment_are_deleted(tmp_path):
    backups = tmp_path / "b"
    old = [datetime.datetime(2026, 10, d, 9) for d in range(1, 11)]
    _populate(backups, "production", old)
    _populate(backups, "development", old)
    foreign = ["notes.txt", "spend_manager_production_latest.db.gz", ".hidden.tmp"]
    for name in foreign:
        (backups / name).write_bytes(b"keep me")

    pruned = prune(backups, "production", keep=2)

    assert len(pruned) == 8  # keep Oct 10 + Oct 9 (Oct 10 is also the month's newest)
    remaining = {p.name for p in backups.iterdir()}
    assert all(name in remaining for name in foreign)
    assert len(list_backups(backups, "development")) == 10
    assert len(list_backups(backups, "production")) == 2


def test_prune_logs_and_continues_when_delete_fails(tmp_path, monkeypatch, caplog):
    from pathlib import Path

    backups = tmp_path / "b"
    _populate(backups, "production", [datetime.datetime(2026, 10, d, 9) for d in range(1, 6)])
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name.startswith("spend_manager_production_20261001"):
            raise PermissionError("read-only")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    pruned = prune(backups, "production", keep=1)

    assert len(pruned) == 3  # Oct 2, 3, 4 deleted; Oct 1 failed; Oct 5 kept (recent & monthly)
    assert "Could not delete old backup spend_manager_production_20261001" in caplog.text

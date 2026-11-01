"""MoneyWell snapshot listing (2026-10-31-spend-manager-ui FD §2.4 BR-4).

Names are made up per test; no fixed snapshot names or counts are assumed.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from spend_manager.exceptions import SourceNotFoundError
from spend_manager.services.snapshots import list_snapshots, snapshot_date


def make(root, name, *, folder=True, mtime=None):
    path = root / name
    if folder:
        path.mkdir()
    else:
        path.write_bytes(b"")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def listing(root):
    return [(s.name, s.kind, s.snapshot_date) for s in list_snapshots(root)]


def test_lists_folders_and_zips_newest_first(tmp_path):
    make(tmp_path, "Budget_20260101.zip", folder=False)
    make(tmp_path, "Budget_20260821")
    make(tmp_path, "Other_20260315")

    assert listing(tmp_path) == [
        ("Budget_20260821", "folder", dt.date(2026, 8, 21)),
        ("Other_20260315", "folder", dt.date(2026, 3, 15)),
        ("Budget_20260101.zip", "zip", dt.date(2026, 1, 1)),
    ]


def test_moneywell_backup_names(tmp_path):
    make(tmp_path, "Budget.moneywell@_@2026-05-01T10-00-00.zip", folder=False)
    make(tmp_path, "Budget.moneywell@_@2026-06-02T08-30-00")

    assert listing(tmp_path) == [
        ("Budget.moneywell@_@2026-06-02T08-30-00", "folder", dt.date(2026, 6, 2)),
        ("Budget.moneywell@_@2026-05-01T10-00-00.zip", "zip", dt.date(2026, 5, 1)),
    ]


def test_ignores_hidden_entries_and_other_files(tmp_path):
    make(tmp_path, ".DS_Store", folder=False)
    make(tmp_path, ".cache")
    make(tmp_path, "notes.txt", folder=False)
    make(tmp_path, "BACKUP.ZIP", folder=False)

    assert listing(tmp_path) == [("BACKUP.ZIP", "zip", None)]


def test_undated_last_then_by_modification_time_then_name(tmp_path):
    make(tmp_path, "Old", mtime=1_000_000)
    make(tmp_path, "New", mtime=2_000_000)
    make(tmp_path, "B_20260101", mtime=1_000_000)
    make(tmp_path, "A_20260101", mtime=1_000_000)
    make(tmp_path, "C_20260101", mtime=3_000_000)

    assert [name for name, _, _ in listing(tmp_path)] == [
        "C_20260101",
        "A_20260101",
        "B_20260101",
        "New",
        "Old",
    ]


def test_empty_root(tmp_path):
    assert list_snapshots(tmp_path) == []


def test_missing_root(tmp_path):
    with pytest.raises(SourceNotFoundError, match="store root directory not found"):
        list_snapshots(tmp_path / "missing")


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("SampleData_20261003", dt.date(2026, 10, 3)),
        ("Data_2026100", None),  # 7 digits
        ("Data_20261003_copy", None),  # not trailing
        ("Data_20261399", None),  # invalid date
        ("X@_@2026-02-30T00-00-00", None),  # invalid date
        ("X@_@2026-02-28T00-00-00", dt.date(2026, 2, 28)),
        ("Plain", None),
    ],
)
def test_snapshot_date(stem, expected):
    assert snapshot_date(stem) == expected

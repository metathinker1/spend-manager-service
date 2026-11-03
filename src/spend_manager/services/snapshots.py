"""List the MoneyWell snapshots available for import (Functional Design §2.4 BR-4).

Only names are inspected — nothing is opened or copied; the import validates
the chosen snapshot. No fixed snapshot names or counts are assumed.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from spend_manager.api.models import Snapshot
from spend_manager.exceptions import SourceNotFoundError

_DATED_SUFFIX = re.compile(r"_(\d{4})(\d{2})(\d{2})$")  # e.g. SampleData_20260821
_MONEYWELL_BACKUP = re.compile(r"@_@(\d{4})-(\d{2})-(\d{2})T")  # MoneyWell backup names


def list_snapshots(store_root: Path) -> list[Snapshot]:
    """Folders and ``.zip`` files directly under ``store_root``, newest first.

    Ordered by snapshot date (undated last), then modification time, then name.

    Raises:
        SourceNotFoundError: ``store_root`` is not a directory.
    """
    root = store_root.expanduser()
    if not root.is_dir():
        raise SourceNotFoundError("MoneyWell store root directory not found")

    found: list[tuple[Snapshot, float]] = []
    for entry in root.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            kind, stem = "folder", entry.name
        elif entry.is_file() and entry.suffix.lower() == ".zip":
            kind, stem = "zip", entry.name[: -len(entry.suffix)]
        else:
            continue
        snapshot = Snapshot(name=entry.name, kind=kind, snapshot_date=snapshot_date(stem))
        found.append((snapshot, entry.stat().st_mtime))

    found.sort(
        key=lambda item: (
            item[0].snapshot_date is None,
            -(item[0].snapshot_date or dt.date.min).toordinal(),
            -item[1],
            item[0].name,
        )
    )
    return [snapshot for snapshot, _ in found]


def snapshot_date(stem: str) -> dt.date | None:
    """The date in a trailing ``_YYYYMMDD`` or a MoneyWell ``@_@YYYY-MM-DDT`` suffix."""
    for pattern in (_DATED_SUFFIX, _MONEYWELL_BACKUP):
        match = pattern.search(stem)
        if match:
            try:
                return dt.date(*(int(part) for part in match.groups()))
            except ValueError:
                continue
    return None

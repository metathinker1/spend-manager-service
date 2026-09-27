"""Atomic CSV writer for the output table.

See Functional Design §1.4 and NFR-5 (a failed write must never leave a
partial or half-overwritten file).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd


def write_csv(df: pd.DataFrame, year: str, month: str, output_dir: Path) -> Path:
    """Write ``df`` to ``<output_dir>/transactions_<year><month>.csv``.

    Writes to a temporary file in the same directory first, then atomically
    renames it into place with ``os.replace``, so a re-import either fully
    replaces the prior file or leaves it untouched — never partially
    overwritten. The temporary file is removed if writing fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"transactions_{year}{month}.csv"

    fd, tmp_name = tempfile.mkstemp(
        dir=output_dir, prefix=f".transactions_{year}{month}_", suffix=".csv.tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        os.close(fd)
        df.to_csv(tmp_path, index=False, encoding="utf-8", lineterminator="\n")
        os.replace(tmp_path, final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return final_path

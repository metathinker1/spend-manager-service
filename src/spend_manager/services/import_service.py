"""Orchestrates one month's MoneyWell import: read -> parse -> table -> CSV.

See Functional Design §1.1 (data flow) and Requirements FR-1..FR-10.
"""

from __future__ import annotations

import logging
from pathlib import Path

from spend_manager.config import Settings
from spend_manager.exceptions import ParseError, SourceFileNotFoundError
from spend_manager.output.csv_writer import write_csv
from spend_manager.output.table import build_table
from spend_manager.parsing.moneywell_parser import parse

logger = logging.getLogger(__name__)


def source_file_name(year: str, month: str) -> str:
    """The MoneyWell export file name for a given year/month (FR-2)."""
    return f"RobBudget_{year}{month}_Details.txt"


def import_month(year: str, month: str, settings: Settings) -> Path:
    """Import one month's transactions and write the output CSV.

    ``year`` and ``month`` must already be validated by the caller (see
    ``spend_manager.api.imports``) — this function trusts them to build the
    source file name and does not re-validate their shape.

    Raises:
        SourceFileNotFoundError: no matching source file exists.
        ParseError: the source path is not a regular file, is unreadable, or
            its contents do not conform to the expected format.
    """
    file_name = source_file_name(year, month)
    source_path = settings.source_dir / file_name

    if not source_path.exists():
        raise SourceFileNotFoundError(file_name)
    if not source_path.is_file():
        # e.g. a directory happens to share the derived file name.
        raise ParseError(None, f"source path exists but is not a regular file: {file_name}")

    try:
        text = source_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ParseError(None, f"source file is not readable: {file_name}") from exc

    parsed = parse(text)

    logger.info(
        "Parsed %s: %d transaction(s) across %d categor(y/ies)",
        file_name,
        len(parsed.transactions),
        len(parsed.categories),
    )

    df = build_table(parsed, year, month)
    output_path = write_csv(df, year, month, settings.output_dir_path)

    logger.info("Wrote %d row(s) to %s", len(df), output_path)
    return output_path

"""Integration tests for spend_manager.services.import_service.import_month.

Exercises the full read -> parse -> table -> CSV pipeline against tmp
directories, using the synthetic fixture as source content.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from spend_manager.config import Settings
from spend_manager.exceptions import ParseError, SourceFileNotFoundError
from spend_manager.services.import_service import import_month, source_file_name

SYNTHETIC_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_202501.txt"


def _settings(source_dir, output_dir) -> Settings:
    return Settings(moneywell_source_dir=str(source_dir), output_dir=str(output_dir))


def test_source_file_name_pattern():
    assert source_file_name("2025", "10") == "RobBudget_202510_Details.txt"


def test_import_month_end_to_end(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    (source_dir / "RobBudget_202501_Details.txt").write_bytes(SYNTHETIC_FIXTURE.read_bytes())

    output_path = import_month("2025", "01", _settings(source_dir, output_dir))

    assert output_path == output_dir / "transactions_202501.csv"
    df = pd.read_csv(output_path, dtype=str)
    assert len(df) == 4
    assert set(df["Category"]) == {"Groceries", "Salary", "Transfer"}
    assert (df["Month"] == "2025-01").all()


def test_missing_source_file_raises_source_file_not_found(tmp_path):
    settings = _settings(tmp_path / "source", tmp_path / "output")

    with pytest.raises(SourceFileNotFoundError) as exc_info:
        import_month("2025", "01", settings)

    assert exc_info.value.file_name == "RobBudget_202501_Details.txt"


def test_directory_in_place_of_file_raises_parse_error(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "RobBudget_202501_Details.txt").mkdir()

    with pytest.raises(ParseError, match="not a regular file"):
        import_month("2025", "01", _settings(source_dir, tmp_path / "output"))


def test_bom_prefixed_source_file_parses_correctly(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    content = b"\xef\xbb\xbf" + SYNTHETIC_FIXTURE.read_bytes()
    (source_dir / "RobBudget_202501_Details.txt").write_bytes(content)

    output_path = import_month("2025", "01", _settings(source_dir, output_dir))

    df = pd.read_csv(output_path, dtype=str)
    assert len(df) == 4


def test_malformed_source_file_raises_parse_error(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "RobBudget_202501_Details.txt").write_text("not\tthe\tright\theader\n")

    with pytest.raises(ParseError, match="header"):
        import_month("2025", "01", _settings(source_dir, tmp_path / "output"))

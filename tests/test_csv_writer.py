"""Unit tests for spend_manager.output.csv_writer.write_csv."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from spend_manager.output.csv_writer import write_csv


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame([{"Month": "2025-10", "Payee": "A", "Amount": "-1.00"}])


def test_writes_expected_file_name(tmp_path):
    out = write_csv(_sample_df(), "2025", "10", tmp_path)

    assert out == tmp_path / "transactions_202510.csv"
    assert out.is_file()


def test_creates_output_dir_if_missing(tmp_path):
    target_dir = tmp_path / "nested" / "output"
    assert not target_dir.exists()

    write_csv(_sample_df(), "2025", "10", target_dir)

    assert target_dir.is_dir()


def test_written_csv_content_round_trips(tmp_path):
    df = _sample_df()

    out = write_csv(df, "2025", "10", tmp_path)
    read_back = pd.read_csv(out, dtype=str)

    assert list(read_back.columns) == list(df.columns)
    assert read_back.iloc[0]["Payee"] == "A"


def test_reimport_overwrites_prior_file(tmp_path):
    write_csv(_sample_df(), "2025", "10", tmp_path)
    second_df = pd.DataFrame([{"Month": "2025-10", "Payee": "B", "Amount": "-2.00"}])

    out = write_csv(second_df, "2025", "10", tmp_path)
    read_back = pd.read_csv(out, dtype=str)

    assert len(read_back) == 1
    assert read_back.iloc[0]["Payee"] == "B"


def test_no_temp_or_partial_file_left_when_write_fails(tmp_path):
    failing_df = MagicMock()
    failing_df.to_csv.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        write_csv(failing_df, "2025", "10", tmp_path)

    assert not (tmp_path / "transactions_202510.csv").exists()
    assert list(tmp_path.iterdir()) == []


def test_failed_write_does_not_disturb_existing_file(tmp_path):
    write_csv(_sample_df(), "2025", "10", tmp_path)
    before = (tmp_path / "transactions_202510.csv").read_text()

    failing_df = MagicMock()
    failing_df.to_csv.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        write_csv(failing_df, "2025", "10", tmp_path)

    after = (tmp_path / "transactions_202510.csv").read_text()
    assert after == before
    # Only the original file remains — no leftover .tmp artifacts.
    assert [p.name for p in tmp_path.iterdir()] == ["transactions_202510.csv"]

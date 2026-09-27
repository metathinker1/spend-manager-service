"""Optional integration test against the real MoneyWell export.

Skipped when the real file is not present on this machine, so the suite
still passes in CI or on another developer's machine. When present, it
verifies the parser against known-correct counts derived from the file
itself (see Requirements — Source File Analysis).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from spend_manager.parsing.moneywell_parser import parse

REAL_SAMPLE_PATH = Path(
    "~/Google Drive/My Drive/AppData/MoneyWell/RobBudget_202510_Details.txt"
).expanduser()

pytestmark = pytest.mark.skipif(
    not REAL_SAMPLE_PATH.is_file(),
    reason=f"real MoneyWell sample not found at {REAL_SAMPLE_PATH}",
)


def test_real_sample_counts_and_total():
    text = REAL_SAMPLE_PATH.read_text(encoding="utf-8-sig")

    parsed = parse(text)

    assert len(parsed.transactions) == 109
    assert len(parsed.categories) == 31
    assert parsed.report_total == Decimal("-987.98")
    assert "Report Total" not in [c.label for c in parsed.categories]

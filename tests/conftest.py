from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SYNTHETIC_FIXTURE = FIXTURES_DIR / "synthetic_202501.txt"

REAL_SAMPLE_PATH = Path(
    "~/Google Drive/My Drive/AppData/MoneyWell/RobBudget_202510_Details.txt"
).expanduser()


@pytest.fixture
def synthetic_text() -> str:
    return SYNTHETIC_FIXTURE.read_text(encoding="utf-8")

from __future__ import annotations

import os
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    # P-3: the test suite must never run against production settings.
    if os.environ.get("SPEND_MANAGER_ENV", "").strip().lower() == "production":
        pytest.exit("Refusing to run tests with SPEND_MANAGER_ENV=production", returncode=2)


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SYNTHETIC_FIXTURE = FIXTURES_DIR / "synthetic_202501.txt"

REAL_SAMPLE_PATH = Path(
    "~/Google Drive/My Drive/AppData/MoneyWell/RobBudget_202510_Details.txt"
).expanduser()


@pytest.fixture
def synthetic_text() -> str:
    return SYNTHETIC_FIXTURE.read_text(encoding="utf-8")

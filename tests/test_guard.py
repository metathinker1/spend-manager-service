"""The test suite refuses to run with production settings (FD v2 §1.12, P-3)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pytest_refuses_production_environment():
    env = {**os.environ, "SPEND_MANAGER_ENV": "production"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_table.py",
            "-q",
            "--no-cov",
            "-p",
            "no:cacheprovider",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2
    assert (
        "Refusing to run tests with SPEND_MANAGER_ENV=production" in result.stdout + result.stderr
    )

"""Application settings, read from environment variables.

See Functional Design → Domain Entities (``Settings``) and Requirements
FR-3 / FR-10.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: src/spend_manager/config.py -> src/spend_manager -> src -> root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_SOURCE_DIR = "~/Google Drive/My Drive/AppData/MoneyWell"
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "output"


class Settings(BaseSettings):
    """Runtime configuration.

    ``source_dir`` and ``output_dir`` are read from the ``MONEYWELL_SOURCE_DIR``
    and ``OUTPUT_DIR`` environment variables respectively (case-insensitive),
    falling back to the defaults below. ``~`` is expanded for both.
    """

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    moneywell_source_dir: str = _DEFAULT_SOURCE_DIR
    output_dir: str = str(_DEFAULT_OUTPUT_DIR)

    @property
    def source_dir(self) -> Path:
        return Path(self.moneywell_source_dir).expanduser()

    @property
    def output_dir_path(self) -> Path:
        return Path(self.output_dir).expanduser()


def get_settings() -> Settings:
    """Build a fresh ``Settings`` instance from the current environment.

    Not cached: tests set environment variables per-case and expect each
    call to reflect the current environment.
    """
    return Settings()

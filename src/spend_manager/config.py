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
_DEFAULT_STORE_ROOT = "~/Project.SpendManager/MoneyWell_Data"
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "spend_manager.db"


class Settings(BaseSettings):
    """Runtime configuration.

    Each field is read from the environment variable of the same name
    (case-insensitive), falling back to the defaults below. ``~`` is
    expanded for every path.

    - ``MONEYWELL_SOURCE_DIR`` / ``OUTPUT_DIR``: text-export CSV import (deprecated).
    - ``MONEYWELL_STORE_ROOT``: directory holding MoneyWell budget folders / zips.
    - ``SPEND_MANAGER_DB_PATH``: the Spend Manager SQLite database file.
    """

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    moneywell_source_dir: str = _DEFAULT_SOURCE_DIR
    output_dir: str = str(_DEFAULT_OUTPUT_DIR)
    moneywell_store_root: str = _DEFAULT_STORE_ROOT
    spend_manager_db_path: str = str(_DEFAULT_DB_PATH)

    @property
    def source_dir(self) -> Path:
        return Path(self.moneywell_source_dir).expanduser()

    @property
    def output_dir_path(self) -> Path:
        return Path(self.output_dir).expanduser()

    @property
    def store_root(self) -> Path:
        return Path(self.moneywell_store_root).expanduser()

    @property
    def db_path(self) -> Path:
        return Path(self.spend_manager_db_path).expanduser()


def get_settings() -> Settings:
    """Build a fresh ``Settings`` instance from the current environment.

    Not cached: tests set environment variables per-case and expect each
    call to reflect the current environment.
    """
    return Settings()

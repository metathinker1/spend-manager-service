"""Application settings, read from environment variables and per-environment files.

See Functional Design v2 (2026-10-17-sqlite-cloud-production-db) §1.1, §2.1
and §3.1. ``SPEND_MANAGER_ENV`` selects ``development`` (default) or
``production``; ``get_settings`` then loads the optional ``.env.<env>`` file
from the project root (real environment variables take precedence) and
validates the result.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from spend_manager.exceptions import ConfigurationError

# Repository root: src/spend_manager/config.py -> src/spend_manager -> src -> root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DEFAULT_SOURCE_DIR = "~/Google Drive/My Drive/AppData/MoneyWell"
_DEFAULT_OUTPUT_DIR = _PROJECT_ROOT / "data" / "output"
_DEFAULT_STORE_ROOT = "~/Project.SpendManager/MoneyWell_Data"
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "spend_manager.db"

ENV_VAR = "SPEND_MANAGER_ENV"

# Where ``get_settings`` looks for ``.env.<env>`` files. Module-level so tests
# can point it at a temporary directory.
ENV_FILE_DIR = _PROJECT_ROOT

# macOS mounts Google Drive, iCloud Drive, Dropbox and OneDrive here
# (``~/Google Drive`` is a symlink into it). A live SQLite database must never
# live in a synced folder (C-5).
CLOUD_STORAGE_ROOT = Path.home() / "Library" / "CloudStorage"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Runtime configuration.

    Each field is read from the environment variable of the same name
    (case-insensitive), falling back to the defaults below. ``~`` is
    expanded for every path. Building ``Settings(...)`` directly never reads
    a ``.env`` file; only ``get_settings()`` does.

    - ``SPEND_MANAGER_ENV``: ``development`` (default) or ``production``.
    - ``SPEND_MANAGER_DB_PATH``: the Spend Manager SQLite database file.
    - ``SPEND_MANAGER_BACKUP_DIR``: where backups are written (required in production).
    - ``SPEND_MANAGER_BACKUP_KEEP``: most recent backups kept (plus one per month).
    - ``MONEYWELL_STORE_ROOT``: directory holding MoneyWell snapshot folders / zips.
    - ``MONEYWELL_SOURCE_DIR`` / ``OUTPUT_DIR``: text-export CSV import (deprecated).
    """

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    spend_manager_env: Environment = Environment.DEVELOPMENT
    moneywell_source_dir: str = _DEFAULT_SOURCE_DIR
    output_dir: str = str(_DEFAULT_OUTPUT_DIR)
    moneywell_store_root: str = _DEFAULT_STORE_ROOT
    spend_manager_db_path: str = str(_DEFAULT_DB_PATH)
    spend_manager_backup_dir: str | None = None
    spend_manager_backup_keep: int = Field(default=30, ge=1)

    @property
    def environment(self) -> Environment:
        return self.spend_manager_env

    @property
    def is_production(self) -> bool:
        return self.spend_manager_env is Environment.PRODUCTION

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

    @property
    def backup_dir(self) -> Path | None:
        if not self.spend_manager_backup_dir:
            return None
        return Path(self.spend_manager_backup_dir).expanduser()

    @property
    def backups_enabled(self) -> bool:
        return self.backup_dir is not None

    def validate_environment(self) -> None:
        """Apply rules C-3..C-6 (Functional Design §3.1).

        Raises:
            ConfigurationError: the first rule that is violated.
        """
        db_path = self.db_path.resolve()
        backup_dir = self.backup_dir.resolve() if self.backup_dir else None
        project_data = (_PROJECT_ROOT / "data").resolve()

        if self.is_production:
            # C-3
            if "spend_manager_db_path" not in self.model_fields_set:
                raise ConfigurationError(
                    "production requires SPEND_MANAGER_DB_PATH to be set explicitly"
                )
            if db_path == _DEFAULT_DB_PATH.resolve():
                raise ConfigurationError(
                    "production SPEND_MANAGER_DB_PATH must not be the development default "
                    f"({_DEFAULT_DB_PATH})"
                )
            # C-4
            if backup_dir is None:
                raise ConfigurationError("production requires SPEND_MANAGER_BACKUP_DIR")
            if not backup_dir.parent.is_dir():
                raise ConfigurationError(
                    f"SPEND_MANAGER_BACKUP_DIR parent folder does not exist: {backup_dir.parent} "
                    "(is Google Drive running and signed in?)"
                )
            # C-5
            if _is_within(db_path, CLOUD_STORAGE_ROOT.resolve()):
                raise ConfigurationError(
                    "production SPEND_MANAGER_DB_PATH must not be inside a synced cloud folder "
                    f"({CLOUD_STORAGE_ROOT}); keep the live database local and back it up instead"
                )
            if _is_within(db_path, backup_dir):
                raise ConfigurationError(
                    "SPEND_MANAGER_DB_PATH must not be inside SPEND_MANAGER_BACKUP_DIR"
                )

        # C-6 (both environments): nothing outside the git-ignored data/ folder
        # may live inside the project's working tree.
        for name, path in (
            ("SPEND_MANAGER_DB_PATH", db_path),
            ("SPEND_MANAGER_BACKUP_DIR", backup_dir),
        ):
            if (
                path is not None
                and _is_within(path, _PROJECT_ROOT)
                and not _is_within(path, project_data)
            ):
                raise ConfigurationError(
                    f"{name} must not be inside the project folder except under data/: {path}"
                )


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


def selected_environment() -> Environment:
    """The environment named by ``SPEND_MANAGER_ENV`` (C-1)."""
    raw = os.environ.get(ENV_VAR, Environment.DEVELOPMENT.value).strip().lower()
    try:
        return Environment(raw)
    except ValueError:
        raise ConfigurationError(
            f"{ENV_VAR} must be 'development' or 'production', got {raw!r}"
        ) from None


def get_settings() -> Settings:
    """Build validated settings for the selected environment.

    Not cached: tests and the CLI change environment variables and expect
    each call to reflect them.

    Raises:
        ConfigurationError: invalid environment, env file or setting values.
    """
    env = selected_environment()
    env_file = ENV_FILE_DIR / f".env.{env.value}"
    has_env_file = env_file.is_file()

    if has_env_file:
        # C-2: an env file may not switch to a different environment.
        file_values = {k.upper(): v for k, v in dotenv_values(env_file).items()}
        file_env = (file_values.get(ENV_VAR) or "").strip().lower()
        if file_env and file_env != env.value:
            raise ConfigurationError(
                f"{env_file.name} sets {ENV_VAR}={file_env} but was selected for {env.value}"
            )

    try:
        settings = Settings(
            _env_file=env_file if has_env_file else None,
            spend_manager_env=env,
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigurationError(f"invalid settings: {problems}") from None

    settings.validate_environment()
    return settings

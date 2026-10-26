"""Tests for spend_manager.config (Functional Design v2 §1.1, §3.1 rules C-1..C-7)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from spend_manager import config
from spend_manager.config import Environment, Settings, get_settings
from spend_manager.exceptions import ConfigurationError

_SETTING_VARS = (
    "SPEND_MANAGER_ENV",
    "SPEND_MANAGER_DB_PATH",
    "SPEND_MANAGER_BACKUP_DIR",
    "SPEND_MANAGER_BACKUP_KEEP",
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated settings environment: no real env vars, env files in tmp, fake CloudStorage."""
    for name in _SETTING_VARS:
        monkeypatch.delenv(name, raising=False)
    env_dir = tmp_path / "project"
    env_dir.mkdir()
    cloud = tmp_path / "Library" / "CloudStorage"
    (cloud / "GoogleDrive-me@example.com" / "My Drive").mkdir(parents=True)
    monkeypatch.setattr(config, "ENV_FILE_DIR", env_dir)
    monkeypatch.setattr(config, "CLOUD_STORAGE_ROOT", cloud)
    return env_dir, cloud, tmp_path


def _prod(monkeypatch, tmp_path: Path, **overrides: str) -> None:
    values = {
        "SPEND_MANAGER_ENV": "production",
        "SPEND_MANAGER_DB_PATH": str(tmp_path / "prod" / "spend_manager.db"),
        "SPEND_MANAGER_BACKUP_DIR": str(tmp_path / "Drive" / "SpendManagerBackups"),
    }
    values.update(overrides)
    (tmp_path / "Drive").mkdir(exist_ok=True)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_development_is_the_default_and_unchanged(env):
    settings = get_settings()
    assert settings.environment is Environment.DEVELOPMENT
    assert not settings.is_production
    assert settings.db_path.name == "spend_manager.db"
    assert settings.db_path.parent.name == "data"
    assert not settings.backups_enabled
    assert settings.spend_manager_backup_keep == 30


def test_settings_built_directly_never_read_env_files(env):
    env_dir, _, tmp_path = env
    (env_dir / ".env.development").write_text(f"SPEND_MANAGER_DB_PATH={tmp_path}/x.db\n")
    assert Settings().db_path.name == "spend_manager.db"


def test_c1_invalid_environment(env, monkeypatch):
    monkeypatch.setenv("SPEND_MANAGER_ENV", "staging")
    with pytest.raises(ConfigurationError, match="must be 'development' or 'production'"):
        get_settings()


def test_environment_name_is_case_insensitive(env, monkeypatch):
    _, _, tmp_path = env
    _prod(monkeypatch, tmp_path, SPEND_MANAGER_ENV=" Production ")
    assert get_settings().is_production


def test_env_file_is_loaded_for_selected_environment(env):
    env_dir, _, tmp_path = env
    (env_dir / ".env.development").write_text(
        f"SPEND_MANAGER_DB_PATH={tmp_path}/dev/spend_manager.db\n"
        f"SPEND_MANAGER_BACKUP_DIR={tmp_path}/dev-backups\n"
        "UNRELATED_SETTING=ignored\n"
    )
    settings = get_settings()
    assert settings.db_path == tmp_path / "dev" / "spend_manager.db"
    assert settings.backup_dir == tmp_path / "dev-backups"


def test_environment_variables_override_env_file(env, monkeypatch):
    env_dir, _, tmp_path = env
    (env_dir / ".env.development").write_text(f"SPEND_MANAGER_DB_PATH={tmp_path}/file.db\n")
    monkeypatch.setenv("SPEND_MANAGER_DB_PATH", str(tmp_path / "var.db"))
    assert get_settings().db_path == tmp_path / "var.db"


def test_production_reads_env_production_file(env, monkeypatch):
    env_dir, _, tmp_path = env
    (tmp_path / "Drive").mkdir()
    (env_dir / ".env.production").write_text(
        "SPEND_MANAGER_ENV=production\n"
        f"SPEND_MANAGER_DB_PATH={tmp_path}/prod/spend_manager.db\n"
        f"SPEND_MANAGER_BACKUP_DIR={tmp_path}/Drive/SpendManagerBackups\n"
        "SPEND_MANAGER_BACKUP_KEEP=10\n"
    )
    monkeypatch.setenv("SPEND_MANAGER_ENV", "production")
    settings = get_settings()
    assert settings.is_production
    assert settings.backups_enabled
    assert settings.spend_manager_backup_keep == 10


def test_c2_env_file_cannot_switch_environment(env):
    env_dir, _, _ = env
    (env_dir / ".env.development").write_text("SPEND_MANAGER_ENV=production\n")
    with pytest.raises(ConfigurationError, match=r"\.env\.development sets SPEND_MANAGER_ENV"):
        get_settings()


def test_c2_production_file_naming_development_is_rejected(env, monkeypatch):
    env_dir, _, tmp_path = env
    _prod(monkeypatch, tmp_path)
    (env_dir / ".env.production").write_text("spend_manager_env=development\n")
    with pytest.raises(ConfigurationError, match="selected for production"):
        get_settings()


def test_c3_production_requires_explicit_db_path(env, monkeypatch):
    _, _, tmp_path = env
    _prod(monkeypatch, tmp_path)
    monkeypatch.delenv("SPEND_MANAGER_DB_PATH")
    with pytest.raises(ConfigurationError, match="SPEND_MANAGER_DB_PATH to be set explicitly"):
        get_settings()


def test_c3_production_cannot_use_development_default(env, monkeypatch):
    _, _, tmp_path = env
    _prod(monkeypatch, tmp_path, SPEND_MANAGER_DB_PATH=str(config._DEFAULT_DB_PATH))
    with pytest.raises(ConfigurationError, match="development default"):
        get_settings()


def test_c4_production_requires_backup_dir(env, monkeypatch):
    _, _, tmp_path = env
    _prod(monkeypatch, tmp_path)
    monkeypatch.delenv("SPEND_MANAGER_BACKUP_DIR")
    with pytest.raises(ConfigurationError, match="requires SPEND_MANAGER_BACKUP_DIR"):
        get_settings()


def test_c4_backup_dir_parent_must_exist(env, monkeypatch):
    _, _, tmp_path = env
    _prod(monkeypatch, tmp_path, SPEND_MANAGER_BACKUP_DIR=str(tmp_path / "NoDrive" / "Backups"))
    with pytest.raises(ConfigurationError, match="Google Drive running"):
        get_settings()


def test_c5_production_db_not_in_cloud_storage(env, monkeypatch):
    _, cloud, tmp_path = env
    db = cloud / "GoogleDrive-me@example.com" / "My Drive" / "spend_manager.db"
    _prod(monkeypatch, tmp_path, SPEND_MANAGER_DB_PATH=str(db))
    with pytest.raises(ConfigurationError, match="synced cloud folder"):
        get_settings()


def test_c5_detects_cloud_storage_through_a_symlink(env, monkeypatch):
    # ~/Google Drive is a symlink into ~/Library/CloudStorage on macOS.
    _, cloud, tmp_path = env
    link = tmp_path / "Google Drive"
    os.symlink(cloud / "GoogleDrive-me@example.com", link)
    _prod(monkeypatch, tmp_path, SPEND_MANAGER_DB_PATH=str(link / "My Drive" / "sm.db"))
    with pytest.raises(ConfigurationError, match="synced cloud folder"):
        get_settings()


def test_c5_db_not_inside_backup_dir(env, monkeypatch):
    _, _, tmp_path = env
    backups = tmp_path / "Drive" / "SpendManagerBackups"
    _prod(monkeypatch, tmp_path, SPEND_MANAGER_DB_PATH=str(backups / "live.db"))
    with pytest.raises(ConfigurationError, match="inside SPEND_MANAGER_BACKUP_DIR"):
        get_settings()


def test_backup_dir_in_google_drive_is_allowed(env, monkeypatch):
    _, cloud, tmp_path = env
    backups = cloud / "GoogleDrive-me@example.com" / "My Drive" / "SpendManagerBackups"
    _prod(monkeypatch, tmp_path, SPEND_MANAGER_BACKUP_DIR=str(backups))
    assert get_settings().backup_dir == backups


def test_c6_paths_inside_project_only_under_data(env, monkeypatch):
    project_src = config._PROJECT_ROOT / "src" / "spend_manager.db"
    monkeypatch.setenv("SPEND_MANAGER_DB_PATH", str(project_src))
    with pytest.raises(ConfigurationError, match="inside the project folder"):
        get_settings()

    monkeypatch.setenv("SPEND_MANAGER_DB_PATH", str(config._PROJECT_ROOT / "data" / "other.db"))
    monkeypatch.setenv("SPEND_MANAGER_BACKUP_DIR", str(config._PROJECT_ROOT / "backups"))
    with pytest.raises(ConfigurationError, match="SPEND_MANAGER_BACKUP_DIR must not be inside"):
        get_settings()


def test_c7_backup_keep_must_be_positive(env, monkeypatch):
    monkeypatch.setenv("SPEND_MANAGER_BACKUP_KEEP", "0")
    with pytest.raises(ConfigurationError, match="spend_manager_backup_keep"):
        get_settings()


def test_valid_production_settings(env, monkeypatch):
    _, _, tmp_path = env
    _prod(monkeypatch, tmp_path)
    settings = get_settings()
    assert settings.is_production
    assert settings.db_path == tmp_path / "prod" / "spend_manager.db"
    assert settings.backup_dir == tmp_path / "Drive" / "SpendManagerBackups"

"""Tests for the app lifespan: banner, run lock, shutdown backup (FD v2 §1.11)."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from spend_manager import main
from spend_manager.config import Settings
from spend_manager.db.backup import RunLock, list_backups
from spend_manager.db.schema import ensure_schema, open_db
from spend_manager.exceptions import ConfigurationError, DatabaseInUseError


def _settings(tmp_path, **values) -> Settings:
    return Settings(spend_manager_db_path=str(tmp_path / "sm.db"), **values)


def _migrated(path):
    conn = open_db(path)
    ensure_schema(conn)
    conn.close()


def test_banner_lock_and_shutdown_backup(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="spend_manager")
    _migrated(tmp_path / "sm.db")
    settings = _settings(tmp_path, spend_manager_backup_dir=str(tmp_path / "b"))
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    with TestClient(main.app):
        assert "Spend Manager starting: env=development" in caplog.text
        assert f"backups={tmp_path / 'b'}" in caplog.text
        with pytest.raises(DatabaseInUseError):  # shared lock held while running
            RunLock(settings.db_path).acquire_exclusive()

    assert "Shutdown backup: created spend_manager_development_" in caplog.text
    assert len(list_backups(tmp_path / "b", "development")) == 1
    RunLock(settings.db_path).acquire_exclusive().release()  # released on shutdown

    with TestClient(main.app):
        pass
    assert "Shutdown backup: skipped (unchanged since" in caplog.text


def test_no_shutdown_backup_without_backup_dir_or_db(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="spend_manager")
    monkeypatch.setattr(main, "get_settings", lambda: _settings(tmp_path))
    with TestClient(main.app):
        pass
    assert "backups=disabled" in caplog.text
    assert "Shutdown backup" not in caplog.text

    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: _settings(tmp_path, spend_manager_backup_dir=str(tmp_path / "b")),
    )
    with TestClient(main.app):  # backups configured but no database yet
        pass
    assert "Shutdown backup" not in caplog.text


def test_shutdown_backup_failure_is_logged(tmp_path, monkeypatch, caplog):
    from spend_manager.exceptions import BackupError

    _migrated(tmp_path / "sm.db")
    settings = _settings(tmp_path, spend_manager_backup_dir=str(tmp_path / "b"))
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    def failing(*args, **kwargs):
        raise BackupError("Google Drive folder not writable")

    monkeypatch.setattr(main, "create_backup", failing)
    with TestClient(main.app):
        pass
    assert "Shutdown backup failed: Google Drive folder not writable" in caplog.text
    RunLock(settings.db_path).acquire_exclusive().release()  # lock still released


def test_startup_fails_on_configuration_error(monkeypatch):
    def bad():
        raise ConfigurationError("production requires SPEND_MANAGER_BACKUP_DIR")

    monkeypatch.setattr(main, "get_settings", bad)
    with pytest.raises(ConfigurationError), TestClient(main.app):
        pass


def test_startup_refused_during_restore(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    restore_lock = RunLock(settings.db_path).acquire_exclusive()
    try:
        with pytest.raises(DatabaseInUseError, match="being restored"), TestClient(main.app):
            pass
    finally:
        restore_lock.release()

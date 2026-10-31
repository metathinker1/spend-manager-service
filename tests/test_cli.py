"""Tests for the spend-manager-db CLI (FD v2 §1.9)."""

from __future__ import annotations

import sqlite3

import pytest

from spend_manager import cli, config
from spend_manager.db.backup import RunLock, list_backups
from spend_manager.db.schema import MIGRATIONS, ensure_schema, open_db

LATEST = MIGRATIONS[-1][0]


@pytest.fixture
def dev(tmp_path, monkeypatch):
    """Development env with a DB path and backup dir in tmp; no real env files.

    ``SPEND_MANAGER_ENV`` is *set* (not just deleted) so monkeypatch restores it
    after tests where ``cli.main(["--env", …])`` assigns ``os.environ`` itself.
    """
    monkeypatch.setenv("SPEND_MANAGER_ENV", "development")
    monkeypatch.setattr(config, "ENV_FILE_DIR", tmp_path / "no-env-files")
    db = tmp_path / "dev" / "sm.db"
    backups = tmp_path / "backups"
    monkeypatch.setenv("SPEND_MANAGER_DB_PATH", str(db))
    monkeypatch.setenv("SPEND_MANAGER_BACKUP_DIR", str(backups))
    return db, backups


@pytest.fixture
def prod(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEND_MANAGER_ENV", "development")  # restored after cli sets it
    monkeypatch.setattr(config, "ENV_FILE_DIR", tmp_path / "no-env-files")
    monkeypatch.setattr(config, "CLOUD_STORAGE_ROOT", tmp_path / "CloudStorage")
    (tmp_path / "Drive").mkdir()
    db = tmp_path / "prod" / "spend_manager.db"
    backups = tmp_path / "Drive" / "SpendManagerBackups"
    monkeypatch.setenv("SPEND_MANAGER_DB_PATH", str(db))
    monkeypatch.setenv("SPEND_MANAGER_BACKUP_DIR", str(backups))
    return db, backups


def _answer(monkeypatch, reply: bool, prompts: list[str]):
    def fake(prompt: str) -> bool:
        prompts.append(prompt)
        return reply

    monkeypatch.setattr(cli, "confirm", fake)


def _add(db, uid):
    conn = open_db(db)
    conn.execute(
        "INSERT INTO account (moneywell_uid, name, type) VALUES (?, 'x', 'checking')", (uid,)
    )
    conn.close()


def _make_v1_database(db, monkeypatch):
    from spend_manager.db import schema

    conn = open_db(db)
    with monkeypatch.context() as m:
        m.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:1])
        ensure_schema(conn)
    conn.close()


def _uids(db):
    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT moneywell_uid FROM account ORDER BY id")]
    finally:
        conn.close()


# --- status ---------------------------------------------------------------------


def test_status_missing_database(dev, capsys):
    assert cli.main(["status"]) == 1
    out = capsys.readouterr().out
    assert "environment:  development" in out
    assert "database file does not exist" in out
    assert "last backup:  none" in out


def test_status_up_to_date_with_backup(dev, capsys):
    db, _ = dev
    assert cli.main(["migrate"]) == 0
    assert cli.main(["backup"]) == 0
    capsys.readouterr()

    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert f"schema:       v{LATEST} (required v{LATEST}) — up to date" in out
    assert "last backup:  spend_manager_development_" in out
    assert "min ago" in out


def test_status_migration_needed(dev, capsys):
    db, _ = dev
    open_db(db).close()
    assert cli.main(["status"]) == 1
    assert "v0 (required" in capsys.readouterr().out


# --- migrate ----------------------------------------------------------------------


def test_development_migrate_needs_no_prompt(dev, capsys, monkeypatch):
    prompts: list[str] = []
    _answer(monkeypatch, False, prompts)
    assert cli.main(["migrate"]) == 0
    assert prompts == []
    assert f"development schema migrated v0 -> v{LATEST}" in capsys.readouterr().out
    assert cli.main(["migrate"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_production_migrate_new_database_asks_first(prod, monkeypatch, capsys):
    db, _ = prod
    prompts: list[str] = []
    _answer(monkeypatch, False, prompts)

    assert cli.main(["migrate", "--env", "production"]) == 1
    assert "migration cancelled" in capsys.readouterr().out
    assert prompts == [f"Apply migrations v1..v{LATEST} to PRODUCTION {db}?"]
    assert not db.exists()

    _answer(monkeypatch, True, prompts)
    assert cli.main(["migrate", "--env", "production"]) == 0
    assert f"production schema migrated v0 -> v{LATEST}" in capsys.readouterr().out


def test_production_migrate_backs_up_existing_database_first(prod, monkeypatch, capsys):
    db, backups = prod
    _make_v1_database(db, monkeypatch)  # production at v1

    assert cli.main(["migrate", "--env", "production", "--yes"]) == 0

    out = capsys.readouterr().out
    assert "pre-migration backup: created spend_manager_production_" in out
    assert f"production schema migrated v1 -> v{LATEST}" in out
    assert len(list_backups(backups, "production")) == 1


def test_production_migrate_aborts_when_backup_fails(prod, monkeypatch, capsys):
    from spend_manager.exceptions import BackupError

    db, _ = prod
    open_db(db).close()  # exists, v0

    def failing(*args, **kwargs):
        raise BackupError("Google Drive not writable")

    monkeypatch.setattr(cli, "create_backup", failing)
    assert cli.main(["migrate", "--env", "production", "--yes"]) == 1
    assert "pre-migration backup failed, migration aborted" in capsys.readouterr().err
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    conn.close()


# --- backup -----------------------------------------------------------------------


def test_backup_created_then_skipped_then_forced(dev, capsys):
    cli.main(["migrate"])
    capsys.readouterr()
    assert cli.main(["backup"]) == 0
    assert "backup created: spend_manager_development_" in capsys.readouterr().out
    assert cli.main(["backup"]) == 0
    assert "backup skipped: unchanged since" in capsys.readouterr().out
    assert cli.main(["backup", "--force"]) == 0
    assert "backup created" in capsys.readouterr().out


def test_backup_without_backup_dir(dev, monkeypatch, capsys):
    monkeypatch.delenv("SPEND_MANAGER_BACKUP_DIR")
    assert cli.main(["backup"]) == 1
    assert "no backup folder configured" in capsys.readouterr().err


def test_backup_of_missing_database(dev, capsys):
    assert cli.main(["backup"]) == 1
    assert "error: database file not found: sm.db" in capsys.readouterr().err


# --- restore ----------------------------------------------------------------------


def test_restore_by_file_name_from_backup_folder(dev, capsys):
    db, backups = dev
    cli.main(["migrate"])
    _add(db, "A")
    cli.main(["backup"])
    name = list_backups(backups, "development")[0].file
    _add(db, "B")
    capsys.readouterr()

    assert cli.main(["restore", name]) == 0

    out = capsys.readouterr().out
    assert f"restored sm.db from {name}" in out
    assert "safety backup: spend_manager_development_" in out
    assert _uids(db) == ["A"]


def test_production_restore_requires_confirmation(prod, monkeypatch, capsys):
    db, backups = prod
    cli.main(["migrate", "--env", "production", "--yes"])
    cli.main(["backup", "--env", "production"])
    backup_path = list_backups(backups, "production")[0].path
    prompts: list[str] = []
    _answer(monkeypatch, False, prompts)

    assert cli.main(["restore", str(backup_path), "--env", "production"]) == 1
    assert "restore cancelled" in capsys.readouterr().out
    assert prompts and prompts[0].startswith(f"Replace PRODUCTION database {db}")

    assert cli.main(["restore", str(backup_path), "--env", "production", "--yes"]) == 0


def test_restore_refused_while_service_running(dev, capsys):
    db, backups = dev
    cli.main(["migrate"])
    cli.main(["backup"])
    name = list_backups(backups, "development")[0].file
    service = RunLock(db).acquire_shared()
    try:
        assert cli.main(["restore", name]) == 1
    finally:
        service.release()
    assert "stop the Spend Manager service first" in capsys.readouterr().err


def test_restore_warns_when_backup_schema_is_older(dev, monkeypatch, capsys):
    db, backups = dev
    _make_v1_database(db, monkeypatch)
    cli.main(["backup"])
    name = list_backups(backups, "development")[0].file
    capsys.readouterr()

    assert cli.main(["restore", name]) == 0
    assert "restored schema is v1; run `spend-manager-db migrate --env development`" in (
        capsys.readouterr().out
    )


# --- errors -----------------------------------------------------------------------


def test_configuration_errors_exit_1(dev, monkeypatch, capsys):
    monkeypatch.setenv("SPEND_MANAGER_ENV", "production")
    monkeypatch.delenv("SPEND_MANAGER_BACKUP_DIR")
    assert cli.main(["status"]) == 1
    assert "error: production requires SPEND_MANAGER_BACKUP_DIR" in capsys.readouterr().err


def test_verbose_prints_traceback(dev, capsys):
    assert cli.main(["backup", "--verbose"]) == 1
    assert "Traceback" in capsys.readouterr().err


def test_confirm_reads_terminal(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert cli.confirm("Proceed?") is True
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert cli.confirm("Proceed?") is False

    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.confirm("Proceed?") is False


def test_usage_error_exits_2():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["frobnicate"])
    assert exc_info.value.code == 2

"""``spend-manager-db`` — status, migrations, backups and restore (FD v2 §1.9).

    spend-manager-db status  [--env E]
    spend-manager-db migrate [--env E] [--yes]
    spend-manager-db backup  [--env E] [--force]
    spend-manager-db restore FILE [--env E] [--yes]

``--env`` sets ``SPEND_MANAGER_ENV`` for this process before settings are
loaded (defaults to the current environment, i.e. development). Production
migrations take a backup first and, like production restores, ask for
confirmation unless ``--yes``. Exit status: 0 success, 1 failure / not up to
date, 2 usage error.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sqlite3
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

from spend_manager.config import ENV_VAR, Environment, Settings, get_settings
from spend_manager.db.backup import create_backup, latest_backup, restore_backup
from spend_manager.db.schema import ensure_schema, open_db, read_schema_version, required_version
from spend_manager.exceptions import (
    BackupError,
    DatabaseInUseError,
    DatabaseSetupError,
    RestoreError,
)

_HANDLED = (
    BackupError,
    DatabaseInUseError,
    DatabaseSetupError,
    RestoreError,
    sqlite3.Error,
    OSError,
)


def confirm(prompt: str) -> bool:
    """Ask a yes/no question on the terminal (module-level so tests can replace it)."""
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.env:
        os.environ[ENV_VAR] = args.env
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = get_settings()
        return _COMMANDS[args.command](settings, args)
    except _HANDLED as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        return 1


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--env",
        choices=[e.value for e in Environment],
        help=f"environment (default: ${ENV_VAR} or development)",
    )
    common.add_argument("--verbose", action="store_true", help="show tracebacks and info logs")

    parser = argparse.ArgumentParser(
        prog="spend-manager-db", description="Spend Manager database operations."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", parents=[common], help="show database and backup status")
    migrate = commands.add_parser("migrate", parents=[common], help="apply schema migrations")
    migrate.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    backup = commands.add_parser("backup", parents=[common], help="back up the database now")
    backup.add_argument("--force", action="store_true", help="write even if unchanged")
    restore = commands.add_parser("restore", parents=[common], help="restore a backup")
    restore.add_argument("file", help="backup file (path, or a file name in the backup folder)")
    restore.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    return parser


def _db_version(db_path: Path) -> int | None:
    """Schema version without changing anything; None if the file does not exist."""
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return read_schema_version(conn)
    finally:
        conn.close()


def _status(settings: Settings, args: argparse.Namespace) -> int:
    env = settings.environment.value
    version = _db_version(settings.db_path)
    required = required_version()
    if version is None:
        state, ok = "database file does not exist", False
    elif version < required:
        state, ok = "migration needed", False
    elif version > required:
        state, ok = "newer than this code", False
    else:
        state, ok = "up to date", True

    backup = latest_backup(settings.backup_dir, env)
    if backup is None:
        last = "none"
    else:
        age = datetime.datetime.now(datetime.UTC) - backup.created_at
        last = f"{backup.file} ({_human_age(age)} ago)"

    print(f"environment:  {env}")
    print(f"database:     {settings.db_path}")
    shown = f"v{version}" if version is not None else "-"
    print(f"schema:       {shown} (required v{required}) — {state}")
    print(f"backups:      {settings.backup_dir or 'disabled'}")
    print(f"last backup:  {last}")
    return 0 if ok else 1


def _migrate(settings: Settings, args: argparse.Namespace) -> int:
    env = settings.environment.value
    current = _db_version(settings.db_path) or 0
    latest = required_version()
    if current >= latest:
        print(f"{env} schema is v{current} — up to date")
        return 0

    if settings.is_production:
        if settings.db_path.is_file():
            # P-2: never migrate production without a backup of the current state.
            try:
                result = create_backup(
                    settings.db_path,
                    settings.backup_dir,
                    env,
                    keep=settings.spend_manager_backup_keep,
                )
            except BackupError as exc:
                raise BackupError(f"pre-migration backup failed, migration aborted: {exc}") from exc
            print(f"pre-migration backup: {result.status} {result.file}")
        if not args.yes and not confirm(
            f"Apply migrations v{current + 1}..v{latest} to PRODUCTION {settings.db_path}?"
        ):
            print("migration cancelled")
            return 1

    conn = open_db(settings.db_path)
    try:
        version = ensure_schema(conn)
    finally:
        conn.close()
    print(f"{env} schema migrated v{current} -> v{version}")
    return 0


def _backup(settings: Settings, args: argparse.Namespace) -> int:
    if settings.backup_dir is None:
        raise BackupError("no backup folder configured (SPEND_MANAGER_BACKUP_DIR)")
    result = create_backup(
        settings.db_path,
        settings.backup_dir,
        settings.environment.value,
        keep=settings.spend_manager_backup_keep,
        force=args.force,
    )
    if result.status == "created":
        print(f"backup created: {result.file} ({result.size_bytes} bytes)")
        for name in result.pruned:
            print(f"pruned: {name}")
    else:
        print(f"backup skipped: unchanged since {result.file}")
    return 0


def _restore(settings: Settings, args: argparse.Namespace) -> int:
    backup_file = Path(args.file).expanduser()
    if (
        not backup_file.exists()
        and settings.backup_dir is not None
        and backup_file.parent == Path(".")
    ):
        backup_file = settings.backup_dir / backup_file.name
    if (
        settings.is_production
        and not args.yes
        and not confirm(
            f"Replace PRODUCTION database {settings.db_path} with {backup_file.name}? "
            "A safety backup of the current database is taken first."
        )
    ):
        print("restore cancelled")
        return 1

    result = restore_backup(
        backup_file,
        settings.db_path,
        settings.backup_dir,
        settings.environment.value,
        keep=settings.spend_manager_backup_keep,
    )
    print(f"restored {settings.db_path.name} from {result.restored_from}")
    if result.safety_backup:
        print(f"safety backup: {result.safety_backup}")
    if result.schema_version < required_version():
        env = settings.environment.value
        print(
            f"note: restored schema is v{result.schema_version}; run "
            f"`spend-manager-db migrate --env {env}`"
        )
    return 0


def _human_age(age: datetime.timedelta) -> str:
    seconds = int(age.total_seconds())
    if seconds < 3600:
        return f"{max(seconds // 60, 0)} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


_COMMANDS: dict[str, Callable[[Settings, argparse.Namespace], int]] = {
    "status": _status,
    "migrate": _migrate,
    "backup": _backup,
    "restore": _restore,
}


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

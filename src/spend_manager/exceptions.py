"""Exceptions raised by the import pipelines.

Mapped to HTTP responses in ``spend_manager.main`` (see Functional Design →
Error Model):

- Text-export import: ``SourceFileNotFoundError`` -> 404, ``ParseError`` -> 400.
- MoneyWell store import: ``SourceNotFoundError`` -> 404,
  ``ImportRejectedError`` (and subclasses) -> 400, ``DatabaseSetupError``
  (``DatabasePathError``, ``SchemaVersionError``, ``SchemaOutdatedError``,
  ``ConfigurationError``) -> 500.
- Backups / restore (CLI and post-import): ``BackupError``, ``RestoreError``,
  ``DatabaseInUseError`` — never mapped to an HTTP error for a committed import.
"""

from __future__ import annotations


class SourceFileNotFoundError(Exception):
    """Raised when the derived MoneyWell source file does not exist."""

    def __init__(self, file_name: str) -> None:
        # Only the file name is included in the message (never the full
        # resolved path) so a 404 response cannot leak local filesystem
        # layout to a caller.
        self.file_name = file_name
        super().__init__(f"Source file not found: {file_name}")


class ParseError(Exception):
    """Raised when the source file does not conform to the expected format."""

    def __init__(self, line_no: int | None, message: str) -> None:
        self.line_no = line_no
        self.message = message
        prefix = f"Line {line_no}: " if line_no is not None else ""
        super().__init__(f"{prefix}{message}")


# --- MoneyWell persistentStore import -------------------------------------
#
# Messages only ever include the caller-supplied ``budget_folder`` value and
# MoneyWell record identifiers, never resolved filesystem paths (S-5).


class SourceNotFoundError(Exception):
    """The store root, budget folder/zip, or the persistentStore inside it is missing."""


class ImportRejectedError(Exception):
    """Base class for a MoneyWell store import rejected before anything is written (400)."""


class InvalidSourceError(ImportRejectedError):
    """``budget_folder`` is malformed, escapes the root, or is not a usable folder/zip/SQLite."""


class UnsupportedStoreError(ImportRejectedError):
    """The store lacks the Core Data entities, tables, or columns the importer relies on."""


class StoreValidationError(ImportRejectedError):
    """The store's data violates one or more validation rules (V-1..V-13)."""

    MAX_LISTED = 10

    def __init__(self, anomalies: list[str]) -> None:
        self.anomalies = anomalies
        listed = "; ".join(anomalies[: self.MAX_LISTED])
        more = len(anomalies) - self.MAX_LISTED
        suffix = f"; ... and {more} more" if more > 0 else ""
        super().__init__(f"{len(anomalies)} validation error(s): {listed}{suffix}")


class BudgetMismatchError(ImportRejectedError):
    """The store belongs to a different MoneyWell budget than the database is bound to."""

    def __init__(self, store_budget_id: str, db_budget_id: str) -> None:
        self.store_budget_id = store_budget_id
        self.db_budget_id = db_budget_id
        super().__init__(
            f"store belongs to MoneyWell budget {store_budget_id}; "
            f"this database is bound to budget {db_budget_id}"
        )


class SplitConflictError(ImportRejectedError):
    """New split children reference a transaction that was imported earlier (FR-10)."""

    MAX_LISTED = 10

    def __init__(self, conflicts: list[tuple[str, str]]) -> None:
        # (parent transaction uid, new split child uid)
        self.conflicts = conflicts
        listed = "; ".join(
            f"split {child} -> already-imported transaction {parent}"
            for parent, child in conflicts[: self.MAX_LISTED]
        )
        more = len(conflicts) - self.MAX_LISTED
        suffix = f"; ... and {more} more" if more > 0 else ""
        super().__init__(
            f"{len(conflicts)} new split(s) belong to transactions imported earlier, "
            f"which cannot be changed by a re-import: {listed}{suffix}"
        )


class DatabaseSetupError(Exception):
    """The Spend Manager database cannot be used as configured (server-side problem, 500)."""


class DatabasePathError(DatabaseSetupError):
    """``SPEND_MANAGER_DB_PATH`` does not name a usable database file."""


class SchemaVersionError(DatabaseSetupError):
    """The Spend Manager database schema is newer than this code understands."""


class ConfigurationError(DatabaseSetupError):
    """Invalid environment selection, settings file or setting values (C-1..C-7)."""


class SchemaOutdatedError(DatabaseSetupError):
    """The production schema is older than the code; run ``spend-manager-db migrate``."""


# --- Backups / restore -------------------------------------------------------


class BackupError(Exception):
    """A backup could not be created; committed data is never affected."""


class RestoreError(Exception):
    """A backup could not be restored; the live database is left untouched."""


class DatabaseInUseError(Exception):
    """The database is in use (service running or restore in progress)."""

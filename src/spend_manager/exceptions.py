"""Exceptions raised by the import pipeline.

Mapped to HTTP responses in ``spend_manager.main`` (see Functional Design →
Error Model): ``SourceFileNotFoundError`` -> 404, ``ParseError`` -> 400.
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

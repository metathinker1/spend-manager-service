"""Locate a MoneyWell store under the configured root and stage a private copy.

See Functional Design §1.2 and security rules S-1..S-3, S-5. The original
files are only ever *read as bytes* (``shutil.copy2`` / ``zipfile``); SQLite
only ever opens the temporary copy, so the source — including its WAL — is
never modified (NFR-1).
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from spend_manager.exceptions import InvalidSourceError, SourceNotFoundError

STORE_FILE = "persistentStore"
STORE_FILES = (STORE_FILE, f"{STORE_FILE}-wal", f"{STORE_FILE}-shm")
MAX_ZIP_UNCOMPRESSED_BYTES = 1 << 30  # 1 GiB
_COPY_CHUNK = 1 << 20


@contextmanager
def resolve_store(root: Path, budget_folder: str) -> Iterator[Path]:
    """Yield the path of a temporary copy of the requested ``persistentStore``.

    ``budget_folder`` names a folder or a ``.zip`` directly under ``root``.
    The temporary directory is removed when the context exits, whether or
    not an error occurred.

    Raises:
        SourceNotFoundError: root, budget folder/zip, or store not found.
        InvalidSourceError: ``budget_folder`` is malformed or escapes the
            root, or the zip is unusable.
    """
    root_resolved, candidate = _resolve_candidate(root, budget_folder)
    with tempfile.TemporaryDirectory(prefix="moneywell_store_") as tmp:
        tmp_dir = Path(tmp)
        if candidate.is_dir():
            _copy_from_folder(candidate, root_resolved, tmp_dir, budget_folder)
        elif candidate.is_file() and candidate.suffix.lower() == ".zip":
            _extract_from_zip(candidate, tmp_dir, budget_folder)
        else:
            raise InvalidSourceError(f"not a budget folder or .zip file: {budget_folder}")
        yield tmp_dir / STORE_FILE


def _resolve_candidate(root: Path, budget_folder: str) -> tuple[Path, Path]:
    root_resolved = root.expanduser().resolve()
    if not root_resolved.is_dir():
        raise SourceNotFoundError("MoneyWell store root directory not found")

    if (
        budget_folder in ("", ".", "..")
        or "/" in budget_folder
        or "\\" in budget_folder
        or "\x00" in budget_folder
    ):
        raise InvalidSourceError(
            f"budget_folder must be a single folder or file name: {budget_folder!r}"
        )

    candidate = root_resolved / budget_folder
    if not candidate.exists():
        raise SourceNotFoundError(f"budget folder not found: {budget_folder}")
    _require_inside(candidate, root_resolved, budget_folder)
    return root_resolved, candidate


def _require_inside(path: Path, root_resolved: Path, budget_folder: str) -> None:
    # Defeats symlinks that point outside the store root (S-1).
    resolved = path.resolve()
    if resolved == root_resolved or not resolved.is_relative_to(root_resolved):
        raise InvalidSourceError(f"budget_folder resolves outside the store root: {budget_folder}")


def _copy_from_folder(folder: Path, root_resolved: Path, tmp_dir: Path, budget_folder: str) -> None:
    for store_dir in (folder / "data", folder):
        store = store_dir / STORE_FILE
        if store.is_file():
            break
    else:
        raise SourceNotFoundError(f"no {STORE_FILE} found in budget folder: {budget_folder}")

    for name in STORE_FILES:
        path = store_dir / name
        if path.is_file():
            _require_inside(path, root_resolved, budget_folder)
            shutil.copy2(path, tmp_dir / name)


def _extract_from_zip(archive: Path, tmp_dir: Path, budget_folder: str) -> None:
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise InvalidSourceError(f"not a valid zip file: {budget_folder}") from exc

    with zf:
        members = [info for info in zf.infolist() if not info.is_dir()]
        stores = [info for info in members if PurePosixPath(info.filename).name == STORE_FILE]
        if not stores:
            raise SourceNotFoundError(f"no {STORE_FILE} found in zip: {budget_folder}")
        if len(stores) > 1:
            raise InvalidSourceError(f"zip contains more than one {STORE_FILE}: {budget_folder}")

        store_dir = PurePosixPath(stores[0].filename).parent
        selected = [
            info
            for info in members
            if PurePosixPath(info.filename).parent == store_dir
            and PurePosixPath(info.filename).name in STORE_FILES
        ]
        for info in selected:
            _check_member_name(info.filename, budget_folder)
        if sum(info.file_size for info in selected) > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise InvalidSourceError(f"zip store exceeds the size limit: {budget_folder}")

        budget = MAX_ZIP_UNCOMPRESSED_BYTES
        for info in selected:
            # Flattened: the member's directory part is never used on disk.
            target = tmp_dir / PurePosixPath(info.filename).name
            with zf.open(info) as src, target.open("wb") as dst:
                budget -= _copy_limited(src, dst, budget, budget_folder)


def _check_member_name(filename: str, budget_folder: str) -> None:
    path = PurePosixPath(filename.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise InvalidSourceError(f"zip contains an unsafe path: {budget_folder}")


def _copy_limited(src, dst, limit: int, budget_folder: str) -> int:
    # Enforces the size cap on actual decompressed bytes, not just the
    # (attacker-controllable) sizes recorded in the zip directory.
    copied = 0
    while chunk := src.read(_COPY_CHUNK):
        copied += len(chunk)
        if copied > limit:
            raise InvalidSourceError(f"zip store exceeds the size limit: {budget_folder}")
        dst.write(chunk)
    return copied

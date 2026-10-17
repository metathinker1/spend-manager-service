"""Tests for spend_manager.moneywell_store.source (Functional Design §1.2, S-1..S-3)."""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest
from moneywell_fixture import default_store, write_store

from spend_manager.exceptions import InvalidSourceError, SourceNotFoundError
from spend_manager.moneywell_store import source
from spend_manager.moneywell_store.source import resolve_store


@pytest.fixture
def root(tmp_path) -> Path:
    r = tmp_path / "MoneyWell_Data"
    r.mkdir()
    return r


def _digest(folder: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.iterdir())
        if p.is_file()
    }


def test_folder_with_data_subdir_layout(root):
    write_store(default_store(), root / "Budget@_@X" / "data", wal=True)

    with resolve_store(root, "Budget@_@X") as store:
        copied = {p.name for p in store.parent.iterdir()}
        assert store.name == "persistentStore"
        assert copied == {"persistentStore", "persistentStore-wal", "persistentStore-shm"}
        temp_dir = store.parent

    assert not temp_dir.exists()


def test_flat_folder_layout(root):
    write_store(default_store(), root / "SampleData")

    with resolve_store(root, "SampleData") as store:
        assert store.is_file()


def test_source_files_are_not_modified(root):
    folder = root / "SampleData"
    write_store(default_store(), folder, wal=True)
    before = _digest(folder)

    with resolve_store(root, "SampleData") as store:
        store.write_bytes(b"clobbered copy")

    assert _digest(folder) == before


def test_temp_dir_removed_when_body_raises(root):
    write_store(default_store(), root / "SampleData")

    with pytest.raises(RuntimeError), resolve_store(root, "SampleData") as store:
        temp_dir = store.parent
        raise RuntimeError("boom")

    assert not temp_dir.exists()


def _zip_store(root: Path, name: str, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(root / name, "w") as zf:
        for member, data in members.items():
            zf.writestr(member, data)


def test_zip_backup(root, tmp_path):
    staged = tmp_path / "staged"
    write_store(default_store(), staged, wal=True)
    _zip_store(
        root,
        "Budget.zip",
        {
            "budget.json": b"{}",
            "data/persistentStore": (staged / "persistentStore").read_bytes(),
            "data/persistentStore-wal": (staged / "persistentStore-wal").read_bytes(),
            "__MACOSX/data/._persistentStore": b"resource fork",
        },
    )

    with resolve_store(root, "Budget.zip") as store:
        names = {p.name for p in store.parent.iterdir()}
        assert names == {"persistentStore", "persistentStore-wal"}
        assert store.read_bytes() == (staged / "persistentStore").read_bytes()


def test_zip_without_store_is_not_found(root):
    _zip_store(root, "Empty.zip", {"budget.json": b"{}"})
    with pytest.raises(SourceNotFoundError), resolve_store(root, "Empty.zip"):
        pass


def test_zip_with_two_stores_is_rejected(root):
    _zip_store(root, "Two.zip", {"a/persistentStore": b"x", "b/persistentStore": b"y"})
    with pytest.raises(InvalidSourceError, match="more than one"), resolve_store(root, "Two.zip"):
        pass


def test_zip_slip_member_is_rejected(root):
    _zip_store(root, "Evil.zip", {"../../persistentStore": b"x"})
    with pytest.raises(InvalidSourceError, match="unsafe path"), resolve_store(root, "Evil.zip"):
        pass


def test_zip_size_cap_is_enforced_on_actual_bytes(root, monkeypatch):
    _zip_store(root, "Big.zip", {"persistentStore": b"x" * 100})
    monkeypatch.setattr(source, "MAX_ZIP_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(InvalidSourceError, match="size limit"), resolve_store(root, "Big.zip"):
        pass


def test_zip_actual_bytes_exceeding_cap_are_rejected(root, monkeypatch):
    # Declared sizes within the cap, but the running byte budget is smaller.
    _zip_store(root, "Big.zip", {"persistentStore": b"x" * 100})
    monkeypatch.setattr(source, "MAX_ZIP_UNCOMPRESSED_BYTES", 100)
    monkeypatch.setattr(source, "_COPY_CHUNK", 7)
    original = source._copy_limited
    monkeypatch.setattr(
        source, "_copy_limited", lambda src, dst, limit, bf: original(src, dst, 50, bf)
    )
    with pytest.raises(InvalidSourceError, match="size limit"), resolve_store(root, "Big.zip"):
        pass


def test_corrupt_zip_is_rejected(root):
    (root / "Bad.zip").write_bytes(b"not a zip")
    with pytest.raises(InvalidSourceError, match="not a valid zip"), resolve_store(root, "Bad.zip"):
        pass


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "../x", "/etc", "a\\b", "x\x00y"])
def test_malformed_budget_folder_is_rejected(root, bad):
    with pytest.raises(InvalidSourceError), resolve_store(root, bad):
        pass


def test_symlink_escaping_root_is_rejected(root, tmp_path):
    outside = tmp_path / "outside"
    write_store(default_store(), outside)
    os.symlink(outside, root / "Sneaky")

    with pytest.raises(InvalidSourceError, match="outside"), resolve_store(root, "Sneaky"):
        pass


def test_store_symlink_escaping_root_is_rejected(root, tmp_path):
    outside = tmp_path / "outside"
    write_store(default_store(), outside)
    folder = root / "Budget"
    folder.mkdir()
    os.symlink(outside / "persistentStore", folder / "persistentStore")

    with pytest.raises(InvalidSourceError, match="outside"), resolve_store(root, "Budget"):
        pass


def test_missing_root(tmp_path):
    with (
        pytest.raises(SourceNotFoundError, match="root"),
        resolve_store(tmp_path / "nope", "SampleData"),
    ):
        pass


def test_missing_budget_folder(root):
    with pytest.raises(SourceNotFoundError, match="Nope"), resolve_store(root, "Nope"):
        pass


def test_folder_without_store(root):
    (root / "Empty").mkdir()
    with (
        pytest.raises(SourceNotFoundError, match="no persistentStore"),
        resolve_store(root, "Empty"),
    ):
        pass


def test_plain_file_is_rejected(root):
    (root / "notes.txt").write_text("hi")
    with (
        pytest.raises(InvalidSourceError, match="not a budget folder"),
        resolve_store(root, "notes.txt"),
    ):
        pass


def test_error_messages_do_not_leak_absolute_paths(root):
    with pytest.raises(SourceNotFoundError) as exc_info, resolve_store(root, "Nope"):
        pass
    assert str(root) not in str(exc_info.value)

"""Tests for LocalFilesystemStorage."""

from __future__ import annotations

import shutil

import pytest

from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage


def test_list_files_ignores_system_files_and_recurses(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "clip.mp4").write_bytes(b"a")
    (tmp_path / "sub" / "nested.mp4").write_bytes(b"b")
    (tmp_path / ".DS_Store").write_bytes(b"junk")

    storage = LocalFilesystemStorage()
    files = sorted(storage.list_files(tmp_path))

    assert [f.name for f in files] == ["clip.mp4", "nested.mp4"]


def test_list_files_raises_on_missing_directory(tmp_path):
    storage = LocalFilesystemStorage()
    with pytest.raises(NotADirectoryError):
        list(storage.list_files(tmp_path / "does_not_exist"))


def test_checksum_is_stable_and_content_sensitive(tmp_path):
    file_a = tmp_path / "a.bin"
    file_b = tmp_path / "b.bin"
    file_a.write_bytes(b"hello world")
    file_b.write_bytes(b"different content")

    storage = LocalFilesystemStorage()
    assert storage.checksum(file_a) == storage.checksum(file_a)
    assert storage.checksum(file_a) != storage.checksum(file_b)


def test_copy_creates_destination_dirs_and_preserves_content_and_source(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    destination = tmp_path / "nested" / "deeper" / "destination.bin"

    storage = LocalFilesystemStorage()
    storage.copy(source, destination)

    assert destination.read_bytes() == b"payload"
    assert source.exists()  # copy-only in v0.1 — bron blijft altijd onaangeroerd


def test_copy_returns_none_when_metadata_replication_succeeds(tmp_path):
    """Normal case — content and metadata both replicate fine, no warning."""
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    destination = tmp_path / "destination.bin"

    storage = LocalFilesystemStorage()
    warning = storage.copy(source, destination)

    assert warning is None
    assert destination.read_bytes() == b"payload"


def test_copy_raises_when_the_content_copy_itself_fails(tmp_path, monkeypatch):
    """A genuine content-copy failure (shutil.copyfile) must still propagate
    as OSError — it is never downgraded to a warning, only a metadata-only
    failure (shutil.copystat, see below) is."""
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    destination = tmp_path / "destination.bin"

    def _broken_copyfile(src, dst):
        raise OSError("disk vol (gesimuleerd)")

    monkeypatch.setattr(shutil, "copyfile", _broken_copyfile)
    storage = LocalFilesystemStorage()

    with pytest.raises(OSError, match="disk vol"):
        storage.copy(source, destination)
    assert not destination.exists()


def test_copy_returns_a_warning_and_keeps_the_content_when_metadata_replication_fails(
    tmp_path, monkeypatch
):
    """Regression test for the false-negative found on a real Sony camera
    card (SONYCARD.IND — exFAT `uchg`/immutable flag): shutil.copystat()'s
    final os.chflags() step can raise EPERM even though the byte content was
    already copied successfully just before it. Reproduced here via a
    monkeypatched shutil.copystat instead of depending on real exFAT
    hardware, so this is deterministic and portable."""
    source = tmp_path / "input" / "SONYCARD.IND"
    source.parent.mkdir()
    source.write_bytes(b"")
    destination = tmp_path / "output" / "SONYCARD.IND"

    def _broken_copystat(src, dst, *, follow_symlinks=True):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(shutil, "copystat", _broken_copystat)
    storage = LocalFilesystemStorage()

    warning = storage.copy(source, destination)

    assert warning is not None
    assert "metadata" in warning.lower()
    assert destination.exists()
    assert destination.read_bytes() == b""  # content gekopieerd ondanks de metadata-fout


def test_exists_reflects_real_filesystem_state(tmp_path):
    present = tmp_path / "present.bin"
    present.write_bytes(b"x")
    absent = tmp_path / "absent.bin"

    storage = LocalFilesystemStorage()
    assert storage.exists(present) is True
    assert storage.exists(absent) is False


def test_free_bytes_matches_shutil_disk_usage(tmp_path):
    storage = LocalFilesystemStorage()
    assert storage.free_bytes(tmp_path) == shutil.disk_usage(tmp_path).free


def test_remove_deletes_an_existing_file(tmp_path):
    target = tmp_path / "partial.bin"
    target.write_bytes(b"partial content")

    storage = LocalFilesystemStorage()
    storage.remove(target)

    assert not target.exists()


def test_remove_is_a_safe_no_op_when_the_file_does_not_exist(tmp_path):
    """Never raises — `_process_asset` calls this unconditionally after any
    copy() failure, including when copy() never got far enough to create
    anything at all (see core/ingest_service.py)."""
    storage = LocalFilesystemStorage()
    storage.remove(tmp_path / "never_existed.bin")  # mag nooit raisen

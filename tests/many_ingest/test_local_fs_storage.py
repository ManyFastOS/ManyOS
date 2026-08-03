"""Tests for LocalFilesystemStorage."""

from __future__ import annotations

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


def test_exists_reflects_real_filesystem_state(tmp_path):
    present = tmp_path / "present.bin"
    present.write_bytes(b"x")
    absent = tmp_path / "absent.bin"

    storage = LocalFilesystemStorage()
    assert storage.exists(present) is True
    assert storage.exists(absent) is False

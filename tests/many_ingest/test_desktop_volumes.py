"""Tests for the volume-detection OS-integration layer (Fase 1).

Two kinds of coverage, deliberately kept apart:
- Deterministic tests against fake directory trees under tmp_path (empty
  check, media counting, name-pattern exclusion, full list_candidate_volumes
  composition with injected identity checks) — these hold on any machine.
- A couple of narrow, honest checks of the *real* device-identity functions
  (default_is_boot_volume/default_is_destination_volume). A single-disk dev
  machine or CI sandbox cannot fabricate a genuinely different physical
  volume, so the "this is NOT the boot/destination disk" branch can only be
  fully verified by hand, on a real Mac with a real external drive attached
  (see the Fase 1 handoff notes for the exact manual scenarios).
"""

from __future__ import annotations

from pathlib import Path

from many_ingest.desktop.volumes import (
    default_is_boot_volume,
    default_is_destination_volume,
    describe_volume,
    format_size,
    list_candidate_volumes,
    scan_media_summary,
)


def _make_volume(root: Path, name: str, files: dict[str, bytes]) -> Path:
    volume = root / name
    volume.mkdir()
    for relative_path, content in files.items():
        target = volume / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return volume


# -- scan_media_summary / describe_volume ---------------------------------------


def test_scan_media_summary_counts_only_relevant_extensions(tmp_path):
    volume = _make_volume(
        tmp_path,
        "SD_CARD_1",
        {
            "C0001.MP4": b"x" * 1000,
            "C0002.MXF": b"x" * 2000,
            "interview.wav": b"x" * 500,
            "notes.txt": b"irrelevant",
            ".DS_Store": b"irrelevant",
        },
    )

    count, total_bytes = scan_media_summary(volume)

    assert count == 3
    assert total_bytes == 3500


def test_scan_media_summary_skips_hidden_directories_entirely(tmp_path):
    volume = _make_volume(tmp_path, "SD_CARD_1", {".Trashes/deleted.mp4": b"x" * 1000})

    count, total_bytes = scan_media_summary(volume)

    assert (count, total_bytes) == (0, 0)


def test_describe_volume_returns_none_for_empty_volume(tmp_path):
    volume = _make_volume(tmp_path, "EMPTY_CARD", {})

    assert describe_volume(volume) is None


def test_describe_volume_ignores_only_system_files_when_checking_emptiness(tmp_path):
    volume = _make_volume(tmp_path, "EMPTY_CARD", {".DS_Store": b"x"})

    assert describe_volume(volume) is None


def test_describe_volume_returns_none_for_unreadable_path(tmp_path):
    missing = tmp_path / "does_not_exist"

    assert describe_volume(missing) is None


def test_describe_volume_reports_name_capacity_and_media_summary(tmp_path):
    volume = _make_volume(tmp_path, "SD_CARD_1", {"C0001.MP4": b"x" * 1000})

    info = describe_volume(volume)

    assert info is not None
    assert info.name == "SD_CARD_1"
    assert info.path == volume
    assert info.media_file_count == 1
    assert info.media_total_bytes == 1000
    assert info.capacity_bytes > 0


# -- format_size -----------------------------------------------------------------


def test_format_size_matches_report_py_formatting():
    assert format_size(500) == "500 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(214_000_000_000) == "199.3 GB"


# -- list_candidate_volumes composition (identity checks injected) --------------


def test_list_candidate_volumes_excludes_boot_and_destination_and_named_system_volumes(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    _make_volume(volumes_root, "SD_CARD_1", {"C0001.MP4": b"x" * 1000})
    _make_volume(volumes_root, "Macintosh HD", {"System": b"x"})
    boot = _make_volume(volumes_root, "SomeBootLookalike", {"x": b"x"})
    destination = _make_volume(volumes_root, "ManyFastNAS", {"x": b"x"})
    _make_volume(volumes_root, "EMPTY_CARD", {})

    candidates = list_candidate_volumes(
        storage_root=Path("/irrelevant/because/injected"),
        volumes_root=volumes_root,
        is_boot_volume=lambda path: path == boot,
        is_destination_volume=lambda path, storage_root: path == destination,
    )

    assert [c.name for c in candidates] == ["SD_CARD_1"]


def test_list_candidate_volumes_returns_empty_list_when_volumes_root_missing(tmp_path):
    assert list_candidate_volumes(storage_root=None, volumes_root=tmp_path / "no_such_dir") == []


def test_list_candidate_volumes_is_sorted_by_name(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    _make_volume(volumes_root, "ZZZ_LAST", {"a.mp4": b"x"})
    _make_volume(volumes_root, "AAA_FIRST", {"a.mp4": b"x"})

    candidates = list_candidate_volumes(
        storage_root=None,
        volumes_root=volumes_root,
        is_boot_volume=lambda path: False,
        is_destination_volume=lambda path, storage_root: False,
    )

    assert [c.name for c in candidates] == ["AAA_FIRST", "ZZZ_LAST"]


# -- default identity checks: honest, narrow real-filesystem coverage -----------


def test_default_is_boot_volume_is_true_for_root_itself():
    assert default_is_boot_volume(Path("/")) is True


def test_default_is_destination_volume_is_false_without_a_configured_storage_root(tmp_path):
    assert default_is_destination_volume(tmp_path, storage_root=None) is False


def test_default_is_destination_volume_is_true_when_on_the_same_device(tmp_path):
    storage_root = tmp_path / "does" / "not" / "exist" / "yet"

    # tmp_path itself always exists and is necessarily on the same device as
    # any not-yet-created descendant of it — this exercises the "existing
    # ancestor" walk-up and the positive match, without needing a real,
    # separate physical volume (see module docstring).
    assert default_is_destination_volume(tmp_path, storage_root=storage_root) is True

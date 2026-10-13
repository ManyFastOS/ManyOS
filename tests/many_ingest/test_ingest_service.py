"""Tests for IngestService — the core orchestration pipeline.

Uses the real LocalFilesystemStorage/JSONManifest adapters against tmp_path for most
cases (an integration-style test of the whole ports & adapters wiring), plus one
fake Storage to force a checksum mismatch and exercise the verification-failure path.
"""

from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage
from many_ingest.classification.camera_profiles import ClassificationSource
from many_ingest.classification.file_types import MediaType
from many_ingest.config import IngestConfig
from many_ingest.core import ingest_service as ingest_service_module
from many_ingest.core.ingest_service import (
    AssetOutcome,
    DestinationFullError,
    DestinationUnavailableError,
    IngestService,
    ProgressUpdate,
    _normalize_frame_rate,
    _resolve_sidecar_relationships,
)
from many_ingest.core.report import summarize
from many_ingest.logger import ActionLogger
from many_ingest.metadata_extractor import FfprobeNotFoundError
from many_ingest.ports.storage import Storage
from many_ingest.sidecar_relationship import RelationshipEvidence

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe niet geïnstalleerd",
)


def _make_config(tmp_path: Path) -> IngestConfig:
    return IngestConfig(
        storage_root=tmp_path / "storage",
        manifest_path=tmp_path / "asset_schema.json",
        log_dir=tmp_path / "logs",
    )


def _workspace_dir(
    config: IngestConfig,
    client: str,
    project: str,
    category: str,
    camera_profile: str | None = None,
) -> Path:
    """Mirrors _build_workspace_path's layout for tests that need to pre-place a
    colliding file at the exact path the pipeline will compute today. Fase 5.3:
    pass `camera_profile` for Camera/Drone categories to mirror the new
    camera-aware subfolder; omit it for Audio/Onbekend, which stay flat."""
    today = dt.date.today().isoformat()
    folder = config.storage_root / "Klanten" / client / project / f"{today}_Raw" / category
    if camera_profile is not None:
        folder = folder / camera_profile
    return folder


def _make_service(config: IngestConfig, camera_profiles, storage: Storage | None = None) -> IngestService:
    return IngestService(
        storage=storage or LocalFilesystemStorage(),
        manifest=JSONManifest(config.manifest_path),
        config=config,
        camera_profiles=camera_profiles,
    )


def test_dry_run_does_not_touch_disk(tmp_path, camera_profiles):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)
    report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

    assert report.dry_run is True
    assert len(report.assets) == 1
    asset = report.assets[0]
    assert asset.outcome == AssetOutcome.PREVIEW
    assert asset.category == "Drone"
    assert not config.storage_root.exists()  # niets gekopieerd
    assert not config.manifest_path.exists()  # schema niet bijgewerkt
    assert not report.log_path.exists()  # een dry-run schrijft ook het actielog niet


def test_dry_run_succeeds_even_when_log_dir_is_unreachable(tmp_path, camera_profiles):
    """Regression test for a real bug found in manual testing: a preview
    must not fail just because the destination's log_dir happens to be
    unreachable at that moment (e.g. an external destination SSD that isn't
    currently mounted) — a dry-run only reads, it should never need to write
    anywhere, including the action log. Before the fix, this raised a
    PermissionError from ActionLogger's directory creation, which a broad
    `except OSError` in ingest_worker.py mislabeled as "source unreadable"."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    unreachable_parent = tmp_path / "unreachable"
    unreachable_parent.mkdir()
    unreachable_parent.chmod(0o555)  # simuleert een niet-aangekoppelde bestemmingsschijf
    config = IngestConfig(
        storage_root=tmp_path / "storage",
        manifest_path=tmp_path / "asset_schema.json",
        log_dir=unreachable_parent / "logs",
    )
    try:
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)
    finally:
        unreachable_parent.chmod(0o755)  # opruimen, anders kan tmp_path niet weggegooid worden

    assert report.assets[0].outcome == AssetOutcome.PREVIEW
    assert not config.log_dir.exists()  # geen write geprobeerd, dus ook geen map aangemaakt


def test_real_run_still_requires_a_reachable_log_dir(tmp_path, camera_profiles):
    """A real ingest genuinely writes to log_dir, so it must still fail
    (cleanly, as an OSError — not silently, not a crash) when the
    destination isn't reachable. Only the dry-run path changed."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    unreachable_parent = tmp_path / "unreachable"
    unreachable_parent.mkdir()
    unreachable_parent.chmod(0o555)
    config = IngestConfig(
        storage_root=tmp_path / "storage",
        manifest_path=tmp_path / "asset_schema.json",
        log_dir=unreachable_parent / "logs",
    )
    try:
        service = _make_service(config, camera_profiles)
        with pytest.raises(OSError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)
    finally:
        unreachable_parent.chmod(0o755)


def test_real_run_copies_verifies_and_registers(tmp_path, camera_profiles):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)
    report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    asset = report.assets[0]
    assert asset.outcome == AssetOutcome.COPIED
    assert asset.destination_path.exists()
    assert asset.destination_path.read_bytes() == b"fake video bytes"
    assert report.total_bytes == len(b"fake video bytes")
    assert report.duration_seconds >= 0

    schema = json.loads(config.manifest_path.read_text())
    assert len(schema["assets"]) == 1
    assert schema["assets"][0]["asset_id"] == asset.checksum
    assert schema["assets"][0]["client_id"] == "Nike"


def test_second_run_skips_duplicates_and_leaves_source_untouched(tmp_path, camera_profiles):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    clip = input_dir / "DJI_0001.MP4"
    clip.write_bytes(b"fake video bytes")

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)
    service.run(input_dir, client="Nike", project="Zomer", dry_run=False)
    second_report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    asset = second_report.assets[0]
    assert asset.outcome == AssetOutcome.DUPLICATE_SKIPPED
    assert clip.exists()  # copy-only, v0.1 — bron blijft intact


def test_failed_verification_is_not_registered_and_does_not_crash_the_run(tmp_path, camera_profiles):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    config = _make_config(tmp_path)
    real_storage = LocalFilesystemStorage()

    class _MismatchingStorage(Storage):
        """Copies for real, but reports a different checksum for the destination —
        forces the verification-failure branch without contriving real disk
        corruption."""

        def list_files(self, root):
            return real_storage.list_files(root)

        def exists(self, path):
            return real_storage.exists(path)

        def copy(self, source, destination):
            real_storage.copy(source, destination)

        def checksum(self, path):
            if config.storage_root in Path(path).parents:
                return "destination-checksum"
            return "source-checksum"

        def free_bytes(self, path):
            return real_storage.free_bytes(path)

        def remove(self, path):
            real_storage.remove(path)

    service = _make_service(config, camera_profiles, storage=_MismatchingStorage())
    report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    asset = report.assets[0]
    assert asset.outcome == AssetOutcome.FAILED_VERIFICATION
    assert asset.error is not None
    assert asset.metadata_warning is None
    assert not config.manifest_path.exists()


def test_metadata_only_copy_failure_still_counts_as_copied_with_a_warning(
    tmp_path, camera_profiles, monkeypatch
):
    """Regression test for a real false-negative found on a real Sony camera
    card: SONYCARD.IND has exFAT flags (`uchg`/immutable) that
    shutil.copystat()'s final os.chflags() step cannot replicate onto an
    exFAT destination, raising EPERM — after the file's (0-byte) content had
    already been copied correctly. That must produce a COPIED asset with a
    metadata_warning, never failed_verification. Reproduced here via a
    monkeypatched shutil.copystat so this doesn't depend on real exFAT
    hardware (see test_local_fs_storage.py for the adapter-level version of
    this same test)."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "SONYCARD.IND").write_bytes(b"")

    def _broken_copystat(src, dst, *, follow_symlinks=True):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(
        "many_ingest.adapters.local_fs_storage.shutil.copystat", _broken_copystat
    )

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)
    report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    asset = report.assets[0]
    assert asset.outcome == AssetOutcome.COPIED
    assert asset.error is None
    assert asset.metadata_warning is not None
    assert "metadata" in asset.metadata_warning.lower()
    assert asset.destination_path.read_bytes() == b""

    schema = json.loads(config.manifest_path.read_text())
    assert len(schema["assets"]) == 1  # content was fine — nog steeds geregistreerd

    log_lines = report.log_path.read_text().strip().splitlines()
    asset_events = [json.loads(line) for line in log_lines if '"asset_processed"' in line]
    assert asset_events[0]["metadata_warning"] is not None
    assert asset_events[0]["outcome"] == "copied"

    summary = summarize(report)
    assert summary.metadata_warnings == 1
    assert summary.errors == 0
    assert summary.safe_to_delete_source is True  # geen contentrisico — alleen metadata


def test_genuine_content_copy_failure_is_still_a_real_failure(
    tmp_path, camera_profiles, monkeypatch
):
    """The other half of the false-negative fix: a failure in the actual
    byte-copy step (not metadata) must still fail loudly, exactly as before —
    this distinguishes the two failure classes rather than silencing both."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    def _broken_copyfile(src, dst):
        raise OSError("disk vol (gesimuleerd)")

    monkeypatch.setattr(
        "many_ingest.adapters.local_fs_storage.shutil.copyfile", _broken_copyfile
    )

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)
    report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    asset = report.assets[0]
    assert asset.outcome == AssetOutcome.FAILED_VERIFICATION
    assert asset.error is not None
    assert asset.metadata_warning is None
    assert not config.manifest_path.exists()

    summary = summarize(report)
    assert summary.errors == 1
    assert summary.metadata_warnings == 0
    assert summary.safe_to_delete_source is False


def test_ffprobe_missing_aborts_before_touching_disk(tmp_path, camera_profiles, monkeypatch):
    monkeypatch.setattr("many_ingest.core.ingest_service.is_ffprobe_available", lambda: False)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)

    with pytest.raises(FfprobeNotFoundError, match="brew install ffmpeg"):
        service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

    assert not config.log_dir.exists()  # gestopt vóór er iets werd geschreven


def test_dry_run_succeeds_even_when_the_destination_is_unreachable(tmp_path, camera_profiles):
    """Fase 3.5's destination pre-flight check must never block a preview —
    same principle as the earlier log_dir-specific fix, now stated as the
    general rule for the whole destination."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    unreachable_parent = tmp_path / "unreachable"
    unreachable_parent.mkdir()
    unreachable_parent.chmod(0o555)
    config = IngestConfig(
        storage_root=unreachable_parent / "disk" / "Footage",
        manifest_path=unreachable_parent / "disk" / "Schema" / "asset_schema.json",
        log_dir=unreachable_parent / "disk" / "Logs",
    )
    try:
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)
    finally:
        unreachable_parent.chmod(0o755)

    assert report.assets[0].outcome == AssetOutcome.PREVIEW


def test_real_run_raises_destination_unavailable_error_when_unreachable(tmp_path, camera_profiles):
    """A real run genuinely needs to write to the destination, so it must
    still fail — cleanly, as a distinct, nameable error, before touching
    anything (not a bare OSError misattributed to the source by a caller's
    broad except-clause — see ingest_worker.py/cli.py, which catch this
    exception type specifically)."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    unreachable_parent = tmp_path / "unreachable"
    unreachable_parent.mkdir()
    unreachable_parent.chmod(0o555)
    config = IngestConfig(
        storage_root=unreachable_parent / "disk" / "Footage",
        manifest_path=unreachable_parent / "disk" / "Schema" / "asset_schema.json",
        log_dir=unreachable_parent / "disk" / "Logs",
    )
    try:
        service = _make_service(config, camera_profiles)
        with pytest.raises(DestinationUnavailableError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)
    finally:
        unreachable_parent.chmod(0o755)


def test_progress_callback_reports_every_file_with_running_byte_total(tmp_path, camera_profiles):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"12345")
    (input_dir / "DJI_0002.MP4").write_bytes(b"1234567890")

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)

    updates: list[ProgressUpdate] = []
    service.run(
        input_dir, client="Nike", project="Zomer", dry_run=True, progress_callback=updates.append
    )

    assert [u.processed for u in updates] == [1, 2]
    assert all(u.total == 2 for u in updates)
    assert updates[0].current_file == "DJI_0001.MP4"
    assert updates[1].current_file == "DJI_0002.MP4"
    assert updates[0].bytes_processed == 5
    assert updates[1].bytes_processed == 15  # cumulatief: 5 + 10


def test_asset_callback_reports_every_file_with_its_outcome(tmp_path, camera_profiles):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"12345")
    (input_dir / "DJI_0002.MP4").write_bytes(b"1234567890")

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)

    assets_seen: list = []
    report = service.run(
        input_dir,
        client="Nike",
        project="Zomer",
        dry_run=False,
        asset_callback=assets_seen.append,
    )

    assert [a.source_path for a in assets_seen] == [a.source_path for a in report.assets]
    assert [a.outcome for a in assets_seen] == [AssetOutcome.COPIED, AssetOutcome.COPIED]


def test_asset_callback_fires_as_each_file_completes_not_after_the_whole_run(tmp_path, camera_profiles):
    """Proves asset_callback is invoked synchronously per file, interleaved
    with progress_callback — not collected and fired once at the end."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"a")
    (input_dir / "DJI_0002.MP4").write_bytes(b"b")

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)

    call_order: list[str] = []
    service.run(
        input_dir,
        client="Nike",
        project="Zomer",
        dry_run=False,
        progress_callback=lambda update: call_order.append(f"progress:{update.processed}"),
        asset_callback=lambda asset: call_order.append(f"asset:{asset.source_path.name}"),
    )

    # Voor elk bestand komt asset_callback vóór progress_callback van datzelfde
    # bestand — nooit alle asset-callbacks pas na alle progress-callbacks.
    assert call_order == [
        "asset:DJI_0001.MP4",
        "progress:1",
        "asset:DJI_0002.MP4",
        "progress:2",
    ]


def test_run_without_asset_callback_behaves_exactly_as_before(tmp_path, camera_profiles):
    """Backward-compatibility: existing callers (CLI, tests) that never pass
    asset_callback must see no behavior change at all."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    config = _make_config(tmp_path)
    service = _make_service(config, camera_profiles)

    report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    assert report.assets[0].outcome == AssetOutcome.COPIED
    assert report.assets[0].destination_path.exists()


class TestCollisionProtection:
    def test_identical_content_at_destination_is_treated_as_duplicate_and_never_copied(
        self, tmp_path, camera_profiles
    ):
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"same content")

        # Simuleert een bestand dat al op de bestemming staat zonder dat het
        # manifest ervan weet (bijv. een eerdere, onderbroken run).
        destination_dir = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI")
        destination_dir.mkdir(parents=True)
        destination = destination_dir / "DJI_0001.MP4"
        destination.write_bytes(b"same content")

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.outcome == AssetOutcome.DUPLICATE_SKIPPED
        assert asset.name_conflict_resolved is False
        assert destination.read_bytes() == b"same content"  # ongewijzigd, niet herschreven
        assert not config.manifest_path.exists()  # niets nieuws geregistreerd

    def test_different_content_at_destination_gets_an_automatic_suffix(
        self, tmp_path, camera_profiles
    ):
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"new content")

        destination_dir = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI")
        destination_dir.mkdir(parents=True)
        original = destination_dir / "DJI_0001.MP4"
        original.write_bytes(b"different, unrelated content")

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.outcome == AssetOutcome.COPIED
        assert asset.name_conflict_resolved is True
        assert asset.destination_path.name == "DJI_0001_001.MP4"
        # het bestaande bestand op de oorspronkelijke naam is nooit aangeraakt
        assert original.read_bytes() == b"different, unrelated content"
        assert asset.destination_path.read_bytes() == b"new content"

    def test_multiple_collisions_increment_the_suffix(self, tmp_path, camera_profiles):
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"third variant")

        destination_dir = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI")
        destination_dir.mkdir(parents=True)
        (destination_dir / "DJI_0001.MP4").write_bytes(b"first variant")
        (destination_dir / "DJI_0001_001.MP4").write_bytes(b"second variant")

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.destination_path.name == "DJI_0001_002.MP4"
        assert asset.outcome == AssetOutcome.COPIED

    def test_dry_run_previews_the_resolved_name_without_writing_anything(
        self, tmp_path, camera_profiles
    ):
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"new content")

        destination_dir = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI")
        destination_dir.mkdir(parents=True)
        (destination_dir / "DJI_0001.MP4").write_bytes(b"different content")

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        asset = report.assets[0]
        assert asset.outcome == AssetOutcome.PREVIEW
        assert asset.name_conflict_resolved is True
        assert asset.destination_path.name == "DJI_0001_001.MP4"
        # dry-run: er is niets nieuws op de schijf geschreven
        assert not asset.destination_path.exists()


# -- Fase 4.1: destination-capacity safety hardening (2026-09-15) -----------------
#
# See that day's forensic audit: a real manual test drove a destination disk
# to 0 bytes free mid-run, which surfaced as a misleading "source unreadable"
# error via an uncaught OSError from the action-log write. These tests cover
# the preflight capacity check, the runtime ENOSPC classification/cleanup,
# and that a non-ENOSPC destination-write failure is still classified
# correctly (never silently swallowed, never conflated with "source").


class _FixedFreeSpaceStorage(Storage):
    """Wraps the real LocalFilesystemStorage but reports a controlled, fixed
    free-space figure — lets capacity-preflight tests run against real
    tmp_path files without ever needing to actually fill a real disk (this
    round's testing rules explicitly forbid that)."""

    def __init__(self, free_bytes_value: int) -> None:
        self._real = LocalFilesystemStorage()
        self._free_bytes_value = free_bytes_value

    def list_files(self, root):
        return self._real.list_files(root)

    def exists(self, path):
        return self._real.exists(path)

    def checksum(self, path):
        return self._real.checksum(path)

    def copy(self, source, destination):
        return self._real.copy(source, destination)

    def free_bytes(self, path):
        return self._free_bytes_value

    def remove(self, path):
        self._real.remove(path)


class _EnospcAfterPartialWriteStorage(Storage):
    """Simulates a copy() that writes some bytes to the destination before
    failing with ENOSPC — the real C0066.MP4 scenario from the 2026-09-15
    incident (a partial, not a clean all-or-nothing failure). `fail_on`
    names the one source file this should fail for; every other file is
    copied for real via the real adapter."""

    def __init__(self, fail_on: str) -> None:
        self._real = LocalFilesystemStorage()
        self._fail_on = fail_on

    def list_files(self, root):
        return self._real.list_files(root)

    def exists(self, path):
        return self._real.exists(path)

    def checksum(self, path):
        return self._real.checksum(path)

    def copy(self, source, destination):
        if Path(source).name != self._fail_on:
            return self._real.copy(source, destination)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"partial-bytes-only")
        raise OSError(errno.ENOSPC, "No space left on device")

    def free_bytes(self, path):
        return self._real.free_bytes(path)

    def remove(self, path):
        self._real.remove(path)


def _patch_action_logger_log(monkeypatch, *, fail_on_event: str, errno_value: int | None) -> None:
    """Makes `ActionLogger.log()` raise an OSError (with the given errno,
    or none — a plain OSError) the moment it's asked to log `fail_on_event`,
    while every other event still logs for real."""
    original_log = ActionLogger.log

    def _maybe_failing_log(self, event, **fields):
        if event == fail_on_event:
            raise OSError(errno_value, "gesimuleerde schrijffout")
        return original_log(self, event, **fields)

    monkeypatch.setattr(ActionLogger, "log", _maybe_failing_log)


class TestDestinationCapacityPreflight:
    def test_rejects_a_destination_with_insufficient_free_space(self, tmp_path, camera_profiles):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"x" * 1000)

        config = _make_config(tmp_path)
        storage = _FixedFreeSpaceStorage(free_bytes_value=100)  # veel te weinig
        service = _make_service(config, camera_profiles, storage=storage)

        with pytest.raises(DestinationFullError) as excinfo:
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        assert "Onvoldoende ruimte" in str(excinfo.value)
        assert "Benodigd" in str(excinfo.value)
        assert "Beschikbaar" in str(excinfo.value)
        assert not config.manifest_path.exists()  # niets gekopieerd of geregistreerd

    def test_accepts_a_destination_with_sufficient_free_space(self, tmp_path, camera_profiles):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"x" * 1000)

        config = _make_config(tmp_path)
        storage = _FixedFreeSpaceStorage(free_bytes_value=10 * 1024**3)  # 10 GB, ruim genoeg
        service = _make_service(config, camera_profiles, storage=storage)

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        assert report.assets[0].outcome == AssetOutcome.COPIED

    def test_the_safety_margin_is_actually_taken_into_account(self, tmp_path, camera_profiles):
        """Available space that covers the raw file size but not the extra
        margin on top must still be rejected — proves the margin is really
        added, not just a bare files-vs-available comparison."""
        from many_ingest.core.ingest_service import _DESTINATION_FREE_SPACE_MARGIN_BYTES

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        content = b"x" * 1000
        (input_dir / "DJI_0001.MP4").write_bytes(content)

        config = _make_config(tmp_path)
        just_short = len(content) + _DESTINATION_FREE_SPACE_MARGIN_BYTES - 1
        storage = _FixedFreeSpaceStorage(free_bytes_value=just_short)
        service = _make_service(config, camera_profiles, storage=storage)

        with pytest.raises(DestinationFullError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    def test_accepts_exactly_files_plus_margin(self, tmp_path, camera_profiles):
        from many_ingest.core.ingest_service import _DESTINATION_FREE_SPACE_MARGIN_BYTES

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        content = b"x" * 1000
        (input_dir / "DJI_0001.MP4").write_bytes(content)

        config = _make_config(tmp_path)
        exactly_enough = len(content) + _DESTINATION_FREE_SPACE_MARGIN_BYTES
        storage = _FixedFreeSpaceStorage(free_bytes_value=exactly_enough)
        service = _make_service(config, camera_profiles, storage=storage)

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        assert report.assets[0].outcome == AssetOutcome.COPIED

    def test_dry_run_ignores_destination_capacity_entirely(self, tmp_path, camera_profiles):
        """A preview must stay strictly read-only — never rejected, never
        even asked about free space, no matter how full the destination
        claims to be. Same principle as the destination-writability preflight."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"x" * 1000)

        config = _make_config(tmp_path)
        storage = _FixedFreeSpaceStorage(free_bytes_value=0)  # "hartstikke vol"
        service = _make_service(config, camera_profiles, storage=storage)

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        assert report.assets[0].outcome == AssetOutcome.PREVIEW
        assert not config.storage_root.exists()


class TestRuntimeEnospc:
    def test_enospc_during_copy_raises_destination_full_and_cleans_up_the_partial_file(
        self, tmp_path, camera_profiles
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        good = input_dir / "DJI_0001.MP4"
        good.write_bytes(b"good bytes")
        bad = input_dir / "DJI_0002.MP4"
        bad.write_bytes(b"never actually copied")

        config = _make_config(tmp_path)
        storage = _EnospcAfterPartialWriteStorage(fail_on="DJI_0002.MP4")
        service = _make_service(config, camera_profiles, storage=storage)

        with pytest.raises(DestinationFullError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        workspace = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI")

        # De partial destination-file van de mislukte copy is weggehaald:
        assert not (workspace / "DJI_0002.MP4").exists()

        # Het eerder al gekopieerde, geverifieerde bestand blijft gewoon staan
        # en geregistreerd:
        verified_path = workspace / "DJI_0001.MP4"
        assert verified_path.exists()
        assert verified_path.read_bytes() == b"good bytes"
        schema = json.loads(config.manifest_path.read_text())
        assert len(schema["assets"]) == 1
        assert schema["assets"][0]["original_path"] == str(good)

        # De bron is volledig onaangetast — beide bestanden, ongewijzigd:
        assert good.read_bytes() == b"good bytes"
        assert bad.read_bytes() == b"never actually copied"

    def test_a_non_enospc_copy_failure_still_behaves_exactly_as_before(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """Regression guard: only ENOSPC aborts the whole run. Any other
        copy-write failure must keep today's existing behaviour — a per-asset
        FAILED_VERIFICATION, the run itself still completes normally (see
        test_genuine_content_copy_failure_is_still_a_real_failure above,
        which this mirrors) — never a DestinationFullError, never abort."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        def _broken_copyfile(src, dst):
            raise OSError("disk vol (gesimuleerd, geen ENOSPC-errno)")

        monkeypatch.setattr(
            "many_ingest.adapters.local_fs_storage.shutil.copyfile", _broken_copyfile
        )

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        assert report.assets[0].outcome == AssetOutcome.FAILED_VERIFICATION
        assert not config.manifest_path.exists()

    def test_enospc_during_action_log_write_is_classified_as_destination_full(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"x")

        _patch_action_logger_log(monkeypatch, fail_on_event="asset_processed", errno_value=errno.ENOSPC)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        with pytest.raises(DestinationFullError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    def test_non_enospc_action_log_failure_is_destination_unavailable_not_source(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """The 2026-09-15 bug, reproduced directly: an unclassified OSError
        from the action-log write used to escape all the way up unclassified
        (this exact call had no try/except at all). It must now be a
        distinct, destination-specific error — never left bare (which is
        what let a caller's generic `except OSError` mislabel it as
        "source unreadable")."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"x")

        _patch_action_logger_log(monkeypatch, fail_on_event="asset_processed", errno_value=errno.EIO)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        with pytest.raises(DestinationUnavailableError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

    def test_enospc_during_manifest_write_is_destination_full_and_keeps_the_copied_file(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"good content")

        def _failing_register(self, record):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(JSONManifest, "register", _failing_register)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        with pytest.raises(DestinationFullError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        # Het bestand zelf is al compleet gekopieerd én checksum-geverifieerd
        # vóór de manifest-write faalde — dat maakt het geen "partial" bestand,
        # en het wordt dus nooit weggegooid, alleen de hele run stopt.
        workspace = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI")
        assert (workspace / "DJI_0001.MP4").read_bytes() == b"good content"

    def test_non_enospc_manifest_write_failure_is_destination_unavailable(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"good content")

        def _failing_register(self, record):
            raise OSError(errno.EIO, "gesimuleerde I/O-fout")

        monkeypatch.setattr(JSONManifest, "register", _failing_register)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        with pytest.raises(DestinationUnavailableError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)


class TestFase50ClassificationFoundation:
    """Fase 5.0 — additive domain-model fields (media_type/manufacturer/model/
    source_relative_path). None of these tests exercise anything new about
    destination paths, category, camera_profile, confidence, dedupe, or
    collision resolution — those are covered, unchanged, by the tests above."""

    def test_source_relative_path_captures_the_nested_card_structure(
        self, tmp_path, camera_profiles
    ):
        input_dir = tmp_path / "input"
        nested = input_dir / "PRIVATE" / "M4ROOT" / "CLIP"
        nested.mkdir(parents=True)
        (nested / "C0001.MXF").write_bytes(b"fake mxf bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        asset = report.assets[0]
        assert asset.source_relative_path == Path("PRIVATE/M4ROOT/CLIP/C0001.MXF")
        # Card-structuur mag geen enkele invloed hebben op waar het bestand
        # terechtkomt — alleen de bestandsnaam telt mee, zoals altijd al.
        assert asset.destination_path.name == "C0001.MXF"

    def test_source_relative_path_falls_back_to_none_when_relative_to_fails(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        def _broken_relative_to(self, *args, **kwargs):
            raise ValueError("gesimuleerd: pad valt niet onder de bronroot")

        monkeypatch.setattr(Path, "relative_to", _broken_relative_to)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.source_relative_path is None
        # Een niet-bepaalbare provenance-waarde mag de rest van de asset nooit
        # beïnvloeden — de ingest zelf slaagt gewoon.
        assert asset.outcome == AssetOutcome.COPIED
        assert asset.destination_path.exists()

    def test_destination_path_is_exactly_unchanged_by_fase_5_0(self, tmp_path, camera_profiles):
        """Regression guard: threading `source` into _process_asset() for
        source_relative_path must not shift destination resolution by even
        one character."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        expected = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI") / "DJI_0001.MP4"
        assert report.assets[0].destination_path == expected

    def test_real_run_exposes_media_type_manufacturer_and_model_on_the_asset_result(
        self, tmp_path, camera_profiles
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.media_type == MediaType.VIDEO
        assert asset.manufacturer == "DJI"
        assert asset.model is None  # DJI-profiel is generiek, geen specifiek model
        assert asset.source_relative_path == Path("DJI_0001.MP4")

    def test_real_run_persists_fase_5_0_fields_in_the_manifest(self, tmp_path, camera_profiles):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        schema = json.loads(config.manifest_path.read_text())
        record = schema["assets"][0]
        assert record["media_type"] == "video"
        assert record["manufacturer"] == "DJI"
        assert record["model"] is None
        assert record["source_relative_path"] == "DJI_0001.MP4"
        # Bestaande velden blijven onaangeroerd.
        assert record["category"] == "Drone"
        assert record["camera_profile"] == "DJI"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("24000/1001", "24000/1001"),
        ("25/1", "25/1"),
        ("30000/1001", "30000/1001"),
        ("50/1", "50/1"),
        ("60000/1001", "60000/1001"),
        ("0/0", None),
        (None, None),
        ("", None),
        ("not-a-fraction", None),
        ("25", None),
        ("25/1/2", None),
        ("25/0", None),
        ("-25/1", None),
        ("25/-1", None),
    ],
)
def test_normalize_frame_rate(raw, expected):
    """Fase 5.1: the exact rational string is the persisted source of truth
    (never a float) — a valid value is returned byte-for-byte unchanged,
    never reformatted; only genuinely unusable input becomes None."""
    assert _normalize_frame_rate(raw) == expected


class TestFase51MetadataProvenance:
    """Fase 5.1 — classification_source + technical metadata
    (codec/width/height/frame_rate/duration_seconds/has_video_stream/
    has_audio_stream). None of these tests touch destination paths,
    category, camera_profile, confidence, manufacturer, model, dedupe, or
    collision resolution — those are covered, unchanged, elsewhere."""

    def test_real_run_exposes_classification_source_on_the_asset_result(
        self, tmp_path, camera_profiles
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        # "fake video bytes" is not a real video -> ffprobe fails -> classify()
        # falls back to the DJI_* filename pattern alone (MEDIUM tier).
        assert asset.classification_source == ClassificationSource.GENERIC_FILENAME_PATTERN

    def test_missing_probe_result_leaves_all_technical_metadata_none(
        self, tmp_path, camera_profiles
    ):
        """None (not False) means "no reliable probe data" — ffprobe failed
        on this file entirely, never a claim about what streams exist."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.codec is None
        assert asset.width is None
        assert asset.height is None
        assert asset.frame_rate is None
        assert asset.duration_seconds is None
        assert asset.has_video_stream is None
        assert asset.has_audio_stream is None
        # Een ontbrekende probe mag de ingest zelf nooit laten falen.
        assert asset.outcome == AssetOutcome.COPIED

    @requires_ffmpeg
    def test_real_video_technical_metadata_is_extracted_and_persisted(
        self, tmp_path, camera_profiles
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        clip = input_dir / "DJI_0001.MP4"
        subprocess.run(
            [
                "ffmpeg", "-v", "quiet", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=1",
                "-c:v", "libx264",
                str(clip),
            ],
            check=True,
        )

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.codec is not None
        assert asset.width == 320
        assert asset.height == 240
        assert asset.frame_rate == "25/1"
        assert asset.duration_seconds is not None
        assert asset.has_video_stream is True
        assert asset.has_audio_stream is False

        schema = json.loads(config.manifest_path.read_text())
        record = schema["assets"][0]
        assert record["codec"] == asset.codec
        assert record["width"] == 320
        assert record["height"] == 240
        assert record["frame_rate"] == "25/1"
        assert record["has_video_stream"] is True
        assert record["has_audio_stream"] is False
        # classification_source moet als stabiele string geserialiseerd zijn,
        # nooit als Python enum-representatie (bv. niet
        # "ClassificationSource.GENERIC_FILENAME_PATTERN").
        assert record["classification_source"] == "generic_filename_pattern"

    def test_destination_path_is_exactly_unchanged_by_fase_5_1(self, tmp_path, camera_profiles):
        """Regression guard, same style as the Fase 5.0 equivalent above."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        expected = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI") / "DJI_0001.MP4"
        assert report.assets[0].destination_path == expected

    def test_reporting_counts_are_unaffected_by_fase_5_1_fields(self, tmp_path, camera_profiles):
        """Regression guard: core/report.py is untouched in Fase 5.1 — the
        new fields must not perturb the existing summarize() output."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        summary = summarize(report)
        assert summary.total_files == 1
        assert summary.video_count == 1
        assert summary.camera_profile_counts == {"DJI": 1}
        assert summary.errors == 0


class _CountingChecksumStorage(LocalFilesystemStorage):
    """Fase 5.2 — wraps the real storage adapter to count how often each
    path is actually checksummed, so the "never hash a main-media file
    twice" guarantee can be verified as observable behaviour through the
    existing Storage port, not as a brittle inspection of an internal cache
    dict."""

    def __init__(self) -> None:
        super().__init__()
        self.checksum_calls: dict[Path, int] = {}

    def checksum(self, path: Path) -> str:
        self.checksum_calls[path] = self.checksum_calls.get(path, 0) + 1
        return super().checksum(path)


_REAL_UMID = "060A2B340101010501010D43130000006AED1A61791206D210322CFFFE7DA477"


def _write_umid_matched_pair(input_dir: Path, umid: str = _REAL_UMID) -> None:
    """Shared by TestFase52SidecarAssociation and
    TestFase53SidecarPlacementInheritance — a module-level helper (not a
    class method) so Fase 5.3's tests can reuse the exact same proven
    UMID-matched fixture pair without duplicating it."""
    (input_dir / "611_4921.MXF").write_bytes(b"fake mxf bytes")
    (input_dir / "611_4921M01.XML").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">\n'
        f'  <TargetMaterial umidRef="{umid}"/>\n'
        '  <Duration value="871"/>\n'
        '  <LtcChangeTable tcFps="25" halfStep="false"></LtcChangeTable>\n'
        "</NonRealTimeMeta>\n"
    )


def _fake_safe_probe_with_umid(umid: str):
    def _fake(path: Path):
        if path.name == "611_4921.MXF":
            return _probe_result_with_umid(f"0x{umid}")
        return None

    return _fake


class TestFase52SidecarAssociation:
    """Fase 5.2 — sidecar_of_asset_id/relationship_evidence. None of these
    tests touch destination paths, category, camera_profile, confidence,
    manufacturer, model, classification_source, checksum/dedupe semantics,
    or collision resolution — those are covered, unchanged, elsewhere."""

    def _write_umid_matched_pair(self, input_dir: Path, umid: str = _REAL_UMID) -> None:
        _write_umid_matched_pair(input_dir, umid)

    def _fake_safe_probe_with_umid(self, umid: str):
        return _fake_safe_probe_with_umid(umid)

    def test_umid_matched_sidecar_gets_the_main_medias_checksum(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        self._write_umid_matched_pair(input_dir)
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", self._fake_safe_probe_with_umid(_REAL_UMID)
        )

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert sidecar_asset.sidecar_of_asset_id == main_asset.checksum
        # De sidecar behoudt gewoon zijn eigen, andere checksum.
        assert sidecar_asset.checksum != main_asset.checksum

    def test_non_sidecar_assets_have_no_relationship(self, tmp_path, camera_profiles):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.sidecar_of_asset_id is None
        assert asset.relationship_evidence == RelationshipEvidence.NONE

    def test_sidecar_without_a_naming_candidate_has_no_relationship(
        self, tmp_path, camera_profiles
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "notes.XML").write_text("<not-sony-metadata/>")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        asset = report.assets[0]
        assert asset.sidecar_of_asset_id is None
        assert asset.relationship_evidence == RelationshipEvidence.NONE
        assert asset.outcome == AssetOutcome.COPIED  # blokkeert de ingest niet

    def test_dry_run_resolves_relationships_without_writing_anything(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """Sidecar-resolutie leest alleen (XML-tekst, ffprobe) — een preview
        blijft dus net zo read-only als vóór Fase 5.2."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        self._write_umid_matched_pair(input_dir)
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", self._fake_safe_probe_with_umid(_REAL_UMID)
        )

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        assert by_name["611_4921M01.XML"].relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert not config.storage_root.exists()  # niets gekopieerd
        assert not config.manifest_path.exists()  # niets geregistreerd

    def test_main_media_referenced_by_a_sidecar_is_checksummed_only_once(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        self._write_umid_matched_pair(input_dir)
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", self._fake_safe_probe_with_umid(_REAL_UMID)
        )

        config = _make_config(tmp_path)
        counting_storage = _CountingChecksumStorage()
        service = _make_service(config, camera_profiles, storage=counting_storage)
        service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        main_media_path = input_dir / "611_4921.MXF"
        assert counting_storage.checksum_calls[main_media_path] == 1

    def test_malformed_sidecar_xml_does_not_block_ingest(self, tmp_path, camera_profiles):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "611_4921.MXF").write_bytes(b"fake mxf bytes")
        (input_dir / "611_4921M01.XML").write_text("<NonRealTimeMeta><Unclosed>")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        sidecar_asset = by_name["611_4921M01.XML"]
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.NONE
        assert sidecar_asset.sidecar_of_asset_id is None
        assert sidecar_asset.outcome == AssetOutcome.COPIED

    def test_unlinked_sidecar_destination_is_unaffected_by_fase_5_3(self, tmp_path, camera_profiles):
        """Fase 5.2's original regression guard, narrowed to what's still
        true after Fase 5.3: a sidecar WITHOUT proven evidence still lands in
        Onbekend/, exactly as before. The linked case (this same
        611_4921.MXF/611_4921M01.XML pair, with a mocked UMID match) now
        co-locates instead — see TestFase53SidecarPlacementInheritance."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        self._write_umid_matched_pair(input_dir)
        # Geen safe_probe-mock hier: zonder een echte MXF-UMID blijft de
        # relatie NONE (zie resolve_relationship), dus dit bewijst het
        # ongekoppelde pad, niet het gekoppelde.

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        assert by_name["611_4921M01.XML"].relationship_evidence == RelationshipEvidence.NONE
        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Onbekend")
        assert by_name["611_4921M01.XML"].destination_path == expected_dir / "611_4921M01.XML"

    def test_classification_is_unaffected_by_sidecar_association(self, tmp_path, camera_profiles):
        """Regression guard: category/camera_profile/confidence/manufacturer/
        model/classification_source stay exactly what they were before
        Fase 5.2, for both the sidecar and its main media."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        self._write_umid_matched_pair(input_dir)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        sidecar_asset = by_name["611_4921M01.XML"]
        assert sidecar_asset.category == "Onbekend"
        assert sidecar_asset.camera_profile == "Onbekend"
        assert sidecar_asset.classification_source == ClassificationSource.NO_SIGNAL_MATCHED


def _probe_result_with_umid(material_package_umid: str | None):
    from many_ingest.metadata_extractor import ProbeResult

    return ProbeResult(
        has_video_stream=True,
        has_audio_stream=True,
        codec=None,
        width=None,
        height=None,
        frame_rate=None,
        duration_seconds=None,
        make=None,
        model=None,
        creation_time=None,
        major_brand=None,
        compatible_brands=None,
        container_format=None,
        material_package_umid=material_package_umid,
    )


class TestResolveSidecarRelationshipsOrderIndependence:
    """Fase 5.2 — proves _resolve_sidecar_relationships() gives an identical
    result regardless of which order scan_result.files happens to list a
    sidecar and its main media in (rglob order is not something this logic
    may depend on)."""

    def test_same_result_regardless_of_scan_order(self, tmp_path, camera_profiles, monkeypatch):
        source = tmp_path
        xml_path = tmp_path / "611_4921M01.XML"
        xml_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">\n'
            f'  <TargetMaterial umidRef="{_REAL_UMID}"/>\n'
            '  <Duration value="871"/>\n'
            '  <LtcChangeTable tcFps="25" halfStep="false"></LtcChangeTable>\n'
            "</NonRealTimeMeta>\n"
        )
        media_path = tmp_path / "611_4921.MXF"
        media_path.write_bytes(b"fake mxf bytes")

        monkeypatch.setattr(
            ingest_service_module,
            "safe_probe",
            lambda path: _probe_result_with_umid(f"0x{_REAL_UMID}") if path == media_path else None,
        )

        sidecar_first, placements_sidecar_first = _resolve_sidecar_relationships(
            [xml_path, media_path], source, camera_profiles
        )
        media_first, placements_media_first = _resolve_sidecar_relationships(
            [media_path, xml_path], source, camera_profiles
        )

        assert sidecar_first == media_first
        assert sidecar_first == {xml_path: (media_path, RelationshipEvidence.UMID_MATCH)}
        # Fase 5.3 — the inherited-placement index is equally scan-order-independent.
        assert placements_sidecar_first == placements_media_first
        assert placements_sidecar_first[media_path].category == "Camera"
        assert placements_sidecar_first[media_path].camera_profile == "Sony FX6"


# -- Fase 5.3: Camera-Aware Organization -------------------------------------
#
# Target layout (see docs): {date}_Raw/Camera/{profile}/, {date}_Raw/Drone/{profile}/,
# with Audio/Onbekend staying flat. A proven sidecar inherits ONLY its main
# media's placement (category/camera_profile/recording_date), never its own
# classification fields.


class TestFase53CameraAwareOrganization:
    """Fase 5.3 — per-profile destination subfolders for Camera/Drone, flat
    for Audio/Onbekend. Classification itself (classify()) is unchanged;
    only _build_workspace_path()'s folder layout is new."""

    def test_each_proven_camera_profile_gets_its_own_subfolder(
        self, tmp_path, camera_profiles, monkeypatch, make_probe_result
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        probes = {
            # Sony FX6 — HIGH via confirmed_filename_pattern alone, no probe needed.
            "611_1234.MXF": None,
            # Sony FX3 — HIGH via metadata_make_or_model (model_contains "FX3").
            "fx3_clip.MP4": make_probe_result(
                model="ILME-FX3", major_brand="XAVC", container_format="mp4"
            ),
            # Sony A7 IV — HIGH via metadata_make_or_model (model_contains "ILCE-7M4").
            "a7iv_clip.MOV": make_probe_result(model="ILCE-7M4"),
            # GoPro — HIGH via metadata_make_or_model (make_contains "GoPro").
            "gopro_clip.MP4": make_probe_result(make="GoPro"),
            # DJI — MEDIUM via the generic "^DJI_.*" filename pattern, no probe needed.
            "DJI_0009.MP4": None,
        }
        for name in probes:
            (input_dir / name).write_bytes(b"fake bytes")

        monkeypatch.setattr(
            ingest_service_module, "safe_probe", lambda path: probes.get(path.name)
        )

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        by_name = {asset.source_path.name: asset for asset in report.assets}

        expectations = [
            ("611_1234.MXF", "Camera", "Sony FX6"),
            ("fx3_clip.MP4", "Camera", "Sony FX3"),
            ("a7iv_clip.MOV", "Camera", "Sony A7 IV"),
            ("gopro_clip.MP4", "Camera", "GoPro"),
            ("DJI_0009.MP4", "Drone", "DJI"),
        ]
        for filename, category, profile in expectations:
            asset = by_name[filename]
            assert asset.category == category
            assert asset.camera_profile == profile
            expected_dir = _workspace_dir(config, "Nike", "Zomer", category, camera_profile=profile)
            assert asset.destination_path == expected_dir / filename

    def test_audio_stays_flat_no_profile_subfolder(self, tmp_path, camera_profiles):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "silence.wav").write_bytes(b"fake audio bytes")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        asset = report.assets[0]
        assert asset.category == "Audio"
        assert asset.camera_profile == "Audio"
        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Audio")
        assert asset.destination_path == expected_dir / "silence.wav"

    def test_unknown_file_stays_flat_under_onbekend(self, tmp_path, camera_profiles):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "random_notes.txt").write_bytes(b"not a recognized asset at all")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        asset = report.assets[0]
        assert asset.category == "Onbekend"
        assert asset.camera_profile == "Onbekend"
        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Onbekend")
        assert asset.destination_path == expected_dir / "random_notes.txt"


class TestFase53ProfileSegmentSafety:
    """Fase 5.3 — _is_safe_profile_segment()/_build_workspace_path() defense
    in depth: classify() can never actually produce an unsafe camera_profile
    value (see _is_safe_profile_segment()'s docstring), so these are unit
    tests directly against the path-building functions, not full-pipeline
    tests — there is no way to reach this branch through classify() itself."""

    @pytest.mark.parametrize("unsafe_value", ["", ".", "..", "a/b", "a\\b", "../../etc"])
    def test_is_safe_profile_segment_rejects_unsafe_values(self, unsafe_value):
        assert ingest_service_module._is_safe_profile_segment(unsafe_value) is False

    @pytest.mark.parametrize("safe_value", ["Sony FX6", "DJI", "GoPro", "Sony A7 IV"])
    def test_is_safe_profile_segment_accepts_real_profile_labels(self, safe_value):
        assert ingest_service_module._is_safe_profile_segment(safe_value) is True

    def test_build_workspace_path_falls_back_to_flat_when_profile_is_unsafe(self):
        base = Path("/storage")
        result = ingest_service_module._build_workspace_path(
            storage_root=base,
            client="Nike",
            project="Zomer",
            recording_date=dt.date(2026, 10, 13),
            category="Camera",
            camera_profile="../evil",
            filename="clip.mp4",
        )
        assert result == (
            base / "Klanten" / "Nike" / "Zomer" / "2026-10-13_Raw" / "Camera" / "clip.mp4"
        )

    def test_build_workspace_path_never_nests_audio_or_onbekend(self):
        base = Path("/storage")
        for category, profile, filename in (
            ("Audio", "Audio", "clip.wav"),
            ("Onbekend", "Onbekend", "clip.bin"),
        ):
            result = ingest_service_module._build_workspace_path(
                storage_root=base,
                client="Nike",
                project="Zomer",
                recording_date=dt.date(2026, 10, 13),
                category=category,
                camera_profile=profile,
                filename=filename,
            )
            assert result == (
                base / "Klanten" / "Nike" / "Zomer" / "2026-10-13_Raw" / category / filename
            )


class TestFase53SidecarPlacementInheritance:
    """Fase 5.3 — a proven sidecar inherits ONLY the category/camera_profile/
    recording_date of its main media for destination-path purposes; its own
    classification fields (category/camera_profile/confidence/manufacturer/
    model/classification_source) are never touched — see
    TestFase52SidecarAssociation.test_classification_is_unaffected_by_sidecar_association,
    which still proves exactly that, unchanged."""

    def test_umid_matched_sidecar_is_co_located_with_its_main_medias_resolved_folder(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert main_asset.category == "Camera"
        assert main_asset.camera_profile == "Sony FX6"
        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Camera", camera_profile="Sony FX6")
        assert main_asset.destination_path == expected_dir / "611_4921.MXF"
        assert sidecar_asset.destination_path == expected_dir / "611_4921M01.XML"
        # De sidecar's eigen classificatievelden blijven ongewijzigd (Onbekend) —
        # alleen het pad erft van de hoofdmedia, zie de klassedocstring.
        assert sidecar_asset.category == "Onbekend"
        assert sidecar_asset.camera_profile == "Onbekend"

    def test_name_and_duration_matched_sidecar_is_co_located_with_its_main_media(
        self, tmp_path, camera_profiles, monkeypatch, make_probe_result
    ):
        """The UMID-less fallback path (FX3/A7IV-style MP4, no UMID exposed
        via ffprobe) — evidence comes from a matching duration instead, and
        placement inheritance works identically."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "C0050.MP4").write_bytes(b"fake mp4 bytes")
        (input_dir / "C0050M01.XML").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">\n'
            "  <Duration value=\"871\"/>\n"
            '  <LtcChangeTable tcFps="25" halfStep="false"></LtcChangeTable>\n'
            "</NonRealTimeMeta>\n"
        )

        main_media_probe = make_probe_result(
            model="ILME-FX3",
            major_brand="XAVC",
            container_format="mp4",
            duration_seconds=871 / 25,
        )
        monkeypatch.setattr(
            ingest_service_module,
            "safe_probe",
            lambda path: main_media_probe if path.name == "C0050.MP4" else None,
        )

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        sidecar_asset = by_name["C0050M01.XML"]
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.NAME_AND_DURATION_MATCH

        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Camera", camera_profile="Sony FX3")
        assert by_name["C0050.MP4"].destination_path == expected_dir / "C0050.MP4"
        assert sidecar_asset.destination_path == expected_dir / "C0050M01.XML"

    def test_unlinked_sidecar_without_any_naming_candidate_stays_under_onbekend(
        self, tmp_path, camera_profiles
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "notes.XML").write_text("<not-sony-metadata/>")

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        asset = report.assets[0]
        assert asset.relationship_evidence == RelationshipEvidence.NONE
        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Onbekend")
        assert asset.destination_path == expected_dir / "notes.XML"

    def test_placement_is_identical_regardless_of_scan_order(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )

        def _run_with_order(order_dir: Path, write_sidecar_first: bool) -> Path:
            order_dir.mkdir()
            if write_sidecar_first:
                _write_umid_matched_pair(order_dir)
            else:
                # Zelfde bestanden, maar de hoofdmedia wordt eerst op schijf
                # gezet — scan-volgorde (rglob) hangt af van het bestandssysteem,
                # dus dit bewijst dat de UITKOMST er niet van afhangt, niet dat
                # scan() zelf een bepaalde volgorde garandeert.
                (order_dir / "611_4921.MXF").write_bytes(b"fake mxf bytes")
                (order_dir / "611_4921M01.XML").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">\n'
                    f'  <TargetMaterial umidRef="{_REAL_UMID}"/>\n'
                    '  <Duration value="871"/>\n'
                    '  <LtcChangeTable tcFps="25" halfStep="false"></LtcChangeTable>\n'
                    "</NonRealTimeMeta>\n"
                )
            config = _make_config(order_dir.parent / f"{order_dir.name}_storage")
            service = _make_service(config, camera_profiles)
            report = service.run(order_dir, client="Nike", project="Zomer", dry_run=True)
            by_name = {asset.source_path.name: asset for asset in report.assets}
            return by_name["611_4921M01.XML"].destination_path.relative_to(config.storage_root)

        sidecar_first_result = _run_with_order(tmp_path / "sidecar_first", True)
        media_first_result = _run_with_order(tmp_path / "media_first", False)

        assert sidecar_first_result == media_first_result

    def test_sidecar_placement_is_correct_even_when_main_media_is_a_duplicate_this_run(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        # Run 1: alleen de hoofdmedia, zodat die al in het manifest staat.
        run1_dir = tmp_path / "run1"
        run1_dir.mkdir()
        (run1_dir / "611_4921.MXF").write_bytes(b"fake mxf bytes")
        service.run(run1_dir, client="Nike", project="Zomer", dry_run=False)

        # Run 2: dezelfde hoofdmedia-inhoud (nu een duplicate) plus zijn sidecar (nieuw).
        run2_dir = tmp_path / "run2"
        run2_dir.mkdir()
        _write_umid_matched_pair(run2_dir)
        report = service.run(run2_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.DUPLICATE_SKIPPED
        assert sidecar_asset.outcome == AssetOutcome.COPIED
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH

        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Camera", camera_profile="Sony FX6")
        assert main_asset.destination_path == expected_dir / "611_4921.MXF"
        assert sidecar_asset.destination_path == expected_dir / "611_4921M01.XML"

    def test_sidecar_placement_is_correct_even_when_main_media_fails_verification(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """A failed main media must never silently present the sidecar as
        part of a fully-succeeded recording group — there is no such
        aggregate status anywhere in IngestService; each asset's `outcome`
        stays fully independent, exactly as before Fase 5.3. This only
        proves the sidecar's own placement is still correct even though its
        main media's copy failed."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)

        config = _make_config(tmp_path)
        real_storage = LocalFilesystemStorage()

        class _FailsVerificationForOneFile(Storage):
            def list_files(self, root):
                return real_storage.list_files(root)

            def exists(self, path):
                return real_storage.exists(path)

            def copy(self, source, destination):
                real_storage.copy(source, destination)

            def checksum(self, path):
                path = Path(path)
                if path.name == "611_4921.MXF" and config.storage_root in path.parents:
                    return "corrupted-destination-checksum"
                return real_storage.checksum(path)

            def free_bytes(self, path):
                return real_storage.free_bytes(path)

            def remove(self, path):
                real_storage.remove(path)

        service = _make_service(config, camera_profiles, storage=_FailsVerificationForOneFile())
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.FAILED_VERIFICATION
        assert sidecar_asset.outcome == AssetOutcome.COPIED
        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Camera", camera_profile="Sony FX6")
        assert sidecar_asset.destination_path == expected_dir / "611_4921M01.XML"

    def test_sidecar_placement_is_unaffected_by_a_name_collision_on_the_main_media(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        collision_dir = _workspace_dir(config, "Nike", "Zomer", "Camera", camera_profile="Sony FX6")
        collision_dir.mkdir(parents=True)
        (collision_dir / "611_4921.MXF").write_bytes(b"different, unrelated content")

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.name_conflict_resolved is True
        assert main_asset.destination_path.name == "611_4921_001.MXF"
        # De map blijft gelijk ondanks de naamconflict-suffix op de hoofdmedia —
        # de sidecar belandt in diezelfde map, onder zijn eigen, ongewijzigde naam.
        assert sidecar_asset.destination_path.parent == collision_dir
        assert sidecar_asset.destination_path.name == "611_4921M01.XML"

    def test_dry_run_and_real_ingest_compute_identical_sidecar_placement(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        preview = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)
        real = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        preview_sidecar = next(
            a for a in preview.assets if a.source_path.name == "611_4921M01.XML"
        )
        real_sidecar = next(a for a in real.assets if a.source_path.name == "611_4921M01.XML")
        assert preview_sidecar.destination_path == real_sidecar.destination_path
        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Camera", camera_profile="Sony FX6")
        assert real_sidecar.destination_path == expected_dir / "611_4921M01.XML"


class TestFase53ManifestBackwardCompatibility:
    """Fase 5.3 — the new camera-aware layout only governs where NEW assets
    are placed; it never reads, rewrites, or migrates existing manifest
    records. An old-shape record (as a pre-5.3 version of Many Ingest would
    have written it, flat destination_path, missing every Fase 5.0+ field)
    must keep deduping correctly, and a genuinely new file in the same run
    must still get the new, nested destination."""

    def test_old_manifest_record_still_dedupes_and_new_files_get_the_new_layout(
        self, tmp_path, camera_profiles
    ):
        config = _make_config(tmp_path)
        config.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        old_content = b"already ingested content"
        old_checksum = hashlib.sha256(old_content).hexdigest()
        old_flat_destination = (
            config.storage_root / "Klanten" / "Nike" / "Zomer" / "2026-01-01_Raw" / "Drone"
            / "DJI_0001.MP4"
        )
        config.manifest_path.write_text(
            json.dumps(
                {
                    "assets": [
                        {
                            "asset_id": old_checksum,
                            "client_id": "Nike",
                            "project_id": "Zomer",
                            "ingest_run_id": "old-run",
                            "operator": "iemand",
                            "source_machine": "oude-machine",
                            "original_path": "/oud/pad/DJI_0001.MP4",
                            "destination_path": str(old_flat_destination),
                            "category": "Drone",
                            "camera_profile": "DJI",
                            "confidence": "middel",
                            "ingested_at": "2026-01-01T00:00:00+00:00",
                            # Bewust GEEN Fase 5.0+/5.1/5.2-velden — dit simuleert een
                            # manifest geschreven door een versie van vóór die fases.
                        }
                    ]
                }
            )
        )

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "DJI_0001.MP4").write_bytes(old_content)
        (input_dir / "DJI_0002.MP4").write_bytes(b"brand new content")

        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {asset.source_path.name: asset for asset in report.assets}
        assert by_name["DJI_0001.MP4"].outcome == AssetOutcome.DUPLICATE_SKIPPED

        new_asset = by_name["DJI_0002.MP4"]
        assert new_asset.outcome == AssetOutcome.COPIED
        expected_dir = _workspace_dir(config, "Nike", "Zomer", "Drone", camera_profile="DJI")
        assert new_asset.destination_path == expected_dir / "DJI_0002.MP4"

        # Het oude record is ongewijzigd gebleven — geen migratie, geen herschrijving.
        schema = json.loads(config.manifest_path.read_text())
        old_record = next(a for a in schema["assets"] if a["asset_id"] == old_checksum)
        assert old_record["destination_path"] == str(old_flat_destination)


# -- Fase 5.3.1A: Sidecar Integrity Hardening (referential integrity) -------
#
# `relationship_evidence` (proven CONTENT relationship) and
# `sidecar_of_asset_id` (MANIFEST relationship — only set when a record for
# that checksum genuinely exists in the manifest right now) are two
# deliberately different claims. See core/ingest_service.py's Fase 5.3.1A
# comment in _process_asset() for the exact rule.


class TestFase531AReferentialIntegrity:
    def test_sidecar_of_asset_id_is_set_when_main_media_is_successfully_copied_and_registered(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)
        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.COPIED
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert sidecar_asset.sidecar_of_asset_id == main_asset.checksum

        # Niet alleen de in-memory AssetResult — het PERSISTEERDE record moet
        # dezelfde, geldige FK bevatten.
        schema = json.loads(config.manifest_path.read_text())
        sidecar_record = next(
            a for a in schema["assets"] if a["asset_id"] == sidecar_asset.checksum
        )
        assert sidecar_record["sidecar_of_asset_id"] == main_asset.checksum
        assert sidecar_record["relationship_evidence"] == "umid_match"

    def test_sidecar_of_asset_id_is_set_when_main_media_is_an_already_registered_duplicate(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        # Run 1: alleen de hoofdmedia, zodat die al een echt manifestrecord heeft.
        run1_dir = tmp_path / "run1"
        run1_dir.mkdir()
        (run1_dir / "611_4921.MXF").write_bytes(b"fake mxf bytes")
        service.run(run1_dir, client="Nike", project="Zomer", dry_run=False)

        # Run 2: dezelfde hoofdmedia-inhoud (nu een bevestigde duplicate) + sidecar (nieuw).
        run2_dir = tmp_path / "run2"
        run2_dir.mkdir()
        _write_umid_matched_pair(run2_dir)
        report = service.run(run2_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.DUPLICATE_SKIPPED
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert sidecar_asset.sidecar_of_asset_id == main_asset.checksum

        # Niet alleen de in-memory AssetResult — het gepersisteerde sidecar-
        # record moet naar een daadwerkelijk aanwezig hoofdmediarecord
        # verwijzen (geregistreerd in run 1, niet opnieuw in run 2).
        schema = json.loads(config.manifest_path.read_text())
        asset_ids = {a["asset_id"] for a in schema["assets"]}
        sidecar_record = next(
            a for a in schema["assets"] if a["asset_id"] == sidecar_asset.checksum
        )
        assert sidecar_record["sidecar_of_asset_id"] == main_asset.checksum
        assert sidecar_record["sidecar_of_asset_id"] in asset_ids
        assert sidecar_record["relationship_evidence"] == "umid_match"

    def test_sidecar_of_asset_id_is_none_when_main_media_fails_verification(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)
        config = _make_config(tmp_path)
        real_storage = LocalFilesystemStorage()

        class _FailsVerificationForOneFile(Storage):
            def list_files(self, root):
                return real_storage.list_files(root)

            def exists(self, path):
                return real_storage.exists(path)

            def copy(self, source, destination):
                real_storage.copy(source, destination)

            def checksum(self, path):
                path = Path(path)
                if path.name == "611_4921.MXF" and config.storage_root in path.parents:
                    return "corrupted-destination-checksum"
                return real_storage.checksum(path)

            def free_bytes(self, path):
                return real_storage.free_bytes(path)

            def remove(self, path):
                real_storage.remove(path)

        service = _make_service(config, camera_profiles, storage=_FailsVerificationForOneFile())
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.FAILED_VERIFICATION
        assert sidecar_asset.outcome == AssetOutcome.COPIED
        # Bewezen bronrelatie blijft behouden...
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        # ...maar geen manifest-FK naar een nooit-geregistreerd hoofdmediarecord.
        assert sidecar_asset.sidecar_of_asset_id is None

        # Het manifest bevat precies één record (de sidecar zelf) — de
        # hoofdmedia is nooit geregistreerd, en de persisted FK is None, niet
        # dangling.
        schema = json.loads(config.manifest_path.read_text())
        assert len(schema["assets"]) == 1
        assert schema["assets"][0]["asset_id"] == sidecar_asset.checksum
        assert schema["assets"][0]["sidecar_of_asset_id"] is None
        assert schema["assets"][0]["relationship_evidence"] == "umid_match"

    def test_sidecar_itself_failing_does_not_change_its_correctly_gated_sidecar_of_asset_id(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """The sidecar's OWN outcome stays fully independent (pre-existing
        Fase 5.2 invariant) — this just confirms the new gating logic isn't
        accidentally coupled to the sidecar's own copy/verification result."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)
        config = _make_config(tmp_path)
        real_storage = LocalFilesystemStorage()

        class _FailsVerificationForTheSidecar(Storage):
            def list_files(self, root):
                return real_storage.list_files(root)

            def exists(self, path):
                return real_storage.exists(path)

            def copy(self, source, destination):
                real_storage.copy(source, destination)

            def checksum(self, path):
                path = Path(path)
                if path.name == "611_4921M01.XML" and config.storage_root in path.parents:
                    return "corrupted-destination-checksum"
                return real_storage.checksum(path)

            def free_bytes(self, path):
                return real_storage.free_bytes(path)

            def remove(self, path):
                real_storage.remove(path)

        service = _make_service(config, camera_profiles, storage=_FailsVerificationForTheSidecar())
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.COPIED
        assert sidecar_asset.outcome == AssetOutcome.FAILED_VERIFICATION
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert sidecar_asset.sidecar_of_asset_id == main_asset.checksum

    def test_sidecar_of_asset_id_is_correct_even_when_the_sidecar_is_scanned_before_its_main_media(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """The exact scenario named in the Fase 5.3.1A brief: the sidecar
        appears BEFORE its main media in the raw scan list. run()'s stable
        sidecar-last partition must still process the main media first."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)

        class _ForcedOrderStorage(LocalFilesystemStorage):
            """Deterministically puts the named file first in list_files(),
            regardless of the real filesystem's own rglob order — so this
            test proves the fix, not just a lucky filesystem ordering."""

            def __init__(self, first_filename: str) -> None:
                super().__init__()
                self._first_filename = first_filename

            def list_files(self, root):
                files = list(super().list_files(root))
                files.sort(key=lambda p: 0 if p.name == self._first_filename else 1)
                return files

        config = _make_config(tmp_path)
        storage = _ForcedOrderStorage("611_4921M01.XML")
        service = _make_service(config, camera_profiles, storage=storage)

        # Bewijs dat de sidecar daadwerkelijk eerst in de ruwe scanlijst staat —
        # anders test dit scenario niets.
        scan_result = service.scan(input_dir)
        assert scan_result.files[0].name == "611_4921M01.XML"

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)
        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.COPIED
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert sidecar_asset.sidecar_of_asset_id == main_asset.checksum

        # Niet alleen de in-memory AssetResult — ook het gepersisteerde
        # sidecar-record moet naar een daadwerkelijk aanwezig hoofdmedia-
        # record verwijzen, ondanks dat de sidecar als eerste in de ruwe
        # scanlijst stond.
        schema = json.loads(config.manifest_path.read_text())
        assert len(schema["assets"]) == 2
        asset_ids = {a["asset_id"] for a in schema["assets"]}
        sidecar_record = next(
            a for a in schema["assets"] if a["asset_id"] == sidecar_asset.checksum
        )
        assert sidecar_record["sidecar_of_asset_id"] == main_asset.checksum
        assert sidecar_record["sidecar_of_asset_id"] in asset_ids
        assert sidecar_record["relationship_evidence"] == "umid_match"

    def test_main_media_manifest_write_failure_aborts_before_any_sidecar_is_touched(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """Hoofdvideo wordt wel gekopieerd, maar manifestregistratie mislukt
        (ENOSPC) — de hele run breekt af (bestaand gedrag, ongewijzigd) vóórdat
        de sidecar ooit aan de beurt komt, dankzij de sidecar-last-herordening.
        Er kan dus geen enkel sidecar-record ontstaan, dangling of anders."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)
        config = _make_config(tmp_path)

        def _failing_register(self, record):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(JSONManifest, "register", _failing_register)
        service = _make_service(config, camera_profiles)

        with pytest.raises(DestinationFullError):
            service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        # Niets is ooit geregistreerd — de run brak af bij de hoofdmedia, de
        # allereerste in de herordende lijst.
        assert not config.manifest_path.exists()

    def test_cancellation_between_main_media_and_sidecar_leaves_no_dangling_relation(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """Annulering (gemodelleerd als een uitzondering uit progress_callback,
        exact zoals ingest_worker.py's SIGTERM-afhandeling) tussen de
        hoofdmedia en zijn sidecar: dankzij de sidecar-last-herordening is het
        eerste verwerkte bestand altijd de hoofdmedia, nooit de sidecar — een
        annulering na bestand 1 raakt de sidecar dus nooit."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        class _Cancelled(Exception):
            pass

        def _progress_callback(update):
            if update.processed == 1:
                raise _Cancelled()

        with pytest.raises(_Cancelled):
            service.run(
                input_dir,
                client="Nike",
                project="Zomer",
                dry_run=False,
                progress_callback=_progress_callback,
            )

        # De hoofdmedia is wél volledig verwerkt en geregistreerd vóór de
        # annulering...
        schema = json.loads(config.manifest_path.read_text())
        assert len(schema["assets"]) == 1
        assert schema["assets"][0]["original_path"] == str(input_dir / "611_4921.MXF")
        # ...de sidecar is nooit bereikt — geen enkel record voor hem, dangling
        # of anders.
        assert all(
            Path(a["original_path"]).name != "611_4921M01.XML" for a in schema["assets"]
        )

    def test_old_manifest_record_shape_still_correctly_backs_a_sidecar_relation(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """Oude manifests blijven leesbaar: een pre-Fase-5.0-record (zonder elk
        van de latere additieve velden) volstaat volledig als grondwaarheid
        voor de nieuwe is_duplicate()-gate — er wordt niets gemigreerd of
        herschreven."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        config = _make_config(tmp_path)
        config.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        main_content = b"fake mxf bytes"
        main_checksum = hashlib.sha256(main_content).hexdigest()
        config.manifest_path.write_text(
            json.dumps(
                {
                    "assets": [
                        {
                            "asset_id": main_checksum,
                            "client_id": "Nike",
                            "project_id": "Zomer",
                            "ingest_run_id": "old-run",
                            "operator": "iemand",
                            "source_machine": "oude-machine",
                            "original_path": "/oud/pad/611_4921.MXF",
                            "destination_path": str(
                                config.storage_root
                                / "Klanten"
                                / "Nike"
                                / "Zomer"
                                / "2026-01-01_Raw"
                                / "Camera"
                                / "611_4921.MXF"
                            ),
                            "category": "Camera",
                            "camera_profile": "Sony FX6",
                            "confidence": "hoog",
                            "ingested_at": "2026-01-01T00:00:00+00:00",
                            # Bewust geen Fase 5.0+/5.1/5.2-velden.
                        }
                    ]
                }
            )
        )

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        # _write_umid_matched_pair schrijft precies dezelfde inhoud (b"fake
        # mxf bytes") als main_content hierboven, plus de bijpassende sidecar.
        _write_umid_matched_pair(input_dir)

        service = _make_service(config, camera_profiles)
        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=False)

        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.DUPLICATE_SKIPPED
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert sidecar_asset.sidecar_of_asset_id == main_checksum

        # Niet alleen de in-memory AssetResult — het gepersisteerde sidecar-
        # record moet verwijzen naar het asset_id van het OUDE, al bestaande
        # record (nooit gemigreerd of herschreven, zie de testdocstring).
        schema = json.loads(config.manifest_path.read_text())
        assert len(schema["assets"]) == 2  # het oude record + de nieuwe sidecar
        asset_ids = {a["asset_id"] for a in schema["assets"]}
        assert main_checksum in asset_ids  # het oude record is ongewijzigd aanwezig
        sidecar_record = next(
            a for a in schema["assets"] if a["asset_id"] == sidecar_asset.checksum
        )
        assert sidecar_record["sidecar_of_asset_id"] == main_checksum
        assert sidecar_record["sidecar_of_asset_id"] in asset_ids
        assert sidecar_record["relationship_evidence"] == "umid_match"

    def test_dry_run_never_suggests_a_manifest_relation_for_a_not_yet_copied_main_media(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """Expliciete dry-run-semantiek: een preview registreert niets, dus
        sidecar_of_asset_id mag nooit een manifestrelatie suggereren die
        alleen zou ontstaan ALS de hoofdvideo daadwerkelijk gekopieerd werd.
        relationship_evidence (de bewezen bronrelatie) blijft wel gewoon
        zichtbaar — dat is altijd al waar, los van wat een run doet."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        _write_umid_matched_pair(input_dir)
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)
        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.PREVIEW
        assert main_asset.is_duplicate is False
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert sidecar_asset.sidecar_of_asset_id is None
        assert not config.manifest_path.exists()  # een dry-run registreert nooit iets

    def test_dry_run_still_truthfully_reports_a_genuinely_pre_existing_manifest_relation(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """Het spiegelbeeld van de vorige test: als de hoofdvideo al ECHT (uit
        een eerdere, niet-dry-run) in het manifest staat, mag een preview die
        waarheid gewoon laten zien — dat is geen fictieve suggestie, want de
        relatie bestaat al."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        run1_dir = tmp_path / "run1"
        run1_dir.mkdir()
        (run1_dir / "611_4921.MXF").write_bytes(b"fake mxf bytes")
        service.run(run1_dir, client="Nike", project="Zomer", dry_run=False)

        run2_dir = tmp_path / "run2"
        run2_dir.mkdir()
        _write_umid_matched_pair(run2_dir)
        report = service.run(run2_dir, client="Nike", project="Zomer", dry_run=True)

        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]

        assert main_asset.outcome == AssetOutcome.PREVIEW
        assert main_asset.is_duplicate is True
        assert sidecar_asset.relationship_evidence == RelationshipEvidence.UMID_MATCH
        assert sidecar_asset.sidecar_of_asset_id == main_asset.checksum
        # Nog steeds niets nieuws geschreven door de preview zelf.
        schema = json.loads(config.manifest_path.read_text())
        assert len(schema["assets"]) == 1  # alleen run1's registratie

    def test_dry_run_and_real_ingest_use_the_same_gating_rule(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """Dry-run en real ingest delen exact hetzelfde codepad voor de
        is_duplicate()-gate (beide zijn read-only aanroepen) — het enige
        verschil is WAT er al écht in het manifest staat op het moment van
        aanroepen, niet een apart dry_run-specifiek vertakking in de
        gating-logica zelf."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        run1_dir = tmp_path / "run1"
        run1_dir.mkdir()
        (run1_dir / "611_4921.MXF").write_bytes(b"fake mxf bytes")
        service.run(run1_dir, client="Nike", project="Zomer", dry_run=False)

        preview_dir = tmp_path / "preview"
        preview_dir.mkdir()
        _write_umid_matched_pair(preview_dir)
        preview_report = service.run(preview_dir, client="Nike", project="Zomer", dry_run=True)

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        _write_umid_matched_pair(real_dir)
        real_report = service.run(real_dir, client="Nike", project="Zomer", dry_run=False)

        preview_sidecar = next(
            a for a in preview_report.assets if a.source_path.name == "611_4921M01.XML"
        )
        real_sidecar = next(
            a for a in real_report.assets if a.source_path.name == "611_4921M01.XML"
        )
        assert preview_sidecar.sidecar_of_asset_id == real_sidecar.sidecar_of_asset_id

    def test_mixed_scan_processes_main_media_before_sidecars_with_correct_progress_and_byte_totals(
        self, tmp_path, camera_profiles, monkeypatch
    ):
        """End-to-end regression over a realistic mixed scan: at least two
        ordinary media files plus one proven sidecar pair. Confirms the
        stable sidecar-last partition (run()) doesn't regress progress
        reporting, byte totals, or processing counts — not just the
        isolated two-file sidecar scenarios covered elsewhere in this
        class."""
        monkeypatch.setattr(
            ingest_service_module, "safe_probe", _fake_safe_probe_with_umid(_REAL_UMID)
        )
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        (input_dir / "clip_a.mp4").write_bytes(b"1" * 100)
        _write_umid_matched_pair(input_dir)  # 611_4921.MXF + 611_4921M01.XML
        (input_dir / "clip_z.mp4").write_bytes(b"2" * 25)

        config = _make_config(tmp_path)
        service = _make_service(config, camera_profiles)

        # Bewijs dat de sidecar niet toevallig al als laatste in de ruwe
        # scanlijst staat — anders zou deze test de herordening niet
        # daadwerkelijk uitoefenen.
        scan_result = service.scan(input_dir)
        sidecar_index = next(
            i for i, p in enumerate(scan_result.files) if p.name == "611_4921M01.XML"
        )
        assert sidecar_index < len(scan_result.files) - 1

        updates: list[ProgressUpdate] = []
        processed_names: list[str] = []
        report = service.run(
            input_dir,
            client="Nike",
            project="Zomer",
            dry_run=False,
            progress_callback=updates.append,
            asset_callback=lambda asset: processed_names.append(asset.source_path.name),
        )

        # Hoofdmedia (en elk ander gewoon bestand) wordt vóór de sidecar verwerkt.
        assert processed_names[-1] == "611_4921M01.XML"
        assert set(processed_names[:-1]) == {"clip_a.mp4", "611_4921.MXF", "clip_z.mp4"}

        # Geen bestand wordt dubbel verwerkt.
        assert len(processed_names) == len(set(processed_names)) == 4

        # Progress-callbacks rapporteren exact 1 t/m N; total blijft constant.
        assert [u.processed for u in updates] == [1, 2, 3, 4]
        assert all(u.total == 4 for u in updates)

        # bytes_processed cumulatief correct, en het eindrapport komt overeen —
        # los van de verwerkingsvolgorde (optelling is orde-onafhankelijk).
        expected_total_bytes = sum(p.stat().st_size for p in input_dir.iterdir())
        assert updates[-1].bytes_processed == expected_total_bytes
        assert report.total_bytes == expected_total_bytes
        assert len(report.assets) == 4

        by_name = {a.source_path.name: a for a in report.assets}
        main_asset = by_name["611_4921.MXF"]
        sidecar_asset = by_name["611_4921M01.XML"]
        assert main_asset.outcome == AssetOutcome.COPIED
        assert sidecar_asset.outcome == AssetOutcome.COPIED
        assert sidecar_asset.sidecar_of_asset_id == main_asset.checksum

        # Ook het gepersisteerde manifest bevat precies vier records, één per
        # bestand — geen dubbele registratie.
        schema = json.loads(config.manifest_path.read_text())
        assert len(schema["assets"]) == 4
        sidecar_record = next(
            a for a in schema["assets"] if a["asset_id"] == sidecar_asset.checksum
        )
        assert sidecar_record["sidecar_of_asset_id"] == main_asset.checksum

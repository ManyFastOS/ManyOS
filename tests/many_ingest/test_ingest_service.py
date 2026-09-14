"""Tests for IngestService — the core orchestration pipeline.

Uses the real LocalFilesystemStorage/JSONManifest adapters against tmp_path for most
cases (an integration-style test of the whole ports & adapters wiring), plus one
fake Storage to force a checksum mismatch and exercise the verification-failure path.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage
from many_ingest.config import IngestConfig
from many_ingest.core.ingest_service import (
    AssetOutcome,
    DestinationUnavailableError,
    IngestService,
    ProgressUpdate,
)
from many_ingest.core.report import summarize
from many_ingest.metadata_extractor import FfprobeNotFoundError
from many_ingest.ports.storage import Storage


def _make_config(tmp_path: Path) -> IngestConfig:
    return IngestConfig(
        storage_root=tmp_path / "storage",
        manifest_path=tmp_path / "asset_schema.json",
        log_dir=tmp_path / "logs",
    )


def _workspace_dir(config: IngestConfig, client: str, project: str, category: str) -> Path:
    """Mirrors _build_workspace_path's layout for tests that need to pre-place a
    colliding file at the exact path the pipeline will compute today."""
    today = dt.date.today().isoformat()
    return config.storage_root / "Klanten" / client / project / f"{today}_Raw" / category


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
        destination_dir = _workspace_dir(config, "Nike", "Zomer", "Drone")
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

        destination_dir = _workspace_dir(config, "Nike", "Zomer", "Drone")
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

        destination_dir = _workspace_dir(config, "Nike", "Zomer", "Drone")
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

        destination_dir = _workspace_dir(config, "Nike", "Zomer", "Drone")
        destination_dir.mkdir(parents=True)
        (destination_dir / "DJI_0001.MP4").write_bytes(b"different content")

        report = service.run(input_dir, client="Nike", project="Zomer", dry_run=True)

        asset = report.assets[0]
        assert asset.outcome == AssetOutcome.PREVIEW
        assert asset.name_conflict_resolved is True
        assert asset.destination_path.name == "DJI_0001_001.MP4"
        # dry-run: er is niets nieuws op de schijf geschreven
        assert not asset.destination_path.exists()

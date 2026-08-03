"""Tests for IngestService — the core orchestration pipeline.

Uses the real LocalFilesystemStorage/JSONManifest adapters against tmp_path for most
cases (an integration-style test of the whole ports & adapters wiring), plus one
fake Storage to force a checksum mismatch and exercise the verification-failure path.
"""

from __future__ import annotations

import json
from pathlib import Path

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage
from many_ingest.config import IngestConfig
from many_ingest.core.ingest_service import AssetOutcome, IngestService
from many_ingest.ports.storage import Storage


def _make_config(tmp_path: Path) -> IngestConfig:
    return IngestConfig(
        storage_root=tmp_path / "storage",
        manifest_path=tmp_path / "asset_schema.json",
        log_dir=tmp_path / "logs",
    )


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
    assert report.log_path.exists()  # actielog wordt wel altijd geschreven


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
    assert not config.manifest_path.exists()

"""Tests for the JSON-based ManyFast Asset Schema store (v0.1)."""

from __future__ import annotations

from pathlib import Path

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.ports.manifest import AssetRecord


def _record(checksum: str) -> AssetRecord:
    return AssetRecord(
        asset_id=checksum,
        client_id="Nike",
        project_id="Zomer",
        ingest_run_id="run-1",
        operator="tester",
        source_machine="test-machine",
        original_path=Path("/in/clip.mp4"),
        destination_path=Path("/out/clip.mp4"),
        category="Drone",
        camera_profile="DJI",
        confidence="hoog",
        ingested_at="2026-08-03T00:00:00+00:00",
    )


def test_is_duplicate_false_when_manifest_does_not_exist_yet(tmp_path):
    manifest = JSONManifest(tmp_path / "asset_schema.json")
    assert manifest.is_duplicate("abc123") is False


def test_register_then_is_duplicate(tmp_path):
    manifest_path = tmp_path / "asset_schema.json"
    manifest = JSONManifest(manifest_path)

    manifest.register(_record("abc123"))

    assert manifest.is_duplicate("abc123") is True
    assert manifest.is_duplicate("other") is False
    assert manifest_path.exists()


def test_register_appends_across_instances(tmp_path):
    manifest_path = tmp_path / "asset_schema.json"
    JSONManifest(manifest_path).register(_record("first"))
    JSONManifest(manifest_path).register(_record("second"))

    reloaded = JSONManifest(manifest_path)
    assert reloaded.is_duplicate("first") is True
    assert reloaded.is_duplicate("second") is True

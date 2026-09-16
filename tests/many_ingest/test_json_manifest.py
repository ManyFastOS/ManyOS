"""Tests for the JSON-based ManyFast Asset Schema store (v0.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.ports.manifest import AssetRecord


def _record(
    checksum: str,
    media_type: str = "unknown",
    manufacturer: str | None = None,
    model: str | None = None,
    source_relative_path: Path | None = None,
    classification_source: str = "no_signal_matched",
    codec: str | None = None,
    width: int | None = None,
    height: int | None = None,
    frame_rate: str | None = None,
    duration_seconds: float | None = None,
    has_video_stream: bool | None = None,
    has_audio_stream: bool | None = None,
) -> AssetRecord:
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
        media_type=media_type,
        manufacturer=manufacturer,
        model=model,
        source_relative_path=source_relative_path,
        classification_source=classification_source,
        codec=codec,
        width=width,
        height=height,
        frame_rate=frame_rate,
        duration_seconds=duration_seconds,
        has_video_stream=has_video_stream,
        has_audio_stream=has_audio_stream,
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


def test_a_failed_write_never_corrupts_the_existing_manifest(tmp_path, monkeypatch):
    """Regression test (2026-09-15): register() used to write via a plain
    `write_text()`, which truncates the file the instant it opens — a write
    failure partway through (e.g. the destination filled up) could silently
    lose every previously-registered asset. register() now writes to a
    `.tmp` file and only replaces the real manifest once that succeeded, so
    a failed write must leave the original, valid manifest byte-for-byte
    unchanged and still raise the original error."""
    manifest_path = tmp_path / "asset_schema.json"
    manifest = JSONManifest(manifest_path)
    manifest.register(_record("already-safe"))
    original_content = manifest_path.read_text()

    def _broken_write_text(self, text, *args, **kwargs):
        raise OSError("disk vol (gesimuleerd)")

    monkeypatch.setattr(Path, "write_text", _broken_write_text)

    with pytest.raises(OSError, match="disk vol"):
        manifest.register(_record("never-makes-it-in"))

    assert manifest_path.read_text() == original_content
    assert not (tmp_path / "asset_schema.json.tmp").exists()  # opgeruimd, niet blijven slingeren

    reloaded = JSONManifest(manifest_path)
    assert reloaded.is_duplicate("already-safe") is True
    assert reloaded.is_duplicate("never-makes-it-in") is False


# -- Fase 5.0: additive fields + backward compatibility ---------------------


def test_register_persists_the_new_fase_5_0_fields(tmp_path):
    manifest_path = tmp_path / "asset_schema.json"
    manifest = JSONManifest(manifest_path)

    manifest.register(
        _record(
            "abc123",
            media_type="video",
            manufacturer="Sony",
            model="FX6",
            source_relative_path=Path("PRIVATE/M4ROOT/CLIP/C0001.MXF"),
        )
    )

    schema = json.loads(manifest_path.read_text())
    asset = schema["assets"][0]
    assert asset["media_type"] == "video"
    assert asset["manufacturer"] == "Sony"
    assert asset["model"] == "FX6"
    assert asset["source_relative_path"] == "PRIVATE/M4ROOT/CLIP/C0001.MXF"


def test_register_persists_null_manufacturer_model_and_source_relative_path_when_unknown(
    tmp_path,
):
    manifest_path = tmp_path / "asset_schema.json"
    manifest = JSONManifest(manifest_path)

    manifest.register(_record("abc123"))  # defaults: manufacturer/model/source_relative_path=None

    schema = json.loads(manifest_path.read_text())
    asset = schema["assets"][0]
    assert asset["media_type"] == "unknown"
    assert asset["manufacturer"] is None
    assert asset["model"] is None
    assert asset["source_relative_path"] is None


def test_an_old_shaped_manifest_without_fase_5_0_keys_still_supports_dedupe(tmp_path):
    """A manifest written before Fase 5.0 has entries with no media_type/
    manufacturer/model/source_relative_path keys at all — no migration is
    performed in Fase 5.0, so is_duplicate() must keep working against a
    manifest file exactly like that, unchanged."""
    manifest_path = tmp_path / "asset_schema.json"
    old_shaped = {
        "assets": [
            {
                "asset_id": "pre-fase-5-0-checksum",
                "client_id": "Nike",
                "project_id": "Zomer",
                "ingest_run_id": "run-0",
                "operator": "tester",
                "source_machine": "test-machine",
                "original_path": "/in/clip.mp4",
                "destination_path": "/out/clip.mp4",
                "category": "Drone",
                "camera_profile": "DJI",
                "confidence": "hoog",
                "ingested_at": "2026-08-03T00:00:00+00:00",
                # geen media_type/manufacturer/model/source_relative_path
            }
        ]
    }
    manifest_path.write_text(json.dumps(old_shaped))

    manifest = JSONManifest(manifest_path)
    assert manifest.is_duplicate("pre-fase-5-0-checksum") is True
    assert manifest.is_duplicate("something-else") is False

    # Nieuwe registraties naast oude, kale records blijven werken.
    manifest.register(_record("new-checksum", media_type="video"))
    assert manifest.is_duplicate("new-checksum") is True
    assert manifest.is_duplicate("pre-fase-5-0-checksum") is True


# -- Fase 5.1: classification_source + technical metadata -------------------


def test_register_persists_the_new_fase_5_1_fields(tmp_path):
    manifest_path = tmp_path / "asset_schema.json"
    manifest = JSONManifest(manifest_path)

    manifest.register(
        _record(
            "abc123",
            classification_source="metadata_make_or_model",
            codec="prores",
            width=3840,
            height=2160,
            frame_rate="30000/1001",
            duration_seconds=125.371800,
            has_video_stream=True,
            has_audio_stream=False,
        )
    )

    schema = json.loads(manifest_path.read_text())
    asset = schema["assets"][0]
    # classification_source is een stabiele string, nooit de Python
    # enum-representatie (bv. niet "ClassificationSource.METADATA_MAKE_OR_MODEL").
    assert asset["classification_source"] == "metadata_make_or_model"
    assert asset["codec"] == "prores"
    assert asset["width"] == 3840
    assert asset["height"] == 2160
    # De exacte rationale framerate-string, nooit afgerond/omgezet naar float.
    assert asset["frame_rate"] == "30000/1001"
    assert asset["duration_seconds"] == 125.371800
    assert asset["has_video_stream"] is True
    assert asset["has_audio_stream"] is False


def test_register_persists_none_technical_metadata_when_probe_was_unavailable(tmp_path):
    manifest_path = tmp_path / "asset_schema.json"
    manifest = JSONManifest(manifest_path)

    manifest.register(_record("abc123"))  # defaults: alle Fase 5.1-velden None/no_signal_matched

    schema = json.loads(manifest_path.read_text())
    asset = schema["assets"][0]
    assert asset["classification_source"] == "no_signal_matched"
    assert asset["codec"] is None
    assert asset["width"] is None
    assert asset["height"] is None
    assert asset["frame_rate"] is None
    assert asset["duration_seconds"] is None
    # None (geen probe), niet False (probe draaide en vond geen stream).
    assert asset["has_video_stream"] is None
    assert asset["has_audio_stream"] is None


def test_an_old_shaped_manifest_without_fase_5_1_keys_still_supports_dedupe(tmp_path):
    """Same guarantee as the Fase 5.0 test above, one phase further: a
    manifest written before Fase 5.1 (with the Fase 5.0 keys, but without
    classification_source/codec/width/height/frame_rate/duration_seconds/
    has_video_stream/has_audio_stream) must still work, unmigrated."""
    manifest_path = tmp_path / "asset_schema.json"
    old_shaped = {
        "assets": [
            {
                "asset_id": "pre-fase-5-1-checksum",
                "client_id": "Nike",
                "project_id": "Zomer",
                "ingest_run_id": "run-0",
                "operator": "tester",
                "source_machine": "test-machine",
                "original_path": "/in/clip.mp4",
                "destination_path": "/out/clip.mp4",
                "category": "Drone",
                "camera_profile": "DJI",
                "confidence": "hoog",
                "ingested_at": "2026-08-03T00:00:00+00:00",
                "media_type": "video",
                "manufacturer": "DJI",
                "model": None,
                "source_relative_path": "DJI_0001.MP4",
                # geen classification_source/codec/width/height/frame_rate/
                # duration_seconds/has_video_stream/has_audio_stream
            }
        ]
    }
    manifest_path.write_text(json.dumps(old_shaped))

    manifest = JSONManifest(manifest_path)
    assert manifest.is_duplicate("pre-fase-5-1-checksum") is True
    assert manifest.is_duplicate("something-else") is False

    manifest.register(_record("new-checksum", classification_source="container_metadata"))
    assert manifest.is_duplicate("new-checksum") is True
    assert manifest.is_duplicate("pre-fase-5-1-checksum") is True

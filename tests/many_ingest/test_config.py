"""Tests for config loading and validation."""

from __future__ import annotations

import pytest

from many_ingest.config import load_camera_profiles, load_ingest_config


def test_load_ingest_config_reads_expected_fields(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "storage_root: /tmp/storage\nmanifest_path: /tmp/schema.json\nlog_dir: /tmp/logs\n"
    )
    config = load_ingest_config(config_path)
    assert str(config.storage_root) == "/tmp/storage"
    assert str(config.manifest_path) == "/tmp/schema.json"
    assert str(config.log_dir) == "/tmp/logs"


def test_load_ingest_config_requires_storage_root(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("manifest_path: /tmp/schema.json\n")
    with pytest.raises(ValueError, match="storage_root"):
        load_ingest_config(config_path)


def test_load_ingest_config_fills_in_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("storage_root: /tmp/storage\n")
    config = load_ingest_config(config_path)
    assert config.manifest_path.name == "asset_schema.json"
    assert config.log_dir.name == "logs"


def test_load_camera_profiles_reads_the_real_config_file(camera_profiles):
    ids = {profile.id for profile in camera_profiles}
    assert ids == {"sony_fx6", "sony_a7iv", "sony_fx3", "dji", "gopro", "audio"}


def test_load_camera_profiles_rejects_missing_required_field(tmp_path):
    path = tmp_path / "camera_profiles.yaml"
    path.write_text("profiles:\n  - id: broken\n    label: Broken\n")
    with pytest.raises(ValueError, match="category"):
        load_camera_profiles(path)


def test_load_camera_profiles_rejects_unknown_category(tmp_path):
    path = tmp_path / "camera_profiles.yaml"
    path.write_text("profiles:\n  - id: x\n    label: X\n    category: Spaceship\n")
    with pytest.raises(ValueError, match="Spaceship"):
        load_camera_profiles(path)


def test_container_requires_brand_defaults_to_false(tmp_path):
    path = tmp_path / "camera_profiles.yaml"
    path.write_text(
        "profiles:\n"
        "  - id: x\n"
        "    label: X\n"
        "    category: Camera\n"
        "    metadata_match:\n"
        "      container_contains: [\"mp4\"]\n"
    )
    profiles = load_camera_profiles(path)
    assert profiles[0].container_requires_brand is False


def test_container_requires_brand_loads_when_set_true(tmp_path):
    path = tmp_path / "camera_profiles.yaml"
    path.write_text(
        "profiles:\n"
        "  - id: x\n"
        "    label: X\n"
        "    category: Camera\n"
        "    metadata_match:\n"
        "      container_contains: [\"mp4\"]\n"
        "      container_requires_brand: true\n"
    )
    profiles = load_camera_profiles(path)
    assert profiles[0].container_requires_brand is True


def test_sony_fx3_in_the_real_config_requires_brand_for_its_container_signal(camera_profiles):
    fx3 = next(p for p in camera_profiles if p.id == "sony_fx3")
    assert fx3.container_requires_brand is True


def test_sony_fx6_in_the_real_config_does_not_require_brand_for_its_container_signal(
    camera_profiles,
):
    fx6 = next(p for p in camera_profiles if p.id == "sony_fx6")
    assert fx6.container_requires_brand is False

"""Tests for config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from many_ingest.config import load_camera_profiles, load_storage_layout, resolve_ingest_config


def test_load_storage_layout_reads_expected_fields(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: Footage\nmanifest_subpath: Schema/schema.json\nlog_subpath: Logs\n"
    )
    layout = load_storage_layout(config_path)
    assert layout.footage_subpath == Path("Footage")
    assert layout.manifest_subpath == Path("Schema/schema.json")
    assert layout.log_subpath == Path("Logs")


def test_load_storage_layout_fills_in_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("")  # leeg bestand — alle drie krijgen de standaardwaarde
    layout = load_storage_layout(config_path)
    assert layout.footage_subpath == Path("ManyFast/Footage")
    assert layout.manifest_subpath.name == "asset_schema.json"
    assert layout.log_subpath.name == "Logs"


def test_load_storage_layout_rejects_an_absolute_path(tmp_path):
    """Fase 3.5: config.yaml never names a physical disk anymore. An
    absolute value (a leftover from the old format, or someone hardcoding a
    disk again) is a clear, actionable error, not a silent misinterpretation."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text('footage_subpath: "/Volumes/Extreme SSD/ManyFast/Footage"\n')
    with pytest.raises(ValueError, match="relatief"):
        load_storage_layout(config_path)


def test_load_storage_layout_rejects_a_tilde_path(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text('log_subpath: "~/ManyFast/Logs"\n')
    with pytest.raises(ValueError, match="relatief"):
        load_storage_layout(config_path)


@pytest.mark.parametrize("key", ["footage_subpath", "manifest_subpath", "log_subpath"])
def test_load_storage_layout_rejects_a_path_traversal_attempt(tmp_path, key):
    """A relative subpath must never be able to resolve outside the chosen
    destination_root — see config.py's _relative_subpath(). Covers all three
    subpaths independently, not just footage_subpath."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f'{key}: "../../outside"\n')
    with pytest.raises(ValueError, match=r"\.\."):
        load_storage_layout(config_path)


@pytest.mark.parametrize("key", ["footage_subpath", "manifest_subpath", "log_subpath"])
def test_load_storage_layout_rejects_a_traversal_attempt_nested_inside_a_longer_path(
    tmp_path, key
):
    """Not just a leading '../..' — a '..' segment anywhere in the path must
    be rejected, e.g. 'ManyFast/../../outside'."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f'{key}: "ManyFast/../../outside"\n')
    with pytest.raises(ValueError, match=r"\.\."):
        load_storage_layout(config_path)


def test_load_storage_layout_accepts_normal_nested_relative_paths(tmp_path):
    """Regular nested relative paths (no '..') must keep working — the
    traversal check must not be overly broad."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: ManyFast/Footage\n"
        "manifest_subpath: ManyFast/ManyOS/AssetSchema/asset_schema.json\n"
        "log_subpath: ManyFast/ManyOS/Logs\n"
    )
    layout = load_storage_layout(config_path)
    assert layout.footage_subpath == Path("ManyFast/Footage")
    assert layout.manifest_subpath == Path("ManyFast/ManyOS/AssetSchema/asset_schema.json")
    assert layout.log_subpath == Path("ManyFast/ManyOS/Logs")


def test_resolve_ingest_config_joins_layout_with_a_chosen_destination_root(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: Footage\nmanifest_subpath: Schema/schema.json\nlog_subpath: Logs\n"
    )
    layout = load_storage_layout(config_path)
    destination_root = tmp_path / "SomeDisk"

    resolved = resolve_ingest_config(layout, destination_root)

    assert resolved.storage_root == destination_root / "Footage"
    assert resolved.manifest_path == destination_root / "Schema" / "schema.json"
    assert resolved.log_dir == destination_root / "Logs"


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


# -- Fase 5.0: manufacturer/model vocabulary --------------------------------


def test_camera_profile_manufacturer_and_model_default_to_none_when_absent(tmp_path):
    """A camera_profiles.yaml written before Fase 5.0 (no manufacturer/model
    keys at all) must keep loading without error — these two fields are
    purely additive."""
    path = tmp_path / "camera_profiles.yaml"
    path.write_text("profiles:\n  - id: x\n    label: X\n    category: Camera\n")
    profiles = load_camera_profiles(path)
    assert profiles[0].manufacturer is None
    assert profiles[0].model is None


def test_camera_profile_manufacturer_and_model_load_when_present(tmp_path):
    path = tmp_path / "camera_profiles.yaml"
    path.write_text(
        "profiles:\n"
        "  - id: x\n"
        "    label: X\n"
        "    category: Camera\n"
        "    manufacturer: Sony\n"
        "    model: FX6\n"
    )
    profiles = load_camera_profiles(path)
    assert profiles[0].manufacturer == "Sony"
    assert profiles[0].model == "FX6"


def test_sony_fx6_and_fx3_in_the_real_config_have_manufacturer_and_model(camera_profiles):
    fx6 = next(p for p in camera_profiles if p.id == "sony_fx6")
    fx3 = next(p for p in camera_profiles if p.id == "sony_fx3")
    assert (fx6.manufacturer, fx6.model) == ("Sony", "FX6")
    assert (fx3.manufacturer, fx3.model) == ("Sony", "FX3")


def test_dji_in_the_real_config_has_a_manufacturer_but_no_specific_model(camera_profiles):
    """DJI's profile matches generically on the make tag, not one specific
    drone model — model stays None rather than a guessed/invented value."""
    dji = next(p for p in camera_profiles if p.id == "dji")
    assert dji.manufacturer == "DJI"
    assert dji.model is None

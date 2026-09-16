"""Loads and validates Many Ingest's configuration: storage layout and camera profiles.

Kept deliberately dumb: plain dict access with clear errors, no schema-validation
library. The config files are small and hand-edited (VISION.md: Simplicity Wins).

As of Fase 3.5 (Dynamic Destination Selection), config.yaml holds only the
RELATIVE storage layout — the Footage/ManyOS/AssetSchema/Logs convention
documented in docs/MANY_INGEST_STORAGE_LAYOUT.md — never a specific physical
disk. ManyFast uses several external destination disks, not one fixed one,
so which physical disk (`destination_root`) is used is chosen per ingest, at
runtime (the GUI's destination picker, or the CLI's required `--destination`),
and only then resolved against this layout via `resolve_ingest_config()`.
`storage_root`/`manifest_path`/`log_dir` as absolute, hardcoded `/Volumes/...`
paths in config.yaml are no longer supported — `load_storage_layout()`
rejects an absolute value with a clear error rather than silently
misinterpreting a leftover old-format config. It also rejects any `..`
path segment in these three subpaths — a relative path must never be able
to resolve outside the chosen `destination_root` (see `_relative_subpath()`).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

VALID_CATEGORIES = {"Camera", "Drone", "Audio"}

# De bestaande, ongewijzigde Project Workspace-conventie (zie
# docs/MANY_INGEST_STORAGE_LAYOUT.md) — relatief t.o.v. een pas op het
# moment van een ingest gekozen destination_root, nooit een vast schijfpad.
DEFAULT_FOOTAGE_SUBPATH = Path("ManyFast/Footage")
DEFAULT_MANIFEST_SUBPATH = Path("ManyFast/ManyOS/AssetSchema/asset_schema.json")
DEFAULT_LOG_SUBPATH = Path("ManyFast/ManyOS/Logs")


@dataclasses.dataclass(frozen=True)
class StorageLayout:
    """The static, disk-independent part of where things go under whichever
    destination_root is chosen at runtime — never an absolute path itself."""

    footage_subpath: Path
    manifest_subpath: Path
    log_subpath: Path


@dataclasses.dataclass(frozen=True)
class IngestConfig:
    """Unchanged in shape since before Fase 3.5 — IngestService/ActionLogger/
    adapters still just receive one of these, with absolute, resolved paths.
    Only how it gets built changed (see `resolve_ingest_config()`), not what
    it is."""

    storage_root: Path
    manifest_path: Path
    log_dir: Path


@dataclasses.dataclass(frozen=True)
class CameraProfile:
    id: str
    label: str
    category: str
    filename_patterns: list[str]
    confirmed_filename_patterns: list[str]
    metadata_make_contains: list[str]
    metadata_model_contains: list[str]
    metadata_brand_contains: list[str]
    metadata_container_contains: list[str]
    audio_only: bool = False
    container_requires_brand: bool = False
    # Fase 5.0 — additief, nooit gebruikt door classify()'s matching-logica zelf
    # (die blijft ongewijzigd op filename/metadata-patronen). Puur beschrijvende
    # data, voor het eerst gebruikt door ClassificationResult.manufacturer/model
    # (zie classification/camera_profiles.py). Nullable: een profiel zonder
    # specifiek model (bv. "DJI", "Audio") laat `model` gewoon None; een oude
    # camera_profiles.yaml zonder deze sleutels blijft laden (zie
    # load_camera_profiles()'s .get()-defaults hieronder).
    manufacturer: str | None = None
    model: str | None = None


def load_storage_layout(path: Path) -> StorageLayout:
    data = yaml.safe_load(path.read_text()) or {}
    return StorageLayout(
        footage_subpath=_relative_subpath(data, "footage_subpath", DEFAULT_FOOTAGE_SUBPATH, path),
        manifest_subpath=_relative_subpath(
            data, "manifest_subpath", DEFAULT_MANIFEST_SUBPATH, path
        ),
        log_subpath=_relative_subpath(data, "log_subpath", DEFAULT_LOG_SUBPATH, path),
    )


def _relative_subpath(data: dict, key: str, default: Path, config_path: Path) -> Path:
    raw = data.get(key)
    if raw is None:
        return default
    candidate = Path(raw)
    if candidate.is_absolute() or str(raw).startswith("~"):
        raise ValueError(
            f"{config_path}: '{key}' moet een relatief pad zijn binnen de gekozen "
            f"bestemmingsschijf (bijv. 'ManyFast/Footage'), geen absoluut pad of "
            f"vast schijfpad — de fysieke bestemmingsschijf wordt per ingest gekozen, "
            f"niet in config.yaml vastgelegd: {raw!r}"
        )
    if ".." in candidate.parts:
        raise ValueError(
            f"{config_path}: '{key}' mag geen '..' bevatten — een relatief pad mag "
            f"nooit buiten de gekozen bestemmingsschijf (destination_root) kunnen "
            f"resolven: {raw!r}"
        )
    return candidate


def resolve_ingest_config(layout: StorageLayout, destination_root: Path) -> IngestConfig:
    """Joins the static layout with a runtime-chosen `destination_root` (the
    absolute mount path of the physical disk picked for this one ingest)
    into the same `IngestConfig` shape IngestService has always taken.
    IngestService itself never needs to know a destination was chosen at
    runtime rather than read from a file."""
    destination_root = Path(destination_root)
    return IngestConfig(
        storage_root=destination_root / layout.footage_subpath,
        manifest_path=destination_root / layout.manifest_subpath,
        log_dir=destination_root / layout.log_subpath,
    )


def load_camera_profiles(path: Path) -> list[CameraProfile]:
    data = yaml.safe_load(path.read_text()) or {}
    profiles = []
    for entry in data.get("profiles", []):
        for required_key in ("id", "label", "category"):
            if required_key not in entry:
                raise ValueError(f"{path}: profiel mist verplicht veld '{required_key}': {entry}")

        category = entry["category"]
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"{path}: profiel '{entry['id']}' heeft onbekende category '{category}' "
                f"(verwacht een van {sorted(VALID_CATEGORIES)})"
            )

        metadata_match = entry.get("metadata_match", {})
        profiles.append(
            CameraProfile(
                id=entry["id"],
                label=entry["label"],
                category=category,
                filename_patterns=entry.get("filename_patterns", []),
                confirmed_filename_patterns=entry.get("confirmed_filename_patterns", []),
                metadata_make_contains=metadata_match.get("make_contains", []),
                metadata_model_contains=metadata_match.get("model_contains", []),
                metadata_brand_contains=metadata_match.get("brand_contains", []),
                metadata_container_contains=metadata_match.get("container_contains", []),
                audio_only=entry.get("audio_only", False),
                container_requires_brand=metadata_match.get("container_requires_brand", False),
                manufacturer=entry.get("manufacturer"),
                model=entry.get("model"),
            )
        )
    return profiles

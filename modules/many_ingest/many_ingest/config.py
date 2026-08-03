"""Loads and validates Many Ingest's configuration: storage locations and camera profiles.

Kept deliberately dumb: plain dict access with clear errors, no schema-validation
library. The config files are small and hand-edited (VISION.md: Simplicity Wins).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

VALID_CATEGORIES = {"Camera", "Drone", "Audio"}


@dataclasses.dataclass(frozen=True)
class IngestConfig:
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


def load_ingest_config(path: Path) -> IngestConfig:
    data = yaml.safe_load(path.read_text()) or {}
    try:
        storage_root = data["storage_root"]
    except KeyError as exc:
        raise ValueError(f"{path}: 'storage_root' is verplicht") from exc

    return IngestConfig(
        storage_root=Path(storage_root).expanduser(),
        manifest_path=Path(
            data.get("manifest_path", "~/.many-ingest/asset_schema.json")
        ).expanduser(),
        log_dir=Path(data.get("log_dir", "~/.many-ingest/logs")).expanduser(),
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
            )
        )
    return profiles

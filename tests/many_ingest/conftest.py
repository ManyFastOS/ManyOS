"""Shared fixtures for the Many Ingest test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from many_ingest.config import load_camera_profiles
from many_ingest.metadata_extractor import ProbeResult

CAMERA_PROFILES_PATH = (
    Path(__file__).resolve().parents[2]
    / "modules"
    / "many_ingest"
    / "config"
    / "camera_profiles.yaml"
)


@pytest.fixture
def camera_profiles():
    return load_camera_profiles(CAMERA_PROFILES_PATH)


@pytest.fixture
def make_probe_result():
    def _make(
        has_video_stream: bool = True,
        has_audio_stream: bool = True,
        make: str | None = None,
        model: str | None = None,
        creation_time: str | None = None,
        major_brand: str | None = None,
        compatible_brands: str | None = None,
        container_format: str | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            has_video_stream=has_video_stream,
            has_audio_stream=has_audio_stream,
            codec=None,
            width=None,
            height=None,
            frame_rate=None,
            duration_seconds=None,
            make=make,
            model=model,
            creation_time=creation_time,
            major_brand=major_brand,
            compatible_brands=compatible_brands,
            container_format=container_format,
        )

    return _make

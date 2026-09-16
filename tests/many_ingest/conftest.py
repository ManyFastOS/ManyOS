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
        duration_seconds: float | None = None,
        material_package_umid: str | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            has_video_stream=has_video_stream,
            has_audio_stream=has_audio_stream,
            codec=None,
            width=None,
            height=None,
            frame_rate=None,
            duration_seconds=duration_seconds,
            make=make,
            model=model,
            creation_time=creation_time,
            major_brand=major_brand,
            compatible_brands=compatible_brands,
            container_format=container_format,
            material_package_umid=material_package_umid,
        )

    return _make


@pytest.fixture
def make_sony_sidecar_xml():
    """Fase 5.2 — builds a minimal Sony NonRealTimeMeta XML text, shaped like
    the real footage examined in the Fase 5.2 real-media audit
    (/Volumes/Sharpwaves), with only the handful of elements
    sidecar_relationship.parse_sony_sidecar() actually reads. Any of the
    three can be omitted (None) to exercise the "missing element" cases."""

    def _make(
        umid: str | None = "060A2B340101010501010D43130000006AED1A61791206D210322CFFFE7DA477",
        duration_frames: int | None = 871,
        fps: int | str | None = 25,
        namespace: str = "urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20",
    ) -> str:
        target_material = f'<TargetMaterial umidRef="{umid}"/>' if umid is not None else ""
        duration = f'<Duration value="{duration_frames}"/>' if duration_frames is not None else ""
        ltc = (
            f'<LtcChangeTable tcFps="{fps}" halfStep="false"></LtcChangeTable>'
            if fps is not None
            else ""
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<NonRealTimeMeta xmlns="{namespace}">\n'
            f"  {target_material}\n"
            f"  {duration}\n"
            f"  {ltc}\n"
            "</NonRealTimeMeta>\n"
        )

    return _make

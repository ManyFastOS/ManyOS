"""Applies camera_profiles.yaml rules to classify an asset's probable source.

Rule-based, not Asset Intelligence (see CLAUDE.md) — deterministic pattern/metadata
matching, no AI/ML. Metadata takes priority over filename, because filenames can be
renamed but metadata is factual. No match, or a conflict between multiple profiles,
resolves to "Onbekend" at low confidence — never a silent guess (see
docs/MANY_INGEST_BUILD_PLAN.md, section 4).
"""

from __future__ import annotations

import dataclasses
import enum
import re
from pathlib import Path

from many_ingest.config import CameraProfile
from many_ingest.metadata_extractor import ProbeResult

UNKNOWN_CATEGORY = "Onbekend"
UNKNOWN_PROFILE_LABEL = "Onbekend"


class Confidence(enum.Enum):
    HIGH = "hoog"
    MEDIUM = "middel"
    LOW = "laag"


@dataclasses.dataclass(frozen=True)
class ClassificationResult:
    category: str
    camera_profile: str
    confidence: Confidence


def classify(
    path: Path,
    probe_result: ProbeResult | None,
    profiles: list[CameraProfile],
) -> ClassificationResult:
    metadata_matches = [p for p in profiles if _matches_metadata(probe_result, p)]
    if len(metadata_matches) == 1:
        return _result_for(metadata_matches[0], Confidence.HIGH)
    if len(metadata_matches) > 1:
        return _unknown()  # tegenstrijdige metadata -> te onzeker om te kiezen

    filename_matches = [p for p in profiles if _matches_filename(path, p)]
    if len(filename_matches) == 1:
        return _result_for(filename_matches[0], Confidence.MEDIUM)

    return _unknown()  # geen match, of meerdere profielen matchen dezelfde bestandsnaam


def _matches_filename(path: Path, profile: CameraProfile) -> bool:
    return any(re.match(pattern, path.name) for pattern in profile.filename_patterns)


def _matches_metadata(probe_result: ProbeResult | None, profile: CameraProfile) -> bool:
    if probe_result is None:
        return False

    if profile.audio_only:
        return probe_result.has_audio_stream and not probe_result.has_video_stream

    make = (probe_result.make or "").lower()
    model = (probe_result.model or "").lower()

    make_hit = any(needle.lower() in make for needle in profile.metadata_make_contains)
    model_hit = any(needle.lower() in model for needle in profile.metadata_model_contains)
    return make_hit or model_hit


def _result_for(profile: CameraProfile, confidence: Confidence) -> ClassificationResult:
    return ClassificationResult(
        category=profile.category, camera_profile=profile.label, confidence=confidence
    )


def _unknown() -> ClassificationResult:
    return ClassificationResult(
        category=UNKNOWN_CATEGORY,
        camera_profile=UNKNOWN_PROFILE_LABEL,
        confidence=Confidence.LOW,
    )

"""Applies camera_profiles.yaml rules to classify an asset's probable source.

Rule-based, not Asset Intelligence (see CLAUDE.md) — deterministic pattern/metadata
matching, no AI/ML. Matching is tiered by specificity, most reliable first:

1. make/model (or, for the Audio profile, real stream analysis) — the most specific
   signal, wins outright if exactly one profile matches.
2. container brand (major_brand/compatible_brands, e.g. Sony's "XAVC") — real
   metadata, but less specific: several camera models can share the same brand, so
   this only decides things when make/model didn't match anything.
3. filename pattern — the least reliable signal (files get renamed), used only when
   neither metadata tier found anything.

At every tier, a conflict (more than one profile matching) resolves to "Onbekend" at
low confidence — never a silent guess (see docs/MANY_INGEST_BUILD_PLAN.md, section 4).
Brand alone must never be allowed to out-vote a specific make/model hit; that's why
it's a strictly separate, lower-priority tier rather than an equal-weight OR
condition (learned from real footage: Sony FX6 and FX3 share the same XAVC brand,
so brand-only matching would otherwise make them permanently ambiguous even when a
model tag clearly identifies one of them).
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
    strong_matches = [p for p in profiles if _matches_make_or_model(probe_result, p)]
    if len(strong_matches) == 1:
        return _result_for(strong_matches[0], Confidence.HIGH)
    if len(strong_matches) > 1:
        return _unknown()  # tegenstrijdige make/model-matches -> te onzeker om te kiezen

    brand_matches = [p for p in profiles if _matches_brand(probe_result, p)]
    if len(brand_matches) == 1:
        return _result_for(brand_matches[0], Confidence.MEDIUM)
    if len(brand_matches) > 1:
        return _unknown()  # bijv. Sony XAVC zonder model -> kan FX6 of FX3 zijn

    filename_matches = [p for p in profiles if _matches_filename(path, p)]
    if len(filename_matches) == 1:
        return _result_for(filename_matches[0], Confidence.MEDIUM)

    return _unknown()  # geen match, of meerdere profielen matchen dezelfde bestandsnaam


def _matches_filename(path: Path, profile: CameraProfile) -> bool:
    return any(re.match(pattern, path.name) for pattern in profile.filename_patterns)


def _matches_make_or_model(probe_result: ProbeResult | None, profile: CameraProfile) -> bool:
    if probe_result is None:
        return False

    if profile.audio_only:
        return probe_result.has_audio_stream and not probe_result.has_video_stream

    make = (probe_result.make or "").lower()
    model = (probe_result.model or "").lower()
    make_hit = any(needle.lower() in make for needle in profile.metadata_make_contains)
    model_hit = any(needle.lower() in model for needle in profile.metadata_model_contains)
    return make_hit or model_hit


def _matches_brand(probe_result: ProbeResult | None, profile: CameraProfile) -> bool:
    if probe_result is None or not profile.metadata_brand_contains:
        return False

    brand = f"{probe_result.major_brand or ''} {probe_result.compatible_brands or ''}".lower()
    return any(needle.lower() in brand for needle in profile.metadata_brand_contains)


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

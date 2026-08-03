"""Applies camera_profiles.yaml rules to classify an asset's probable source.

Rule-based, not Asset Intelligence (see CLAUDE.md) — deterministic pattern/metadata
matching, no AI/ML. Matching is tiered by how trustworthy each *signal* is — the
tiers are generic across all profiles, there is no per-camera special-casing:

- **HIGH** — an exact make/model metadata match (or, for the Audio profile, real
  stream analysis), OR a filename pattern ManyFast has confirmed against real
  footage (`confirmed_filename_patterns` in camera_profiles.yaml — e.g.
  `611_####.MXF`, confirmed as Sony FX6 across three independent projects), OR a
  confirmed container-format match (`metadata_match.container_contains` — e.g.
  MXF vs. XAVC-MP4 as a confirmed FX6-vs-FX3 workflow convention). See
  docs/MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md sections 7–8 for the evidence
  and the explicit caveat: this is a confirmed *workflow convention*, not an
  immutable hardware fact like make/model — reliable as long as the convention
  holds.
- **MEDIUM** — a container-brand match (major_brand/compatible_brands, e.g. Sony's
  "XAVC" — real metadata, but shared across models so less specific), OR a generic,
  not-yet-confirmed filename pattern (`filename_patterns` — e.g. `DJI_*`, `GH*`,
  `DSC####`).
- **LOW ("Onbekend")** — nothing matched, or more than one profile matched at the
  same tier. A conflict is never silently resolved by falling through to a weaker
  tier; it resolves straight to Onbekend (see docs/MANY_INGEST_BUILD_PLAN.md,
  section 4).

Whether a filename pattern lives in `confirmed_filename_patterns` or
`filename_patterns` is a data decision (evidence from real footage), not a code
decision — adding a new confirmed pattern for any camera is a YAML change, not a
code change.
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
    high_matches = [
        p
        for p in profiles
        if _matches_make_or_model(probe_result, p)
        or _matches_confirmed_filename(path, p)
        or _matches_container(probe_result, p)
    ]
    if len(high_matches) == 1:
        return _result_for(high_matches[0], Confidence.HIGH)
    if len(high_matches) > 1:
        return _unknown()  # tegenstrijdige signalen op het hoogste niveau -> te onzeker

    medium_matches = [
        p
        for p in profiles
        if _matches_brand(probe_result, p) or _matches_generic_filename(path, p)
    ]
    if len(medium_matches) == 1:
        return _result_for(medium_matches[0], Confidence.MEDIUM)
    if len(medium_matches) > 1:
        return _unknown()  # bijv. Sony XAVC zonder model -> kan FX6 of FX3 zijn

    return _unknown()  # geen enkel signaal matcht


def _matches_confirmed_filename(path: Path, profile: CameraProfile) -> bool:
    return any(re.match(pattern, path.name) for pattern in profile.confirmed_filename_patterns)


def _matches_generic_filename(path: Path, profile: CameraProfile) -> bool:
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


def _matches_container(probe_result: ProbeResult | None, profile: CameraProfile) -> bool:
    if probe_result is None or not profile.metadata_container_contains:
        return False

    container = (probe_result.container_format or "").lower()
    return any(needle.lower() in container for needle in profile.metadata_container_contains)


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

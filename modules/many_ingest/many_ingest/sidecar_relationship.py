"""Fase 5.2 — Sidecar association: links a Sony NonRealTimeMeta XML sidecar to
its main-media file. Evidence-driven, never camera-model-driven (see CLAUDE.md's
Fase 5.2 real-media audit on /Volumes/Sharpwaves — the same logic applies
identically whether the main media turns out to be an FX6 or an FX3, since
nothing here ever branches on camera_profile/manufacturer/model).

A separate layer from classification/ on purpose: classification decides WHAT a
file is; this module decides HOW two files relate to each other. Two required
steps, never conflated:

1. find_sidecar_candidates() — naming + same-directory candidacy only, never
   proof. `{stem}M01.XML` next to `{stem}.MXF`/`{stem}.MP4`, matched strictly
   within one directory. The audit found the exact same clip-number stem
   (e.g. "C0100") pointing at two entirely different files in two different
   directories on the same disk — a scan-wide or disk-wide stem match would
   be unsafe, so candidacy is always directory-scoped.
2. resolve_relationship() — the actual evidence. A shared UMID
   (`TargetMaterial/@umidRef` in the XML vs. the `material_package_umid`
   ffprobe exposes for MXF) is the strongest, cryptographically unique proof
   — confirmed 123/123 on real FX6 footage, 0 mismatches. A duration match
   (within one frame) is used only when UMID comparison isn't possible at all
   (one or both sides missing, e.g. FX3/A7IV's MP4 container never exposes a
   UMID via ffprobe) — never to override an explicit UMID mismatch.
"""

from __future__ import annotations

import dataclasses
import enum
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from many_ingest.metadata_extractor import ProbeResult

_SIDECAR_NAME_PATTERN = re.compile(r"^(?P<stem>.+)M01\.xml$", re.IGNORECASE)
# Exactly the two containers proven in the audit (123 MXF + 65 MP4 pairs) —
# deliberately not the broader classification/file_types.VIDEO_EXTENSIONS
# (which also includes .mov, never observed paired with a sidecar).
_CANDIDATE_MAIN_MEDIA_EXTENSIONS = (".mxf", ".mp4")


class RelationshipEvidence(enum.Enum):
    UMID_MATCH = "umid_match"
    NAME_AND_DURATION_MATCH = "name_and_duration_match"
    NONE = "none"


@dataclasses.dataclass(frozen=True)
class SidecarMetadata:
    """The minimal Sony NonRealTimeMeta fields Fase 5.2 needs — never the full
    schema. Device/lens/serial are explicitly out of scope (see the Fase 5.2
    design and its real-media audit)."""

    umid: str | None
    duration_seconds: float | None
    frame_rate: int | None


def find_sidecar_candidates(files: list[Path], source: Path) -> dict[Path, Path]:
    """sidecar path -> candidate main-media path, matched strictly within the
    same directory (computed relative to `source`, so behaviour never depends
    on the absolute mount path a disk happens to be attached at). Candidacy
    only — never proof; see resolve_relationship() for the actual evidence
    check."""
    by_directory: dict[Path, dict[str, Path]] = {}
    for file_path in files:
        by_directory.setdefault(_directory_key(file_path, source), {})[
            file_path.name.lower()
        ] = file_path

    candidates: dict[Path, Path] = {}
    for file_path in files:
        match = _SIDECAR_NAME_PATTERN.match(file_path.name)
        if match is None:
            continue
        stem = match.group("stem")
        siblings = by_directory.get(_directory_key(file_path, source), {})
        for extension in _CANDIDATE_MAIN_MEDIA_EXTENSIONS:
            main_media_path = siblings.get((stem + extension).lower())
            if main_media_path is not None:
                candidates[file_path] = main_media_path
                break
    return candidates


def _directory_key(path: Path, source: Path) -> Path:
    try:
        return path.relative_to(source).parent
    except ValueError:
        return path.parent


def parse_sony_sidecar(path: Path) -> SidecarMetadata | None:
    """Reads only TargetMaterial/@umidRef and Duration/@value +
    LtcChangeTable/@tcFps — never the full Sony schema. Never raises: a
    malformed, unreadable, or unexpectedly-shaped XML file returns None, and
    the sidecar simply stays an ordinary, unrelated asset (see
    resolve_relationship()) — a bad sidecar must never block ingest.

    Namespace-tolerant by design (matches by local tag name only, ignoring
    the schema version URI): the real footage audited already showed more
    than one NonRealTimeMeta version string across camera models
    (ver.2.10/ver.2.20), so hardcoding one exact namespace URI would be an
    assumption the evidence itself already contradicts.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None

    umid: str | None = None
    duration_frames: int | None = None
    frame_rate: int | None = None

    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name == "TargetMaterial" and umid is None:
            umid = element.get("umidRef")
        elif local_name == "Duration" and duration_frames is None:
            duration_frames = _parse_int(element.get("value"))
        elif local_name == "LtcChangeTable" and frame_rate is None:
            frame_rate = _parse_int(element.get("tcFps"))

    duration_seconds = (
        duration_frames / frame_rate if duration_frames is not None and frame_rate else None
    )
    return SidecarMetadata(umid=umid, duration_seconds=duration_seconds, frame_rate=frame_rate)


def _parse_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def resolve_relationship(
    sidecar_metadata: SidecarMetadata | None, main_probe: ProbeResult | None
) -> RelationshipEvidence:
    """Evidence-driven, in strict priority order — never camera-model-driven:

    1. A shared UMID wins outright (both present and equal -> UMID_MATCH).
    2. An explicit UMID mismatch (both present, different) is final —
       NEVER rescued by a duration match, however close.
    3. Duration (within one frame, computed from the sidecar's own tcFps) is
       only consulted when UMID comparison isn't possible at all (one or
       both sides missing — e.g. FX3/A7IV's MP4 container, which never
       exposes a UMID via ffprobe).

    Anything else (malformed/unreadable sidecar, missing probe, missing
    duration on either side, an invalid frame rate, or a duration further
    apart than one frame) is NONE — no guessing, ever.
    """
    if sidecar_metadata is None:
        return RelationshipEvidence.NONE

    xml_umid = _normalize_umid(sidecar_metadata.umid)
    media_umid = _normalize_umid(main_probe.material_package_umid) if main_probe else None

    if xml_umid is not None and media_umid is not None:
        return (
            RelationshipEvidence.UMID_MATCH
            if xml_umid == media_umid
            else RelationshipEvidence.NONE
        )

    if (
        sidecar_metadata.duration_seconds is None
        or sidecar_metadata.frame_rate is None
        or main_probe is None
        or main_probe.duration_seconds is None
    ):
        return RelationshipEvidence.NONE

    tolerance_seconds = 1 / sidecar_metadata.frame_rate
    if abs(main_probe.duration_seconds - sidecar_metadata.duration_seconds) <= tolerance_seconds:
        return RelationshipEvidence.NAME_AND_DURATION_MATCH
    return RelationshipEvidence.NONE


def _normalize_umid(raw: str | None) -> str | None:
    """Safe syntactic normalization only (see Fase 5.2 design) — never a
    fuzzy/approximate comparison. Strips an optional "0x"/"0X" prefix (ffprobe
    prefixes material_package_umid with it; the XML's umidRef never has one)
    and case-folds to uppercase (both forms observed as uppercase hex in the
    audit, but this must not depend on that always holding)."""
    if not raw:
        return None
    value = raw.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    return value.upper() or None

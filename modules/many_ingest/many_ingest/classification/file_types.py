"""Rule-based file-type recognition: video / audio / unknown.

Extension check first; an already-computed ProbeResult (not a fresh ffprobe call —
see metadata_extractor.safe_probe) resolves containers that could hold either, e.g. a
.mov with only an audio stream.
"""

from __future__ import annotations

import enum
from pathlib import Path

from many_ingest.metadata_extractor import ProbeResult

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mxf"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aif", ".aiff"}

# Fase 5.0 — additief, alleen gebruikt door detect_media_type() hieronder, nooit
# door detect_file_type() (die blijft ongewijzigd op video/audio/unknown).
# Bewust conservatief: alleen extensies die ondubbelzinnig een stilstaand beeld
# resp. een niet-media companion-bestand zijn — geen metadata-parsing, geen
# sidecar-koppeling (dat is Fase 5.2).
PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".dng",
    ".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2",
}
SIDECAR_EXTENSIONS = {".xml", ".xmp", ".thm"}


class FileType(enum.Enum):
    VIDEO = "video"
    AUDIO = "audio"
    UNKNOWN = "unknown"


class MediaType(enum.Enum):
    """Fase 5.0 — bredere, aparte opvolger-classificatie naast FileType (niet ter
    vervanging: FileType blijft bestaan en ongewijzigd, zie CLAUDE.md/Fase 5.0-
    ontwerp). Altijd berekenbaar; UNKNOWN is de veilige fallback voor een nieuw
    asset, nooit None."""

    VIDEO = "video"
    AUDIO = "audio"
    PHOTO = "photo"
    SIDECAR = "sidecar"
    UNKNOWN = "unknown"


def detect_file_type(path: Path, probe_result: ProbeResult | None) -> FileType:
    ext = path.suffix.lower()

    if ext in AUDIO_EXTENSIONS:
        return FileType.AUDIO

    if ext in VIDEO_EXTENSIONS:
        if probe_result is None:
            return FileType.VIDEO
        if probe_result.has_video_stream:
            return FileType.VIDEO
        if probe_result.has_audio_stream:
            return FileType.AUDIO
        return FileType.UNKNOWN

    return FileType.UNKNOWN


def detect_media_type(path: Path, probe_result: ProbeResult | None) -> MediaType:
    """Independent of detect_file_type() — a separate, additive classification
    (see Fase 5.0 domain model). Extension-only for sidecar/photo (no metadata
    parsing, no sidecar-to-clip pairing — that's Fase 5.2); reuses the same
    video/audio extension sets and stream-presence disambiguation as
    detect_file_type() so the two stay conceptually consistent for the cases
    they share."""
    ext = path.suffix.lower()

    if ext in SIDECAR_EXTENSIONS:
        return MediaType.SIDECAR

    if ext in PHOTO_EXTENSIONS:
        return MediaType.PHOTO

    if ext in AUDIO_EXTENSIONS:
        return MediaType.AUDIO

    if ext in VIDEO_EXTENSIONS:
        if probe_result is None:
            return MediaType.VIDEO
        if probe_result.has_video_stream:
            return MediaType.VIDEO
        if probe_result.has_audio_stream:
            return MediaType.AUDIO
        return MediaType.UNKNOWN

    return MediaType.UNKNOWN

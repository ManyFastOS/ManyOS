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
AUDIO_EXTENSIONS = {".wav", ".mp3"}


class FileType(enum.Enum):
    VIDEO = "video"
    AUDIO = "audio"
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

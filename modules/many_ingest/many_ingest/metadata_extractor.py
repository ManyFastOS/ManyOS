"""ffprobe wrapper: extracts technical metadata from a media file.

Pure technical extraction — codec, resolution, duration, device tags if present.
No AI/ML involved; this is explicitly not Asset Intelligence (see CLAUDE.md).
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path


class FfprobeNotFoundError(RuntimeError):
    """Raised when the `ffprobe` binary isn't available on PATH."""


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    has_video_stream: bool
    has_audio_stream: bool
    codec: str | None
    width: int | None
    height: int | None
    frame_rate: str | None
    duration_seconds: float | None
    make: str | None
    model: str | None
    creation_time: str | None


def _ffprobe_path() -> str:
    path = shutil.which("ffprobe")
    if path is None:
        raise FfprobeNotFoundError(
            "ffprobe niet gevonden op PATH. Installeer via `brew install ffmpeg`."
        )
    return path


def probe(path: Path) -> ProbeResult:
    """Run ffprobe on `path` and return its technical metadata.

    Raises FfprobeNotFoundError, subprocess.CalledProcessError or
    json.JSONDecodeError on failure — use `safe_probe` where one bad file
    shouldn't stop the rest of a run.
    """
    ffprobe = _ffprobe_path()
    command = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)

    streams = data.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    format_tags = data.get("format", {}).get("tags", {}) or {}
    duration = data.get("format", {}).get("duration")
    first_video = video_streams[0] if video_streams else None

    return ProbeResult(
        has_video_stream=bool(video_streams),
        has_audio_stream=bool(audio_streams),
        codec=first_video.get("codec_name") if first_video else None,
        width=first_video.get("width") if first_video else None,
        height=first_video.get("height") if first_video else None,
        frame_rate=first_video.get("r_frame_rate") if first_video else None,
        duration_seconds=float(duration) if duration is not None else None,
        make=_first_tag(format_tags, ["make", "com.apple.quicktime.make"]),
        model=_first_tag(format_tags, ["model", "com.apple.quicktime.model"]),
        creation_time=_first_tag(format_tags, ["creation_time"]),
    )


def safe_probe(path: Path) -> ProbeResult | None:
    """Like `probe`, but never raises — returns None if ffprobe is unavailable or fails.

    Used by the ingest pipeline so one unreadable/corrupt file doesn't abort a run;
    the caller is responsible for logging the None case.
    """
    try:
        return probe(path)
    except (FfprobeNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None


def _first_tag(tags: dict, keys: list[str]) -> str | None:
    lowered = {key.lower(): value for key, value in tags.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None

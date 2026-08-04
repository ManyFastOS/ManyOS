"""macOS volume detection for source selection — a self-contained, testable
OS-integration layer, deliberately separate from MainWindow (see CLAUDE.md:
no business logic in the GUI; docs/MANY_INGEST_V1_UX_DESIGN.md, hoofdstuk 3).

No daemon, no watcher, no auto-launch: detection only runs when explicitly
asked (app startup, "Opnieuw zoeken"/"Andere schijf gebruiken").

Deliberately does not use ffprobe/classify() — the media count/size here are a
quick, extension-based estimate for display, not the real ingest scan.
"""

from __future__ import annotations

import dataclasses
import re
import shutil
from pathlib import Path
from typing import Callable

from many_ingest.classification.file_types import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS

VOLUMES_ROOT = Path("/Volumes")
BOOT_PATH = Path("/")

_RELEVANT_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
_IGNORED_NAMES = {".DS_Store", "Thumbs.db", ".Spotlight-V100", ".Trashes", ".fseventsd"}

# Best-effort, not exhaustive — "waar redelijkerwijs herkenbaar" (see the build
# plan). May need tuning against real machines, same as camera_profiles.yaml
# was tuned against real footage.
_EXCLUDED_NAME_PATTERNS = (
    re.compile(r"^Macintosh HD"),
    re.compile(r"^Recovery$"),
    re.compile(r"com\.apple\.TimeMachine", re.IGNORECASE),
    re.compile(r"Time ?Machine", re.IGNORECASE),
    re.compile(r"^VM$"),
)


@dataclasses.dataclass(frozen=True)
class VolumeInfo:
    """What the UI needs about one candidate volume — never a full path."""

    name: str
    path: Path
    capacity_bytes: int
    media_file_count: int
    media_total_bytes: int


def list_candidate_volumes(
    storage_root: Path | None,
    volumes_root: Path = VOLUMES_ROOT,
    *,
    is_boot_volume: Callable[[Path], bool] | None = None,
    is_destination_volume: Callable[[Path, Path | None], bool] | None = None,
) -> list[VolumeInfo]:
    """Every mounted volume that could plausibly be a shoot's source.

    Always excludes: the boot/system volume, the destination volume derived
    from `storage_root` (if given), unreadable volumes, empty volumes, and
    volumes whose name matches a known system/recovery/backup pattern.

    `is_boot_volume`/`is_destination_volume` default to real device-identity
    checks; tests inject simple fakes so the exclusion/composition logic is
    verifiable without a real second disk (see docs/MANY_INGEST_V1_UX_DESIGN.md
    and the accompanying test suite for why this split exists).
    """
    is_boot_volume = is_boot_volume or default_is_boot_volume
    is_destination_volume = is_destination_volume or default_is_destination_volume

    if not volumes_root.is_dir():
        return []

    candidates: list[VolumeInfo] = []
    for entry in sorted(volumes_root.iterdir()):
        if entry.name.startswith("."):
            continue
        if _matches_excluded_name(entry.name):
            continue
        if is_boot_volume(entry):
            continue
        if is_destination_volume(entry, storage_root):
            continue

        info = describe_volume(entry)
        if info is not None:
            candidates.append(info)

    return candidates


def describe_volume(path: Path) -> VolumeInfo | None:
    """Reads name/capacity/media summary from a real mounted path.

    Returns None if the volume is unreadable or empty (on visible content) —
    both are exclusion reasons, not errors to surface to the user.
    """
    try:
        top_level = list(path.iterdir())
    except OSError:
        return None  # onleesbaar

    visible = [e for e in top_level if e.name not in _IGNORED_NAMES and not e.name.startswith(".")]
    if not visible:
        return None  # leeg (op systeembestanden na)

    try:
        capacity_bytes = shutil.disk_usage(path).total
    except OSError:
        return None  # onleesbaar

    media_file_count, media_total_bytes = scan_media_summary(path)

    return VolumeInfo(
        name=path.name,
        path=path,
        capacity_bytes=capacity_bytes,
        media_file_count=media_file_count,
        media_total_bytes=media_total_bytes,
    )


def scan_media_summary(path: Path) -> tuple[int, int]:
    """Quick, best-effort (count, total_bytes) of relevant media under `path`.

    Used both for volumes and for a manually chosen folder. Extension-based
    only, skips hidden/system entries at any depth — not the real ingest
    scan, which happens later via IngestService.
    """
    count = 0
    total_bytes = 0
    try:
        for file_path in path.rglob("*"):
            if not file_path.is_file():
                continue
            if _is_ignored(file_path, path):
                continue
            if file_path.suffix.lower() not in _RELEVANT_EXTENSIONS:
                continue
            count += 1
            try:
                total_bytes += file_path.stat().st_size
            except OSError:
                pass
    except OSError:
        pass  # best-effort: geeft terug wat er al geteld was
    return count, total_bytes


def format_size(num_bytes: int) -> str:
    """Same B/KB/MB/GB/TB formatting as core/report.py's render_report, kept
    as a small, deliberate local copy so the GUI stays decoupled from the
    engine's reporting module until a later phase actually calls it."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def default_is_boot_volume(path: Path, boot_path: Path = BOOT_PATH) -> bool:
    device = _device_id(path)
    return device is not None and device == _device_id(boot_path)


def default_is_destination_volume(path: Path, storage_root: Path | None) -> bool:
    if storage_root is None:
        return False
    ancestor = _existing_ancestor(storage_root)
    if ancestor is None:
        return False
    device = _device_id(path)
    return device is not None and device == _device_id(ancestor)


def _matches_excluded_name(name: str) -> bool:
    return any(pattern.search(name) for pattern in _EXCLUDED_NAME_PATTERNS)


def _is_ignored(file_path: Path, root: Path) -> bool:
    parts = file_path.relative_to(root).parts
    return any(part in _IGNORED_NAMES or part.startswith(".") for part in parts)


def _device_id(path: Path) -> int | None:
    try:
        return path.stat().st_dev
    except OSError:
        return None


def _existing_ancestor(path: Path) -> Path | None:
    current = path
    while not current.exists():
        if current.parent == current:
            return None
        current = current.parent
    return current

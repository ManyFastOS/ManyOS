"""Human-readable ingest summary.

Split in two deliberately: `IngestSummary` is plain structured data (reusable by
any interface — a future GUI renders it as widgets, not text), and `render_report`
is one particular plain-text rendering of it, used by the CLI today. Neither
depends on how the CLI presents things; `summarize()` only depends on `IngestReport`.
"""

from __future__ import annotations

import collections
import dataclasses

from many_ingest.classification.file_types import FileType
from many_ingest.core.ingest_service import AssetOutcome, IngestReport

_DIVIDER = "=" * 34


@dataclasses.dataclass(frozen=True)
class IngestSummary:
    project: str
    client: str
    dry_run: bool
    total_files: int
    video_count: int
    audio_count: int
    unknown_type_count: int
    camera_profile_counts: dict[str, int]
    duplicates: int
    name_conflicts_resolved: int
    errors: int
    total_bytes: int
    duration_seconds: float
    log_path: str
    safe_to_delete_source: bool


def summarize(report: IngestReport) -> IngestSummary:
    profile_counts = collections.Counter(asset.camera_profile for asset in report.assets)
    errors = sum(1 for a in report.assets if a.outcome == AssetOutcome.FAILED_VERIFICATION)

    return IngestSummary(
        project=report.project,
        client=report.client,
        dry_run=report.dry_run,
        total_files=len(report.assets),
        video_count=sum(1 for a in report.assets if a.file_type == FileType.VIDEO),
        audio_count=sum(1 for a in report.assets if a.file_type == FileType.AUDIO),
        unknown_type_count=sum(1 for a in report.assets if a.file_type == FileType.UNKNOWN),
        camera_profile_counts=dict(profile_counts),
        duplicates=sum(1 for a in report.assets if a.outcome == AssetOutcome.DUPLICATE_SKIPPED),
        name_conflicts_resolved=sum(1 for a in report.assets if a.name_conflict_resolved),
        errors=errors,
        total_bytes=report.total_bytes,
        duration_seconds=report.duration_seconds,
        log_path=str(report.log_path),
        # Nooit "JA" bij een dry-run (er is niets gekopieerd) of als er fouten waren
        # (dan is de bron de enige gegarandeerd goede kopie) — safety first.
        safe_to_delete_source=(not report.dry_run) and errors == 0,
    )


def render_report(summary: IngestSummary) -> str:
    if summary.dry_run:
        header = "🔍 PREVIEW VOLTOOID (dry-run — niets gewijzigd)"
    elif summary.errors == 0:
        header = "✅ INGEST VOLTOOID"
    else:
        header = "⚠️  INGEST VOLTOOID MET FOUTEN"

    lines: list[str] = [_DIVIDER, "", header, "", "Project:", summary.project]
    lines += ["", "Bestanden:", str(summary.total_files)]
    lines += ["", "Video:", str(summary.video_count)]
    lines += ["", "Audio:", str(summary.audio_count)]

    for label in sorted(summary.camera_profile_counts):
        lines += ["", f"{label}:", str(summary.camera_profile_counts[label])]

    lines += ["", "Duplicaten:", str(summary.duplicates)]
    lines += ["", "Naamconflicten opgelost:", str(summary.name_conflicts_resolved)]
    lines += ["", "Fouten:", str(summary.errors)]
    lines += ["", "Totale grootte:", _format_size(summary.total_bytes)]
    lines += ["", "Duur:", _format_duration(summary.duration_seconds)]
    lines += ["", "Logbestand:", summary.log_path]

    if summary.dry_run:
        safe_line = "N.V.T. (dry-run — er is niets gekopieerd)"
    else:
        safe_line = "JA" if summary.safe_to_delete_source else "NEE"
    lines += ["", "Veilig om bronmedia te verwijderen:", safe_line]

    lines += ["", _DIVIDER]
    return "\n".join(lines)


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_duration(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

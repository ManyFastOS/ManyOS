"""Orchestrates the Many Ingest pipeline.

Dry-run and a real run share this one code path: `run()` always scans, classifies,
resolves the destination (including collision checks) and checks for duplicates.
Only the side-effecting steps (copy, verify, register in the ManyFast Asset Schema)
are gated behind `dry_run`, never a separate implementation (see
docs/MANY_INGEST_BUILD_PLAN.md, section 2). Every asset outcome is written to the
action log, regardless of dry-run.

Safety-first additions (see docs/MANY_INGEST_V0.1_READINESS_ASSESSMENT.md):
- Collision protection: a destination file is never silently overwritten.
- An ffprobe pre-flight check aborts the whole run rather than silently degrading
  every asset to "Onbekend".
- An optional `progress_callback` reports per-file progress — a plain callback, not
  printing directly, so a future GUI can reuse `IngestService` unchanged.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import getpass
import socket
import time
import uuid
from pathlib import Path
from typing import Callable

from many_ingest.classification.camera_profiles import Confidence, classify
from many_ingest.classification.file_types import FileType, detect_file_type
from many_ingest.config import CameraProfile, IngestConfig
from many_ingest.logger import ActionLogger
from many_ingest.metadata_extractor import (
    FFPROBE_INSTALL_MESSAGE,
    FfprobeNotFoundError,
    ProbeResult,
    is_ffprobe_available,
    safe_probe,
)
from many_ingest.ports.manifest import AssetRecord, Manifest
from many_ingest.ports.storage import Storage

_MAX_COLLISION_ATTEMPTS = 999


class AssetOutcome(enum.Enum):
    PREVIEW = "preview"
    COPIED = "copied"
    DUPLICATE_SKIPPED = "duplicate_skipped"
    FAILED_VERIFICATION = "failed_verification"


@dataclasses.dataclass
class ScanResult:
    source: Path
    files: list[Path]


@dataclasses.dataclass(frozen=True)
class ProgressUpdate:
    """One per-file progress tick. Plain data — the caller decides how to display
    it (a terminal line today, a GUI progress bar later)."""

    processed: int
    total: int
    current_file: str
    bytes_processed: int


ProgressCallback = Callable[[ProgressUpdate], None]


@dataclasses.dataclass
class AssetResult:
    source_path: Path
    destination_path: Path
    file_type: FileType
    category: str
    camera_profile: str
    confidence: Confidence
    checksum: str
    is_duplicate: bool
    outcome: AssetOutcome
    name_conflict_resolved: bool = False
    error: str | None = None


@dataclasses.dataclass
class IngestReport:
    source: Path
    client: str
    project: str
    dry_run: bool
    run_id: str
    log_path: Path
    duration_seconds: float
    total_bytes: int
    assets: list[AssetResult]


class IngestService:
    def __init__(
        self,
        storage: Storage,
        manifest: Manifest,
        config: IngestConfig,
        camera_profiles: list[CameraProfile],
    ) -> None:
        self._storage = storage
        self._manifest = manifest
        self._config = config
        self._camera_profiles = camera_profiles

    def scan(self, source: Path) -> ScanResult:
        files = list(self._storage.list_files(source))
        return ScanResult(source=Path(source), files=files)

    def run(
        self,
        source: Path,
        client: str,
        project: str,
        dry_run: bool,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestReport:
        if not is_ffprobe_available():
            # Never proceed without metadata capability — a run without ffprobe would
            # silently degrade every asset to "Onbekend" instead of failing loudly.
            raise FfprobeNotFoundError(FFPROBE_INSTALL_MESSAGE)

        start = time.monotonic()
        scan_result = self.scan(source)
        run_id = str(uuid.uuid4())
        logger = ActionLogger(
            path=self._config.log_dir / f"{run_id}.jsonl", run_id=run_id, dry_run=dry_run
        )
        logger.log(
            "run_started",
            source=str(source),
            client=client,
            project=project,
            files_found=len(scan_result.files),
        )

        total = len(scan_result.files)
        assets: list[AssetResult] = []
        bytes_processed = 0

        for index, path in enumerate(scan_result.files, start=1):
            asset = self._process_asset(path, client, project, run_id, dry_run, logger)
            assets.append(asset)

            try:
                bytes_processed += path.stat().st_size
            except OSError:
                pass  # bestandsgrootte is alleen voor voortgangsweergave, geen kritiek pad

            if progress_callback is not None:
                progress_callback(
                    ProgressUpdate(
                        processed=index,
                        total=total,
                        current_file=path.name,
                        bytes_processed=bytes_processed,
                    )
                )

        logger.log(
            "run_completed",
            total=len(assets),
            copied=sum(1 for a in assets if a.outcome == AssetOutcome.COPIED),
            duplicate_skipped=sum(1 for a in assets if a.outcome == AssetOutcome.DUPLICATE_SKIPPED),
            failed=sum(1 for a in assets if a.outcome == AssetOutcome.FAILED_VERIFICATION),
        )

        return IngestReport(
            source=scan_result.source,
            client=client,
            project=project,
            dry_run=dry_run,
            run_id=run_id,
            log_path=logger.path,
            duration_seconds=time.monotonic() - start,
            total_bytes=bytes_processed,
            assets=assets,
        )

    def _process_asset(
        self,
        path: Path,
        client: str,
        project: str,
        run_id: str,
        dry_run: bool,
        logger: ActionLogger,
    ) -> AssetResult:
        probe_result = safe_probe(path)
        file_type = detect_file_type(path, probe_result)
        classification = classify(path, probe_result, self._camera_profiles)
        recording_date = _resolve_recording_date(path, probe_result)
        naive_destination = _build_workspace_path(
            storage_root=self._config.storage_root,
            client=client,
            project=project,
            recording_date=recording_date,
            category=classification.category,
            filename=path.name,
        )
        checksum = self._storage.checksum(path)
        is_duplicate = self._manifest.is_duplicate(checksum)

        destination = naive_destination
        name_conflict_resolved = False
        error: str | None = None

        if not is_duplicate:
            # Collision check runs in dry-run too (read-only: exists + checksum of
            # what's already there) so the preview reflects reality — only the
            # actual copy/register below is gated behind dry_run.
            try:
                destination, is_duplicate = self._resolve_destination(checksum, naive_destination)
                name_conflict_resolved = (not is_duplicate) and (destination != naive_destination)
            except OSError as exc:
                error = f"Kon doelmap niet controleren op naamconflicten: {exc}"

        if error is not None:
            outcome = AssetOutcome.FAILED_VERIFICATION
        elif dry_run:
            outcome = AssetOutcome.PREVIEW
        elif is_duplicate:
            outcome = AssetOutcome.DUPLICATE_SKIPPED
        else:
            try:
                self._storage.copy(path, destination)
                destination_checksum = self._storage.checksum(destination)
            except OSError as exc:
                outcome = AssetOutcome.FAILED_VERIFICATION
                error = str(exc)
            else:
                if destination_checksum != checksum:
                    outcome = AssetOutcome.FAILED_VERIFICATION
                    error = "Checksum van de kopie komt niet overeen met het origineel."
                else:
                    self._manifest.register(
                        AssetRecord(
                            asset_id=checksum,
                            client_id=client,
                            project_id=project,
                            ingest_run_id=run_id,
                            operator=getpass.getuser(),
                            source_machine=socket.gethostname(),
                            original_path=path,
                            destination_path=destination,
                            category=classification.category,
                            camera_profile=classification.camera_profile,
                            confidence=classification.confidence.value,
                            ingested_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                        )
                    )
                    outcome = AssetOutcome.COPIED

        logger.log(
            "asset_processed",
            source_path=str(path),
            destination_path=str(destination),
            file_type=file_type.value,
            category=classification.category,
            camera_profile=classification.camera_profile,
            confidence=classification.confidence.value,
            checksum=checksum,
            is_duplicate=is_duplicate,
            name_conflict_resolved=name_conflict_resolved,
            outcome=outcome.value,
            error=error,
        )

        return AssetResult(
            source_path=path,
            destination_path=destination,
            file_type=file_type,
            category=classification.category,
            camera_profile=classification.camera_profile,
            confidence=classification.confidence,
            checksum=checksum,
            is_duplicate=is_duplicate,
            outcome=outcome,
            name_conflict_resolved=name_conflict_resolved,
            error=error,
        )

    def _resolve_destination(self, source_checksum: str, destination: Path) -> tuple[Path, bool]:
        """Never overwrites an existing file.

        Returns (final_destination, is_duplicate). If `destination` is already
        occupied by a file with the same checksum, that's a duplicate — no new
        path is needed. If it's occupied by different content, an automatic
        `_001`, `_002`, ... suffix is appended until a free (or checksum-matching)
        name is found.
        """
        candidate = destination
        index = 0
        while self._storage.exists(candidate):
            if self._storage.checksum(candidate) == source_checksum:
                return candidate, True
            index += 1
            if index > _MAX_COLLISION_ATTEMPTS:
                raise RuntimeError(
                    f"Te veel naamconflicten voor {destination} (>{_MAX_COLLISION_ATTEMPTS})."
                )
            candidate = _with_suffix(destination, index)
        return candidate, False


def _with_suffix(path: Path, index: int) -> Path:
    return path.with_name(f"{path.stem}_{index:03d}{path.suffix}")


def _resolve_recording_date(path: Path, probe_result: ProbeResult | None) -> dt.date:
    if probe_result is not None and probe_result.creation_time:
        try:
            return dt.datetime.fromisoformat(
                probe_result.creation_time.replace("Z", "+00:00")
            ).date()
        except ValueError:
            pass
    return dt.date.fromtimestamp(path.stat().st_mtime)


def _build_workspace_path(
    storage_root: Path,
    client: str,
    project: str,
    recording_date: dt.date,
    category: str,
    filename: str,
) -> Path:
    return (
        storage_root
        / "Klanten"
        / client
        / project
        / f"{recording_date.isoformat()}_Raw"
        / category
        / filename
    )

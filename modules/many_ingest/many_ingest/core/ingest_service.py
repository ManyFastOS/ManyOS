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
- A destination pre-flight check (`DestinationUnavailableError`, Fase 3.5) aborts a
  REAL run before touching anything if `config.storage_root` isn't reachable/
  writable — e.g. the chosen destination disk was unmounted after a preview. Never
  raised for `dry_run=True`: a preview only reads, it never needs the destination to
  exist yet (see logger.py's docstring for the matching, earlier decision about
  `log_dir` specifically — this is the same principle applied consistently to the
  destination as a whole).
- An optional `progress_callback` reports per-file progress — a plain callback, not
  printing directly, so a future GUI can reuse `IngestService` unchanged.
- An optional `asset_callback` reports each file's outcome (`AssetResult`) the
  moment it's known — additive, defaults to `None`, no change for existing
  callers. Lets a caller stream per-asset results live instead of only seeing
  them in the final `IngestReport.assets` list (see ingest_worker.py).

Destination capacity safety (added after a real manual Fase 4 test drove a
destination disk to 0 bytes free mid-run — see the 2026-09-15 forensic audit):
- A capacity preflight (`DestinationFullError`, `_ensure_destination_has_capacity`)
  aborts a REAL run before the first copy if the destination doesn't have
  enough free space for the scanned files plus a small explicit margin
  (`_DESTINATION_FREE_SPACE_MARGIN_BYTES`). Never raised for a dry-run — same
  read-only principle as `DestinationUnavailableError`.
- A filesystem can still fill up mid-run despite that preflight (e.g. another
  process writing to the same disk concurrently). Every destination-write
  call `_process_asset`/`run()` makes (copy, action-log, manifest) is
  classified: `errno.ENOSPC` becomes `DestinationFullError`, any other
  destination-write `OSError` becomes `DestinationUnavailableError` — never
  left to escape unclassified, which is what previously let a destination
  problem be mislabeled as "source unreadable" by a caller's broad
  `except OSError` (ingest_worker.py). A non-ENOSPC failure during the
  content copy itself keeps its pre-existing behaviour (a per-asset
  `FAILED_VERIFICATION`, the run continues to the next file) — only ENOSPC
  aborts the whole run, since every subsequent file would fail identically.
- A failed copy never leaves a partial destination file behind: `_resolve_destination`
  already proves the chosen `destination` path did not exist before the copy
  attempt, so it's always safe to remove on failure (see `_process_asset`).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import errno
import getpass
import socket
import time
import uuid
from pathlib import Path
from typing import Callable

from many_ingest.classification.camera_profiles import ClassificationSource, Confidence, classify
from many_ingest.classification.file_types import (
    FileType,
    MediaType,
    detect_file_type,
    detect_media_type,
)
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

# De vaste map tussen storage_root (config.py's footage_subpath, resolved
# against a chosen destination_root) en {client}/{project} — part of the
# Project Workspace convention, not something config.yaml controls (unlike
# footage_subpath/manifest_subpath/log_subpath). A single named constant, not
# a literal repeated elsewhere: the desktop app's destination breadcrumb
# (main_window.py) imports this rather than re-hardcoding "Klanten" itself,
# so the GUI's presentation can never drift from what this function actually
# builds on disk.
CLIENT_FOLDER_NAME = "Klanten"


class DestinationUnavailableError(Exception):
    """Raised at the start of a REAL run when the destination (resolved from
    a chosen `destination_root`, see config.py) isn't reachable/writable —
    e.g. the destination disk was unmounted after a preview. Never raised
    for a dry-run. Distinct from a bare `OSError` on purpose: a caller
    (ingest_worker.py, cli.py) can catch this separately and show a message
    that actually names the destination, instead of the destination's
    failure being caught by a generic `except OSError` and misattributed to
    the source (a real bug found in manual testing before this existed).

    Also raised mid-run (not just at the start) for a non-ENOSPC OSError
    while writing the action log or the manifest — those are infrastructure
    writes tied to the whole run's integrity, not a single asset's, so a
    genuine write failure there aborts the run rather than being silently
    treated as "this one file is bad" (see `_log_or_raise`/`_process_asset`)."""


class DestinationFullError(Exception):
    """Raised when the destination doesn't have enough free space — either
    detected up front, before any copy starts (`_ensure_destination_has_capacity`),
    or mid-run when a write actually hits `errno.ENOSPC` (the filesystem can
    fill up between the preflight check and the last file). Distinct from
    `DestinationUnavailableError` on purpose: a caller shows a specific
    "onvoldoende ruimte" message with the actual required/available sizes,
    never the generic "kon deze locatie niet meer lezen" message a bare,
    unclassified `OSError` used to produce (found via a real manual test,
    2026-09-15 — see that day's forensic audit)."""


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
    # Set only when content copied and verified successfully (outcome stays
    # COPIED) but Storage.copy() couldn't fully replicate metadata (timestamps/
    # mode/xattrs/BSD flags) — non-critical, never a reason to fail the asset.
    # See Storage.copy()'s docstring for the failure-severity split this is from.
    metadata_warning: str | None = None
    # Fase 5.0 — additief, computed alongside the fields above but never
    # replacing them (see the Fase 5.0 domain-model design: category/
    # camera_profile/FileType/destination paths/dedupe are all unchanged).
    # manufacturer/model come from the exact same classify() call/CameraProfile
    # record as camera_profile/category — never derived from the label string.
    # media_type is always populated for a new AssetResult (UNKNOWN is its safe
    # fallback, never None); the default below only exists to satisfy dataclass
    # field ordering, real construction in _process_asset() always passes it
    # explicitly. source_relative_path is None only when path.relative_to(source)
    # genuinely can't be computed — see _resolve_source_relative_path().
    manufacturer: str | None = None
    model: str | None = None
    media_type: MediaType = MediaType.UNKNOWN
    source_relative_path: Path | None = None
    # Fase 5.1 — additief, same rationale as the Fase 5.0 block above: real
    # construction in _process_asset() always passes these explicitly, the
    # defaults here only satisfy dataclass field ordering.
    # classification_source is provenance (WHY the classification above was
    # chosen), never a second classification decision — see
    # classification/camera_profiles.py's ClassificationSource. Technical
    # metadata fields follow the None-vs-False rule: None means no reliable
    # probe data (ffprobe missing/failed for this file), False means ffprobe
    # ran and definitively found no such stream.
    classification_source: ClassificationSource = ClassificationSource.NO_SIGNAL_MATCHED
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: str | None = None
    duration_seconds: float | None = None
    has_video_stream: bool | None = None
    has_audio_stream: bool | None = None


AssetCallback = Callable[[AssetResult], None]


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
    # The resolved Project Workspace folder ("Klanten/{client}/{project}" under
    # the chosen destination_root) — the engine is the single source of truth
    # for this path (Fase 4), so a caller (the desktop GUI's "Open in Finder")
    # never has to reconstruct footage_subpath/CLIENT_FOLDER_NAME/client/project
    # itself. Well-defined for a dry-run too (a pure path join, no filesystem
    # access) even though nothing may exist there yet.
    project_workspace_path: Path


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
        asset_callback: AssetCallback | None = None,
    ) -> IngestReport:
        if not is_ffprobe_available():
            # Never proceed without metadata capability — a run without ffprobe would
            # silently degrade every asset to "Onbekend" instead of failing loudly.
            raise FfprobeNotFoundError(FFPROBE_INSTALL_MESSAGE)

        if not dry_run:
            _ensure_destination_is_writable(self._config.storage_root)

        start = time.monotonic()
        scan_result = self.scan(source)

        if not dry_run:
            # Needs the scan result (the actual file list), so this can only run
            # after `self.scan(source)` above — never for a dry-run, same
            # read-only principle as `_ensure_destination_is_writable`.
            _ensure_destination_has_capacity(
                self._storage, self._config.storage_root, scan_result.files
            )

        run_id = str(uuid.uuid4())
        logger = ActionLogger(
            path=self._config.log_dir / f"{run_id}.jsonl", run_id=run_id, dry_run=dry_run
        )
        _log_or_raise(
            logger,
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
            asset = self._process_asset(
                path, scan_result.source, client, project, run_id, dry_run, logger
            )
            assets.append(asset)

            if asset_callback is not None:
                # Aangeroepen zodra het outcome van dít bestand bekend is — vóór
                # progress_callback hieronder, dat alleen aggregaatvoortgang draagt
                # (processed/total/bytes), nooit per-bestand-uitkomst. Zie
                # ingest_worker.py voor de enige huidige consument: streamt
                # hiermee hetzelfde `asset_processed`-event per bestand i.p.v. pas
                # na afloop in bulk.
                asset_callback(asset)

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

        _log_or_raise(
            logger,
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
            project_workspace_path=_project_workspace_path(
                self._config.storage_root, client, project
            ),
        )

    def _process_asset(
        self,
        path: Path,
        source: Path,
        client: str,
        project: str,
        run_id: str,
        dry_run: bool,
        logger: ActionLogger,
    ) -> AssetResult:
        probe_result = safe_probe(path)
        file_type = detect_file_type(path, probe_result)
        media_type = detect_media_type(path, probe_result)
        classification = classify(path, probe_result, self._camera_profiles)
        source_relative_path = _resolve_source_relative_path(path, source)
        technical_metadata = _resolve_technical_metadata(probe_result)
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
        metadata_warning: str | None = None

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
                # Storage.copy() raises OSError only for a genuine content-copy
                # failure; a non-critical metadata-only failure (timestamps/
                # mode/xattrs/BSD flags) comes back as a returned warning
                # string instead, never as an exception — see its docstring.
                metadata_warning = self._storage.copy(path, destination)
            except OSError as exc:
                # `_resolve_destination` above only ever returns a `destination`
                # that was proven not to exist yet (its own collision loop only
                # exits once `exists(candidate)` is False) — so any file found
                # here was necessarily just (partially) written by this failed
                # copy attempt, never something that pre-dates this run. Safe
                # to remove unconditionally; a no-op if copy() never got far
                # enough to create anything.
                self._storage.remove(destination)
                if _is_enospc(exc):
                    raise DestinationFullError(_DESTINATION_FULL_DURING_RUN_MESSAGE) from exc
                outcome = AssetOutcome.FAILED_VERIFICATION
                error = str(exc)
            else:
                try:
                    destination_checksum = self._storage.checksum(destination)
                except OSError as exc:
                    # The copy itself succeeded — only reading it back for
                    # verification failed. That's not evidence the content is
                    # bad, so (unlike the copy() failure above) this file is
                    # never removed here.
                    outcome = AssetOutcome.FAILED_VERIFICATION
                    error = str(exc)
                else:
                    if destination_checksum != checksum:
                        outcome = AssetOutcome.FAILED_VERIFICATION
                        error = "Checksum van de kopie komt niet overeen met het origineel."
                    else:
                        try:
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
                                    media_type=media_type.value,
                                    manufacturer=classification.manufacturer,
                                    model=classification.model,
                                    source_relative_path=source_relative_path,
                                    classification_source=classification.classification_source.value,
                                    codec=technical_metadata.codec,
                                    width=technical_metadata.width,
                                    height=technical_metadata.height,
                                    frame_rate=technical_metadata.frame_rate,
                                    duration_seconds=technical_metadata.duration_seconds,
                                    has_video_stream=technical_metadata.has_video_stream,
                                    has_audio_stream=technical_metadata.has_audio_stream,
                                )
                            )
                        except OSError as exc:
                            # The destination FILE is complete and checksum-
                            # verified at this point — only the manifest write
                            # failed. Never delete a verified file over that;
                            # abort the whole run instead (same reasoning as
                            # `_log_or_raise`: a manifest write failure is an
                            # infrastructure problem, not a single bad asset).
                            if _is_enospc(exc):
                                raise DestinationFullError(
                                    _DESTINATION_FULL_DURING_RUN_MESSAGE
                                ) from exc
                            raise DestinationUnavailableError(
                                f"Kon het manifest niet bijwerken: {exc}"
                            ) from exc
                        outcome = AssetOutcome.COPIED

        _log_or_raise(
            logger,
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
            metadata_warning=metadata_warning,
            media_type=media_type.value,
            manufacturer=classification.manufacturer,
            model=classification.model,
            source_relative_path=source_relative_path,
            classification_source=classification.classification_source.value,
            codec=technical_metadata.codec,
            width=technical_metadata.width,
            height=technical_metadata.height,
            frame_rate=technical_metadata.frame_rate,
            duration_seconds=technical_metadata.duration_seconds,
            has_video_stream=technical_metadata.has_video_stream,
            has_audio_stream=technical_metadata.has_audio_stream,
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
            metadata_warning=metadata_warning,
            manufacturer=classification.manufacturer,
            model=classification.model,
            media_type=media_type,
            source_relative_path=source_relative_path,
            classification_source=classification.classification_source,
            codec=technical_metadata.codec,
            width=technical_metadata.width,
            height=technical_metadata.height,
            frame_rate=technical_metadata.frame_rate,
            duration_seconds=technical_metadata.duration_seconds,
            has_video_stream=technical_metadata.has_video_stream,
            has_audio_stream=technical_metadata.has_audio_stream,
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


def _ensure_destination_is_writable(storage_root: Path) -> None:
    """Fails fast, before scanning/logging/anything else, if the destination
    isn't reachable — same `mkdir(parents=True, exist_ok=True)` technique
    already used elsewhere in this codebase (ActionLogger, LocalFilesystemStorage.copy)
    to prove writability, just done explicitly and early instead of being
    discovered accidentally deep in some other step."""
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DestinationUnavailableError(
            f"Kan niet schrijven naar de bestemming ({storage_root}): {exc}"
        ) from exc


# Kleine, expliciete reserve bovenop de daadwerkelijk geschatte ingest-grootte
# (zie _estimate_total_bytes) — vast, niet proportioneel: "klein en
# expliciet", geen ingewikkelde heuristiek. Dekt (a) de eigen, niet-nul writes
# van een run zelf (het actielog en het JSON-manifest groeien mee met elk
# bestand), en (b) dat elk bestandssysteem — exFAT is het formaat van elke
# externe ManyFast-schijf, zie device_identity.py — clusterruimte per bestand
# reserveert die nooit exact overeenkomt met de bronbestandsgrootte. Gevonden
# n.a.v. een echte handmatige Fase 4-test (2026-09-15): de bestemmingsschijf
# liep tijdens een echte run tot 0 bytes vrij leeg, wat via een niet-
# afgevangen OSError ten onrechte als "bron onleesbaar" werd gepresenteerd —
# zie het forensische auditrapport van die dag voor de volledige analyse.
_DESTINATION_FREE_SPACE_MARGIN_BYTES = 500 * 1024 * 1024  # 500 MiB

_DESTINATION_FULL_DURING_RUN_MESSAGE = (
    "De bestemmingsschijf is tijdens het kopiëren vol geraakt. Maak ruimte "
    "vrij op de bestemmingsschijf en probeer het opnieuw."
)


def _is_enospc(exc: OSError) -> bool:
    return exc.errno == errno.ENOSPC


def _format_bytes_for_message(num_bytes: int) -> str:
    """Same B/KB/MB/GB/TB convention as core/report.py's own `_format_size` —
    kept as a small, deliberate local copy (same established reason
    desktop/volumes.py's `format_size` already is one) purely so this module
    never has to import the reporting layer just for one error message; the
    reverse import (report.py importing from here) already exists, so the
    other direction would be circular."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _estimate_total_bytes(files: list[Path]) -> int:
    """Best-effort sum of every source file's current size — used only for
    the capacity preflight below, never for progress/reporting (that's
    `bytes_processed`'s job in `run()`, built up as files are actually
    processed). An individual `stat()` failure is skipped, not fatal — same
    "best-effort, not critical" stance `run()`'s own progress-byte tally
    already takes for the exact same call."""
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _ensure_destination_has_capacity(storage: Storage, storage_root: Path, files: list[Path]) -> None:
    """Preflight capacity check for a REAL run, before the first copy starts
    (see `IngestService.run()`). Never raised for a dry-run — a preview only
    reads, it must never need to know how much free space the destination
    has, same principle as `_ensure_destination_is_writable`."""
    required = _estimate_total_bytes(files) + _DESTINATION_FREE_SPACE_MARGIN_BYTES
    available = storage.free_bytes(storage_root)
    if available < required:
        raise DestinationFullError(
            "Onvoldoende ruimte op de bestemmingsschijf.\n"
            f"Benodigd: {_format_bytes_for_message(required)}\n"
            f"Beschikbaar: {_format_bytes_for_message(available)}"
        )


def _log_or_raise(logger: ActionLogger, event: str, **fields: object) -> None:
    """Wraps `ActionLogger.log()` so an `OSError` while writing the action
    log itself (e.g. the destination filled up, or was yanked mid-run) is
    classified and raised as a semantic error — never left to escape
    unclassified and be mislabeled as a source problem by a caller's broad
    `except OSError` (see this module's docstring, and the 2026-09-15
    forensic audit)."""
    try:
        logger.log(event, **fields)
    except OSError as exc:
        if _is_enospc(exc):
            raise DestinationFullError(_DESTINATION_FULL_DURING_RUN_MESSAGE) from exc
        raise DestinationUnavailableError(
            f"Kon niet naar het actielogboek schrijven: {exc}"
        ) from exc


def _with_suffix(path: Path, index: int) -> Path:
    return path.with_name(f"{path.stem}_{index:03d}{path.suffix}")


def _resolve_source_relative_path(path: Path, source: Path) -> Path | None:
    """Fase 5.0 — computed once here, the sole writer of `source_relative_path`
    (see AssetResult/AssetRecord). Stored only, never read back by any
    classification/destination/dedupe logic in this phase — see the Fase 5.0
    domain-model design. `list_files(source)` only ever yields descendants of
    `source`, so `relative_to()` failing here should not happen in practice;
    it's guarded anyway (e.g. a symlink could theoretically break the
    ancestor relationship) so a cosmetic provenance field can never abort or
    fail an asset — `None` is a safe, honest "couldn't determine this"."""
    try:
        return path.relative_to(source)
    except ValueError:
        return None


@dataclasses.dataclass(frozen=True)
class _TechnicalMetadata:
    """Fase 5.1 — normalized technical metadata (layer B, see the Fase 5.1
    design: raw ffprobe stays in ProbeResult, this is ManyOS's own stable
    subset of it). Purely a grouping convenience for _process_asset(); never
    persisted as a nested object — AssetResult/AssetRecord keep these as flat
    sibling fields, same shape as Fase 5.0."""

    codec: str | None
    width: int | None
    height: int | None
    frame_rate: str | None
    duration_seconds: float | None
    has_video_stream: bool | None
    has_audio_stream: bool | None


_MISSING_TECHNICAL_METADATA = _TechnicalMetadata(
    codec=None,
    width=None,
    height=None,
    frame_rate=None,
    duration_seconds=None,
    has_video_stream=None,
    has_audio_stream=None,
)


def _resolve_technical_metadata(probe_result: ProbeResult | None) -> _TechnicalMetadata:
    """Fase 5.1 — the sole writer of the technical-metadata fields on
    AssetResult/AssetRecord. `None` on every field when `probe_result` is
    `None` (ffprobe missing or failed for this file) — never a reason to
    fail the asset itself (see `safe_probe()`). Deliberately distinct from
    `False` on has_video_stream/has_audio_stream, which means ffprobe ran
    and definitively found no such stream — the two must never be conflated."""
    if probe_result is None:
        return _MISSING_TECHNICAL_METADATA
    return _TechnicalMetadata(
        codec=probe_result.codec,
        width=probe_result.width,
        height=probe_result.height,
        frame_rate=_normalize_frame_rate(probe_result.frame_rate),
        duration_seconds=probe_result.duration_seconds,
        has_video_stream=probe_result.has_video_stream,
        has_audio_stream=probe_result.has_audio_stream,
    )


def _normalize_frame_rate(raw: str | None) -> str | None:
    """Fase 5.1 — validates ProbeResult's raw rational frame-rate string
    (e.g. "30000/1001") and returns it UNCHANGED when usable — the exact
    rational value is the persisted source of truth (never a float; a future
    UI derives a display value like 29.97 from this if it wants one). Only
    genuinely unusable input becomes `None`: missing, empty, malformed, or a
    zero/negative numerator or denominator (e.g. "0/0")."""
    if not raw:
        return None
    parts = raw.split("/")
    if len(parts) != 2:
        return None
    try:
        numerator, denominator = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if numerator <= 0 or denominator <= 0:
        return None
    return raw


def _resolve_recording_date(path: Path, probe_result: ProbeResult | None) -> dt.date:
    if probe_result is not None and probe_result.creation_time:
        try:
            return dt.datetime.fromisoformat(
                probe_result.creation_time.replace("Z", "+00:00")
            ).date()
        except ValueError:
            pass
    return dt.date.fromtimestamp(path.stat().st_mtime)


def _project_workspace_path(storage_root: Path, client: str, project: str) -> Path:
    return storage_root / CLIENT_FOLDER_NAME / client / project


def _build_workspace_path(
    storage_root: Path,
    client: str,
    project: str,
    recording_date: dt.date,
    category: str,
    filename: str,
) -> Path:
    return (
        _project_workspace_path(storage_root, client, project)
        / f"{recording_date.isoformat()}_Raw"
        / category
        / filename
    )

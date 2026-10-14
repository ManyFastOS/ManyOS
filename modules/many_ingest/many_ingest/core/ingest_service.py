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
from many_ingest.device_identity import same_physical_device
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
from many_ingest.sidecar_relationship import (
    RelationshipEvidence,
    find_sidecar_candidates,
    parse_sony_sidecar,
    resolve_relationship,
)

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

_CLIENT_FIELD_LABEL = "Klantnaam"
_PROJECT_FIELD_LABEL = "Projectnaam"


class InvalidWorkspaceNameError(Exception):
    """Raised by `validate_workspace_name()` when `client` or `project`
    cannot safely form a Project Workspace path segment
    (`Klanten/{client}/{project}`, see `_project_workspace_path()`). Checked
    as the very first thing `run()` does — before ffprobe, scanning, or any
    manifest/destination access — identically for a dry-run and a real run
    (Fase 5.3.2: a read-only preview still turns `client`/`project` into
    path segments it reads from, so it needs the exact same guarantee a
    real run does)."""


def validate_workspace_name(value: str, field_label: str) -> None:
    """The one validation `client`/`project` names go through — called by
    `IngestService.run()` (the real safety boundary, mandatory regardless of
    caller) and reused as-is by the desktop GUI for Preview-button gating
    (`main_window.py`, via `is_valid_workspace_name()` below) — no second
    implementation (Fase 5.3.2).

    Deliberately conservative: rejects anything that could change which
    directory `_project_workspace_path()`/`_build_workspace_path()` resolve
    to (a path separator, `.`, `..`, an absolute path) or that cannot safely
    round-trip through a filesystem path / JSON manifest / log line (`\\0`
    and other control characters), plus leading/trailing whitespace —
    silently stripping that would itself be the kind of silent, surprising
    normalization this function must never do (the caller always learns
    exactly why a name was rejected, never ends up with a different name
    than what they typed). Ordinary punctuation, accented characters,
    digits, and spaces *between* words are all unaffected — see
    `tests/many_ingest/test_ingest_service.py` for the concrete names this
    must keep accepting (e.g. "Van der Panne Makelaardij", "Project 2026 -
    Düsseldorf", "Commercial & Social")."""
    if not value or not value.strip():
        raise InvalidWorkspaceNameError(f"{field_label} mag niet leeg zijn.")
    if value != value.strip():
        raise InvalidWorkspaceNameError(
            f"{field_label} mag geen spaties aan het begin of einde bevatten."
        )
    if value in (".", ".."):
        raise InvalidWorkspaceNameError(f"{field_label} mag niet '.' of '..' zijn.")
    if "/" in value or "\\" in value:
        raise InvalidWorkspaceNameError(
            f"{field_label} mag geen '/' of '\\' bevatten (geen padscheidingstekens)."
        )
    if Path(value).is_absolute():
        raise InvalidWorkspaceNameError(f"{field_label} mag geen absoluut pad zijn.")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise InvalidWorkspaceNameError(
            f"{field_label} bevat een onzichtbaar of ongeldig teken en kan niet worden gebruikt."
        )


def is_valid_workspace_name(value: str) -> bool:
    """Bool convenience wrapper around `validate_workspace_name()` — for UI
    gating only (main_window.py's Preview-button enablement). Never a
    substitute for the real check: `IngestService.run()` always calls
    `validate_workspace_name()` itself and never trusts that a caller's UI
    already validated anything (the same defense-in-depth split
    `eject.py`'s `can_eject()`/`validate_eject()` already uses)."""
    try:
        validate_workspace_name(value, "")
    except InvalidWorkspaceNameError:
        return False
    return True


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


class DestinationEscapesWorkspaceError(Exception):
    """Raised when a computed destination path — the Project Workspace root
    itself (checked once in `run()`), or a specific asset's full destination
    within it (checked in `_process_asset()`) — does not stay inside its
    expected boundary once symlinks are resolved (Fase 5.3.2 — defense in
    depth, see `_verify_within_boundary()`). A safe `client`/`project`
    *string* (see `validate_workspace_name()`) is not enough on its own: a
    pre-existing symlink already sitting on disk under `storage_root`
    (`Klanten`, a client folder, a project folder, or anything deeper)
    could still redirect a perfectly ordinary name outside the workspace.

    Same severity class as `DestinationUnavailableError`/
    `DestinationFullError` — aborts the whole run, never downgraded to a
    single asset's `FAILED_VERIFICATION` — because the cause is an
    infrastructure-level safety violation (something is wrong with the
    destination disk's own layout), not a problem with one file.

    TOCTOU: proving containment and the filesystem call it guards (mkdir,
    copy, or the `exists`/`checksum` calls `_resolve_destination` makes for
    a collision check) are two separate steps, not one atomic operation — a
    symlink swapped into place in the brief window between them would not
    be caught. v0.1's threat model is a single operator running one ingest
    at a time against a destination disk only that operator controls; this
    gap is accepted and documented here rather than silently assumed closed.
    Closing it fully would need atomic, symlink-aware filesystem primitives
    (e.g. `O_NOFOLLOW`/`openat`-relative opens) that Python's `pathlib`/
    `shutil` do not portably expose — out of scope for this phase."""


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
    # Fase 5.2 — additief, same rationale as the blocks above. Populated only
    # when sidecar_relationship.resolve_relationship() actually found
    # evidence for this file (a sidecar); every non-sidecar asset (and every
    # sidecar without proven evidence) keeps sidecar_of_asset_id=None,
    # relationship_evidence=NONE — see sidecar_relationship.py's module
    # docstring for why this is evidence-driven, never camera-model-driven.
    sidecar_of_asset_id: str | None = None
    relationship_evidence: RelationshipEvidence = RelationshipEvidence.NONE


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
        # Fase 5.3.2 — the very first thing run() does, before ffprobe,
        # scanning, or any destination access, identical for dry_run=True
        # and dry_run=False (see validate_workspace_name()'s docstring):
        # client/project become path segments in _project_workspace_path()
        # below, so an unsafe name must never reach any filesystem call, not
        # even a read-only one.
        validate_workspace_name(client, _CLIENT_FIELD_LABEL)
        validate_workspace_name(project, _PROJECT_FIELD_LABEL)

        if not is_ffprobe_available():
            # Never proceed without metadata capability — a run without ffprobe would
            # silently degrade every asset to "Onbekend" instead of failing loudly.
            raise FfprobeNotFoundError(FFPROBE_INSTALL_MESSAGE)

        # Fase 5.3.2 — defense-in-depth, independent of the name validation
        # above: even a perfectly safe client/project STRING can resolve
        # outside storage_root if something already on disk (Klanten/, or
        # the client/project directory itself) is already a symlink
        # pointing elsewhere — planted by an earlier run, another process,
        # or deliberately. Checked here for both modes (same reasoning as
        # the name validation above), so every subsequent read/write below
        # — including the read-only collision checks a dry-run performs —
        # is proven safe before it happens, not caught after the fact.
        # _process_asset() independently re-does this exact same check for
        # every asset (never trusting that this one, up-front call is still
        # valid later in a long run) — see _verified_project_workspace_root()'s
        # docstring for why re-verifying on every use, not just once here,
        # matters.
        _verified_project_workspace_root(self._config.storage_root, client, project)

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

        # Fase 5.2 — resolved once, up front, over the whole file list: a
        # sidecar can appear before or after its main media in
        # scan_result.files (arbitrary rglob order), and resolution here
        # only ever reads each candidate file directly (XML text, ffprobe),
        # never depends on another file having been "processed" yet — so
        # the result is identical regardless of scan order. See
        # sidecar_relationship.py's module docstring for the evidence rules.
        # Fase 5.3 adds a second, equally order-independent return value
        # (`sidecar_placements`) — see _resolve_sidecar_relationships()'s
        # docstring and _process_asset()'s Fase 5.3 comment.
        resolved_sidecars, sidecar_placements = _resolve_sidecar_relationships(
            scan_result.files, scan_result.source, self._camera_profiles
        )
        # Fase 5.3.1A — stable partition: every non-sidecar file (including
        # every proven sidecar's own main media) is processed before ANY
        # proven sidecar, with the original relative scan order preserved
        # within each group. This guarantees that by the time a sidecar's
        # own turn comes up, its main media's outcome for THIS run (if that
        # main media is part of this same scan) is already final — so the
        # referential-integrity check in _process_asset() below
        # (`self._manifest.is_duplicate(main_media_checksum)`) can never see
        # a false negative caused purely by scan order ("XML before its
        # video"). This never changes classification, checksums, dedupe, or
        # camera-aware placement — purely an iteration-order change over the
        # exact same file list.
        ordered_files = [p for p in scan_result.files if p not in resolved_sidecars] + [
            p for p in scan_result.files if p in resolved_sidecars
        ]
        # Run-scoped only: guarantees a main-media file referenced by a
        # sidecar is never checksummed twice (once for its own asset, once
        # for the sidecar's sidecar_of_asset_id) regardless of which one
        # this loop reaches first — see _cached_checksum().
        checksum_cache: dict[Path, str] = {}
        # Fase 5.3.1B — run-scoped only, same reasoning as checksum_cache:
        # memoizes the verified-existing-destination decision per main-media
        # checksum (not per raw candidate path), so N sidecars proven against
        # the same main media verify (and, critically, re-hash the
        # destination file) at most once per run — see
        # _verified_existing_destination(). Never shared across separate
        # run() calls (a fresh dict every time), so a destination file
        # modified/removed between two runs is always re-verified, never
        # trusted from a stale cache.
        destination_verification_cache: dict[str, Path | None] = {}

        total = len(ordered_files)
        assets: list[AssetResult] = []
        bytes_processed = 0

        for index, path in enumerate(ordered_files, start=1):
            asset = self._process_asset(
                path,
                scan_result.source,
                client,
                project,
                run_id,
                dry_run,
                logger,
                resolved_sidecars,
                sidecar_placements,
                checksum_cache,
                destination_verification_cache,
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
        resolved_sidecars: dict[Path, tuple[Path, RelationshipEvidence]],
        sidecar_placements: dict[Path, _InheritedPlacement],
        checksum_cache: dict[Path, str],
        destination_verification_cache: dict[str, Path | None],
    ) -> AssetResult:
        probe_result = safe_probe(path)
        file_type = detect_file_type(path, probe_result)
        media_type = detect_media_type(path, probe_result)
        classification = classify(path, probe_result, self._camera_profiles)
        source_relative_path = _resolve_source_relative_path(path, source)
        technical_metadata = _resolve_technical_metadata(probe_result)
        recording_date = _resolve_recording_date(path, probe_result)
        checksum = _cached_checksum(self._storage, checksum_cache, path)
        is_duplicate = self._manifest.is_duplicate(checksum)

        # Fase 5.2 — populated only when resolve_relationship() already
        # found evidence for this exact file (see run()); nothing here
        # changes checksum/dedupe semantics.
        #
        # Fase 5.3.1A — `relationship_evidence` and `sidecar_of_asset_id` are
        # two deliberately DIFFERENT claims, never conflated:
        #   - `relationship_evidence` is the proven CONTENT relationship
        #     between the two source files (UMID/duration match) — always
        #     set when resolve_relationship() found evidence, regardless of
        #     anything that happens to either file afterwards.
        #   - `sidecar_of_asset_id` is a MANIFEST relationship: it is only
        #     ever set to the main media's checksum when a record for that
        #     checksum actually exists in the manifest at this exact moment
        #     (checked via the existing `Manifest.is_duplicate()` — the same
        #     primitive `is_duplicate` above already uses, never a new
        #     manifest method). `run()`'s stable sidecar-last partitioning
        #     guarantees that if the main media is part of this same scan,
        #     its outcome (COPIED and registered, confirmed DUPLICATE_SKIPPED
        #     against a pre-existing record, or FAILED_VERIFICATION/never
        #     reached) is already final by the time we get here — so this
        #     check can never give a false negative purely due to scan
        #     order. It also naturally keeps dry-run honest: a dry-run never
        #     calls `register()`, so this only reports a manifest relation
        #     that genuinely, already exists on disk — never one that would
        #     only become true if the main media were actually copied.
        #     A missing/failed main media leaves `sidecar_of_asset_id=None`
        #     while `relationship_evidence` stays exactly as proven — never
        #     a dangling reference, never a lost/altered content relation.
        sidecar_of_asset_id: str | None = None
        relationship_evidence = RelationshipEvidence.NONE
        resolved = resolved_sidecars.get(path)
        if resolved is not None:
            main_media_path, relationship_evidence = resolved
            main_media_checksum = _cached_checksum(self._storage, checksum_cache, main_media_path)
            if self._manifest.is_duplicate(main_media_checksum):
                sidecar_of_asset_id = main_media_checksum

        # Fase 5.3.2 — computed here, ONCE per asset, before the Fase 5.3.1B
        # sidecar co-location lookup immediately below: re-derives AND
        # re-verifies the full chain (storage_root -> Project Workspace
        # root, via _verified_project_workspace_root() — never just reuses
        # a value computed earlier in run(), see that function's docstring
        # for why re-verifying on every use, not just once per run,
        # matters). Deliberately computed BEFORE _verified_existing_destination()
        # is called, not after: that function does its OWN internal
        # `allowed_root.resolve()` + containment check against whatever
        # `allowed_root` it is given, but never itself re-verifies that
        # `allowed_root` is still inside storage_root — only the CALLER can
        # prove that. Passing an already fully-verified, resolved root in
        # here (rather than the raw, unresolved `_project_workspace_path()`
        # a prior version passed) closes that gap without touching
        # `_verified_existing_destination()`'s own logic at all: a 5.3.1B
        # candidate is now checked against a boundary already proven safe,
        # never against one a mid-run symlink swap could have compromised
        # in between (see
        # test_symlink_swapped_in_mid_run_is_caught_on_the_next_file_not_silently_trusted).
        project_workspace_root_resolved = _verified_project_workspace_root(
            self._config.storage_root, client, project
        )

        # Fase 5.3.1B — when sidecar_of_asset_id is set (a CONFIRMED manifest
        # relationship, per Fase 5.3.1A directly above), try to physically
        # co-locate this sidecar with its main media's ACTUAL, already-
        # registered destination — covers a duplicate sitting in either a
        # current camera-aware folder or an old, flat pre-Fase-5.3 folder,
        # as long as it's inside THIS run's own Project Workspace
        # (Klanten/{client}/{project}/, via the already-verified
        # project_workspace_root_resolved above — never the whole
        # storage_root: a main media registered for a different client/
        # project must never decide where this run's sidecar physically
        # lands, however identical its checksum happens to be). Deliberately
        # gated on `sidecar_of_asset_id`, never attempted when it's `None`
        # (FAILED_VERIFICATION / never-reached main media this run) — this
        # reuses the 5.3.1A guarantee as its own precondition rather than
        # re-deriving it, so 5.3.1B can never weaken it. A verification
        # failure here (missing file, changed content, unsafe path, wrong
        # project) is never fatal — `_verified_existing_destination()` only
        # ever returns `None` on failure, falling through to the unchanged
        # Fase 5.3 camera-aware computation below.
        verified_existing_destination: Path | None = None
        if sidecar_of_asset_id is not None:
            verified_existing_destination = _verified_existing_destination(
                self._storage,
                self._manifest,
                project_workspace_root_resolved,
                sidecar_of_asset_id,
                destination_verification_cache,
            )

        # Fase 5.3 — a proven sidecar inherits ONLY its main media's placement
        # (category/camera_profile/recording_date) for destination-path
        # purposes, never its own classification: `classification` above
        # (and everything derived from it — AssetResult/AssetRecord's
        # category/camera_profile/confidence/manufacturer/model/
        # classification_source) stays the sidecar's own honest result,
        # unaffected by this. An unresolved sidecar, or any other ordinary
        # asset (`resolved is None`), keeps its own classification/date
        # exactly as before Fase 5.3 — still Onbekend/ for an unlinked XML.
        # `sidecar_placements` always has an entry for `main_media_path` here
        # (populated unconditionally alongside `resolved_sidecars` in
        # _resolve_sidecar_relationships(), in the same branch) — a plain
        # index lookup rather than `.get()`, so a broken invariant fails
        # loudly instead of silently misfiling the sidecar.
        placement = sidecar_placements[main_media_path] if resolved is not None else None
        destination_category = placement.category if placement is not None else classification.category
        destination_camera_profile = (
            placement.camera_profile if placement is not None else classification.camera_profile
        )
        destination_recording_date = (
            placement.recording_date if placement is not None else recording_date
        )
        # Fase 5.3.1B — a verified existing destination (see above) wins
        # over the freshly computed camera-aware path: `_resolve_destination`
        # below still handles the final filename and any collision suffix
        # exactly as before, this only changes WHICH FOLDER that happens in.
        if verified_existing_destination is not None:
            naive_destination = verified_existing_destination.parent / path.name
        else:
            naive_destination = _build_workspace_path(
                storage_root=self._config.storage_root,
                client=client,
                project=project,
                recording_date=destination_recording_date,
                category=destination_category,
                camera_profile=destination_camera_profile,
                filename=path.name,
            )

        # Fase 5.3.2 follow-up review, finding 1 — two different cases,
        # deliberately handled differently (see
        # DestinationEscapesWorkspaceError's docstring):
        #
        # - ORDINARY placement (verified_existing_destination is None):
        #   naive_destination was just built from a FIXED FORMULA
        #   (_build_workspace_path(): date/category/[camera_profile/]
        #   filename, under the Project Workspace root) — so it is
        #   possible, and required, to verify that RESOLVING it preserves
        #   that EXACT relative structure, not merely that it lands
        #   "somewhere" inside storage_root. A symlink redirecting to a
        #   different, otherwise entirely legitimate client's/project's/
        #   date's/category's/camera-profile's folder — still fully
        #   "inside storage_root" — is caught here; a prior version's bare
        #   containment check missed exactly this (see
        #   _verify_within_boundary()'s docstring). Deliberately
        #   RE-VERIFIED here (not the project_workspace_root_resolved
        #   computed earlier, above the sidecar lookup) to keep the TOCTOU
        #   window right before the collision-check/copy operations that
        #   follow as small as possible.
        # - SIDECAR co-location (verified_existing_destination is not
        #   None): it is a RESOLVED, already fully-verified path returned
        #   by _verified_existing_destination() (Fase 5.3.1B, unchanged) —
        #   which may legitimately be an old, flat, pre-Fase-5.3 folder
        #   with no fixed structure at all. There is no "expected relative
        #   formula" to compare against here, and there must never be
        #   one: forcing a legacy folder to match the current camera-aware
        #   shape would be exactly the unwanted migration this phase must
        #   not introduce. Its directory is already proven safe by
        #   5.3.1B's own check (_is_within(), against the same, now
        #   correctly-fenced project_workspace_root_resolved computed
        #   above) — reused as-is, no further check needed.
        #
        # Either way, the result is `destination_dir_resolved`: the
        # already-verified, resolved DIRECTORY `_resolve_destination()`
        # below checks every collision candidate's leaf against (Fase
        # 5.3.2 follow-up review, finding about collision resolution).
        if verified_existing_destination is not None:
            destination_dir_resolved = verified_existing_destination.parent
        else:
            project_workspace_root = _project_workspace_path(
                self._config.storage_root, client, project
            )
            project_workspace_root_resolved = _verified_project_workspace_root(
                self._config.storage_root, client, project
            )
            resolved_naive_destination = _verify_within_boundary(
                naive_destination,
                project_workspace_root,
                project_workspace_root_resolved,
                candidate_label="Het doelpad",
                boundary_label="de Project Workspace-map",
            )
            destination_dir_resolved = resolved_naive_destination.parent

        destination = naive_destination
        name_conflict_resolved = False
        error: str | None = None
        metadata_warning: str | None = None

        if not is_duplicate:
            # Collision check runs in dry-run too (read-only: exists + checksum of
            # what's already there) so the preview reflects reality — only the
            # actual copy/register below is gated behind dry_run.
            try:
                destination, is_duplicate = self._resolve_destination(
                    checksum, naive_destination, destination_dir_resolved
                )
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
                                    sidecar_of_asset_id=sidecar_of_asset_id,
                                    relationship_evidence=relationship_evidence.value,
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
            sidecar_of_asset_id=sidecar_of_asset_id,
            relationship_evidence=relationship_evidence.value,
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
            sidecar_of_asset_id=sidecar_of_asset_id,
            relationship_evidence=relationship_evidence,
        )

    def _resolve_destination(
        self, source_checksum: str, destination: Path, destination_dir_resolved: Path
    ) -> tuple[Path, bool]:
        """Never overwrites an existing file.

        Returns (final_destination, is_duplicate). If `destination` is already
        occupied by a file with the same checksum, that's a duplicate — no new
        path is needed. If it's occupied by different content, an automatic
        `_001`, `_002`, ... suffix is appended until a free (or checksum-matching)
        name is found.

        Fase 5.3.2 — every candidate this loop considers (the bare
        `destination`, then each `_001`, `_002`, ... suffix) has its LEAF
        verified *before* the `exists()`/`checksum()` calls that follow for
        it: resolving the candidate (following a symlink the leaf itself
        might be) must land it inside `destination_dir_resolved` — the
        already-verified, resolved DIRECTORY the caller computed once,
        before this loop starts (see `_process_asset()`'s Fase 5.3.2
        block). Every candidate here shares that exact same directory by
        construction (`_with_suffix()` only ever changes the filename's
        stem, never the parent) — so this is deliberately a simpler,
        cheaper check than the caller's own
        (`_verify_within_boundary()`'s exact-relative-structure
        comparison against a distant ancestor): it only needs to confirm
        the leaf's resolution didn't silently move it to a *different*
        directory, never re-derive or re-compare an expected relative path
        from scratch. A suffix candidate is a genuinely different path
        object; it could already exist on disk as a symlink planted
        outside the workspace even when the un-suffixed `destination` is
        perfectly ordinary — checking only once, up front, would leave
        `self._storage.checksum(candidate)` free to read an arbitrary file
        outside the workspace for every later candidate. Raises
        `DestinationEscapesWorkspaceError` (not caught here, not an
        `OSError`, so it is never mistaken by the caller for an ordinary
        collision-check I/O failure) the moment any candidate fails this.
        """
        candidate = destination
        index = 0
        while True:
            resolved_candidate = _resolve_boundary(candidate, "Het doelpad")
            if resolved_candidate.parent != destination_dir_resolved:
                raise DestinationEscapesWorkspaceError(
                    f"Het doelpad ({resolved_candidate}) ligt niet in de verwachte "
                    f"map ({destination_dir_resolved}) — mogelijk een symlink die "
                    f"naar een andere locatie leidt. {_SAFE_STOP_MESSAGE}"
                )
            if not self._storage.exists(candidate):
                return candidate, False
            if self._storage.checksum(candidate) == source_checksum:
                return candidate, True
            index += 1
            if index > _MAX_COLLISION_ATTEMPTS:
                raise RuntimeError(
                    f"Te veel naamconflicten voor {destination} (>{_MAX_COLLISION_ATTEMPTS})."
                )
            candidate = _with_suffix(destination, index)


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


def _cached_checksum(storage: Storage, cache: dict[Path, str], path: Path) -> str:
    """Fase 5.2 — run-scoped cache (see run()). Guarantees a main-media file
    referenced by a sidecar's sidecar_of_asset_id is never checksummed twice
    within one run: once when the main media reaches its own turn in the
    per-file loop, once when a sidecar (processed before or after it,
    scan-order independent) needs the exact same checksum. Every checksum
    computation in _process_asset() goes through this, not just
    sidecar-related ones, so double-hashing can never happen in either
    direction."""
    cached = cache.get(path)
    if cached is not None:
        return cached
    checksum = storage.checksum(path)
    cache[path] = checksum
    return checksum


def _verified_existing_destination(
    storage: Storage,
    manifest: Manifest,
    allowed_root: Path,
    checksum: str,
    cache: dict[str, Path | None],
) -> Path | None:
    """Fase 5.3.1B — the ONLY place that ever decides a manifest-recorded
    destination_path is safe to physically co-locate a sidecar next to.
    Returns the main media's real destination file path (never its parent —
    the caller takes `.parent` itself), or `None` if no candidate from
    `manifest.find_destinations(checksum)` passes every check below.

    `allowed_root` MUST be the CURRENT run's own Project Workspace
    (`_project_workspace_path(storage_root, client, project)` —
    `Klanten/{client}/{project}/`), never the bare `storage_root`. A main
    media file registered for a DIFFERENT client/project can have the exact
    same checksum (e.g. identical footage re-delivered, or simply identical
    bytes) without that ever being a reason to let it decide where this
    run's sidecar physically lands — that would leak a file across client/
    project boundaries. Restricting to the current Project Workspace still
    allows co-locating with an older, flat pre-Fase-5.3 folder for the SAME
    client/project (the actual Fase 5.3.1B goal), since that folder is a
    descendant of the same Project Workspace regardless of its internal
    date/category/camera-profile subfolder shape.

    Never raises: every fallible call below (path resolution, the manifest
    lookup itself, the destination checksum) is caught and treated as "this
    candidate doesn't verify" / "no candidates available", never as a
    reason to abort the run over what is strictly an optional placement
    optimization. This is a DELIBERATELY different failure policy than
    `Manifest.is_duplicate()` elsewhere in `_process_asset()`: a corrupted/
    unreadable manifest file still makes `is_duplicate()` raise and abort
    the whole run unchanged — deduplication is not optional, so that
    existing fail-loud behaviour is correct and is NOT touched here. This
    function only ever softens failures for its OWN, optional lookup.

    Run-scoped cache, keyed by checksum (not by individual candidate path):
    memoizes the FINAL decision for a given main-media checksum, so N
    sidecars proven against the same main media (Fase 5.3.1A) verify, and
    critically re-hash the destination file, at most once per run,
    regardless of how many raw candidates `find_destinations()` returned.
    `cache` is always a fresh dict created per `run()` call (see run()) —
    never shared across separate runs, so a destination file that changes
    or disappears between two runs is always re-verified from scratch, never
    trusted from a stale decision. `allowed_root` is constant for the whole
    lifetime of one `run()` call (client/project never change mid-run), so
    keying the cache by checksum alone — without also keying by
    `allowed_root` — stays correct.

    Verification order, each a precondition for the next:
    1. `candidate.resolve()` — follows symlinks to the real, final path;
       a candidate that can't be resolved at all (including a circular-
       symlink `RuntimeError`, not just `OSError`/`ValueError`) is skipped,
       not fatal.
    2. The resolved path must be inside `allowed_root` (also resolved) —
       blocks path traversal, a symlink that textually looks like it's
       under `allowed_root` but actually escapes it, AND a different
       client/project's registered destination.
    3. `same_physical_device()` (device_identity.py, `st_dev` — never a
       path-string/volume-name comparison) — blocks a swapped volume that
       happens to be mounted at a path matching an old record.
    4. `resolved.is_file()` — must be an existing, regular file (not a
       directory, not a dangling special file).
    5. `storage.checksum(resolved) == checksum` — the content must still be
       exactly what the manifest recorded; a changed or corrupted file never
       counts as a match, however close its path looks.

    Multiple candidates (e.g. a hand-edited/merged manifest with more than
    one record for the same checksum — `find_destinations()` never hides
    this) are tried in the order returned; the first one that passes every
    check wins. If none do, this returns `None` and the caller falls back to
    the ordinary, freshly computed Fase 5.3 camera-aware destination."""
    if checksum in cache:
        return cache[checksum]

    try:
        allowed_root_resolved = allowed_root.resolve()
    except (OSError, ValueError, RuntimeError):
        # Can't even resolve the allowed root itself (e.g. the destination
        # disk vanished mid-run, or a symlink loop in its own ancestry) — no
        # candidate could ever be proven safe against it; fall back, never
        # abort a run over this optional lookup.
        cache[checksum] = None
        return None

    try:
        candidates = manifest.find_destinations(checksum)
    except (OSError, ValueError, AttributeError, TypeError):
        # E.g. a corrupted/unreadable manifest JSON file raises
        # json.JSONDecodeError (a ValueError subclass) here. A manifest with
        # syntactically valid JSON but the wrong SHAPE (e.g. "assets" is a
        # string, null, or a list containing non-dict entries) raises
        # AttributeError/TypeError instead, from inside find_destinations()'s
        # own `.get()` calls — just as uncaught, so caught here too.
        # Deliberately NOT the same policy as `Manifest.is_duplicate()` —
        # see this function's docstring above.
        candidates = []

    verified: Path | None = None
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError, RuntimeError):
            continue
        if not _is_within(resolved, allowed_root_resolved):
            continue
        if not same_physical_device(resolved, allowed_root_resolved):
            continue
        try:
            if not resolved.is_file():
                continue
            if storage.checksum(resolved) != checksum:
                continue
        except OSError:
            continue
        verified = resolved
        break

    cache[checksum] = verified
    return verified


def _is_within(path: Path, root: Path) -> bool:
    """Fase 5.3.1B — both arguments are expected already-resolved (symlinks
    followed) by the caller; this is a pure, no-I/O ancestor check, never a
    second resolution pass."""
    return path.is_relative_to(root)


# Fase 5.3.2 follow-up review, finding 2 — a safety-check failure part-way
# through a run never implies that NOTHING happened: files already copied
# and checksum-verified earlier in the same run stay exactly where they
# are (the same "never roll back a verified file" stance
# DestinationFullError/DestinationUnavailableError already take). Every
# DestinationEscapesWorkspaceError message below ends with this, instead
# of a prior version's unconditional (and, after the first file, simply
# false) "er is niets geschreven" claim.
_SAFE_STOP_MESSAGE = (
    "De ingest is om veiligheidsredenen gestopt. Bestanden die eerder in "
    "deze run succesvol zijn gekopieerd en geverifieerd, blijven behouden. "
    "Controleer het rapport of actielogboek voor de status."
)


def _resolve_boundary(path: Path, label: str) -> Path:
    """Fase 5.3.2 — resolves `path` (symlinks followed for every existing
    ancestor; `strict=False` so a not-yet-created tail, e.g. a brand-new
    client/project folder on its very first ingest, is appended literally
    instead of raising) for use as a containment check's boundary or
    candidate (see `_verify_within_boundary()`). A resolve failure (a
    permission error walking an ancestor, a broken/circular symlink) means
    safety can never be proven here — raises
    `DestinationEscapesWorkspaceError` rather than silently falling back to
    the unresolved path, which would defeat the entire point of this
    check."""
    try:
        return path.resolve(strict=False)
    except (OSError, ValueError, RuntimeError) as exc:
        raise DestinationEscapesWorkspaceError(
            f"Kon {label} niet veilig herleiden (mogelijk een kapotte of "
            f"circulaire symlink): {exc}. {_SAFE_STOP_MESSAGE}"
        ) from exc


def _verify_within_boundary(
    candidate: Path,
    candidate_root: Path,
    boundary_resolved: Path,
    *,
    candidate_label: str,
    boundary_label: str,
) -> Path:
    """Fase 5.3.2's containment check, used by both `run()`/
    `_verified_project_workspace_root()` (the bare Project Workspace root
    against `storage_root`) and `_process_asset()`'s ordinary,
    formula-based placement (a specific destination against the Project
    Workspace root) — never duplicated as a second implementation.

    Fase 5.3.2 follow-up review, finding 1 — deliberately STRONGER than "is
    `candidate`, once resolved, merely a descendant of `boundary_resolved`"
    (which a prior version checked via `_is_within()` alone): `candidate`
    is always expected to already be a literal child of `candidate_root`
    (e.g. `storage_root/Klanten/{client}/{project}`, or
    `project_workspace_root/{date}_Raw/{category}/.../{filename}`) — this
    requires that RESOLVING `candidate` (following any symlink anywhere in
    its ancestry) leaves it at the EXACT SAME relative position under
    `boundary_resolved` that it held, unresolved, under `candidate_root`.
    A symlink that redirects to a DIFFERENT, otherwise entirely legitimate
    location still fully inside `boundary_resolved` — another client's or
    project's folder, or another date/category/camera-profile folder
    within the very same project — changes that relative position and is
    therefore still rejected. A bare "is it inside storage_root" check
    missed exactly this (empirically confirmed: a symlink from
    `Klanten/Nike/Zomer` to `Klanten/Adidas/Winter`, both genuinely inside
    the same `storage_root`, passed the old check without complaint and
    caused a real ingest to physically write Nike/Zomer footage into
    Adidas's folder).

    Legitimate case this still allows: `storage_root`/`destination_root`
    itself reachable via a symlink (an operator-configured setup, see
    `DestinationEscapesWorkspaceError`'s docstring) — since `candidate_root`
    and `boundary_resolved` are resolved through the exact same external
    symlink chain, the portion of the relative path INSIDE the Project
    Workspace (`Klanten/{client}/{project}/...`) is unaffected; only a
    symlink INSIDE that internal structure can ever produce a mismatch.

    Never substitutes a different path on failure: a candidate that fails
    this always raises `DestinationEscapesWorkspaceError` and aborts the
    run, never silently falls back to another folder or disk."""
    resolved_candidate = _resolve_boundary(candidate, candidate_label)
    expected_relative = candidate.relative_to(candidate_root)
    try:
        actual_relative = resolved_candidate.relative_to(boundary_resolved)
    except ValueError:
        actual_relative = None
    if actual_relative != expected_relative:
        raise DestinationEscapesWorkspaceError(
            f"{candidate_label} ({resolved_candidate}) komt niet overeen met "
            f"de verwachte locatie ({boundary_resolved / expected_relative}) "
            f"binnen {boundary_label} — mogelijk een symlink die naar een "
            "andere locatie leidt, ook als die zelf nog binnen de "
            f"bestemmingsroot ligt. {_SAFE_STOP_MESSAGE}"
        )
    return resolved_candidate


@dataclasses.dataclass(frozen=True)
class _InheritedPlacement:
    """Fase 5.3 — the subset of a main-media file's own placement a proven
    sidecar inherits for destination-path purposes ONLY — never its full
    classification (see _process_asset()'s Fase 5.3 comment: the sidecar's
    own AssetResult/AssetRecord category/camera_profile/confidence/
    manufacturer/model/classification_source are never touched by this).
    Computed once per main_media_path in _resolve_sidecar_relationships(),
    reusing the exact same safe_probe() result already fetched there for
    evidence resolution — never a second ffprobe call for the same file."""

    category: str
    camera_profile: str
    recording_date: dt.date


def _resolve_sidecar_relationships(
    files: list[Path], source: Path, camera_profiles: list[CameraProfile]
) -> tuple[dict[Path, tuple[Path, RelationshipEvidence]], dict[Path, _InheritedPlacement]]:
    """Fase 5.2 — resolved once, up front (see run()), over the whole scan
    result: candidate discovery (naming + same-directory only) and evidence
    resolution (UMID, then duration fallback) both read each candidate file
    directly and independently, so the result never depends on scan order.
    Only candidates with actual evidence (not RelationshipEvidence.NONE) are
    kept — everything else stays an ordinary, unrelated asset.

    Fase 5.3 — alongside that, for every main_media_path that has at least
    one proven sidecar, also resolves the _InheritedPlacement it hands down
    (see that class's docstring). Classifying main_media_path here is
    equally scan-order-independent (classify() is a pure function of the
    path + the probe result already fetched for evidence resolution), and
    is skipped if already resolved for a given main_media_path (more than
    one sidecar could in principle point at the same main media)."""
    resolved: dict[Path, tuple[Path, RelationshipEvidence]] = {}
    placements: dict[Path, _InheritedPlacement] = {}
    for sidecar_path, main_media_path in find_sidecar_candidates(files, source).items():
        main_media_probe = safe_probe(main_media_path)
        evidence = resolve_relationship(parse_sony_sidecar(sidecar_path), main_media_probe)
        if evidence is not RelationshipEvidence.NONE:
            resolved[sidecar_path] = (main_media_path, evidence)
            if main_media_path not in placements:
                main_media_classification = classify(
                    main_media_path, main_media_probe, camera_profiles
                )
                placements[main_media_path] = _InheritedPlacement(
                    category=main_media_classification.category,
                    camera_profile=main_media_classification.camera_profile,
                    recording_date=_resolve_recording_date(main_media_path, main_media_probe),
                )
    return resolved, placements


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


def _verified_project_workspace_root(storage_root: Path, client: str, project: str) -> Path:
    """Fase 5.3.2 — the ONLY place that produces a *resolved, trusted*
    Project Workspace boundary. Always re-verifies `Klanten/{client}/
    {project}` against `storage_root` from scratch, every single time it is
    called (both from `run()`, once, and from `_process_asset()`, once per
    asset) — never assumes a prior call earlier in the same `run()` already
    proved this, because the filesystem can change between calls (e.g. a
    symlink swapped into `Klanten`/the client folder/the project folder
    between two files of the same run — see DestinationEscapesWorkspaceError's
    TOCTOU note).

    This matters specifically because resolving `project_workspace_root`
    alone and then treating it as trustworthy for the rest of the run —
    without re-anchoring it against `storage_root_resolved` on every use —
    would let such a mid-run symlink swap silently become the new "trusted"
    workspace root for every check performed afterwards: a destination
    candidate and an already-compromised boundary that are BOTH resolved
    through the very same swapped symlink still look mutually consistent to
    a check that only compares the two against each other. Re-deriving and
    re-verifying the full chain (storage_root -> project_workspace_root) on
    every call closes that gap — a compromised `project_workspace_root` is
    caught here immediately, not just once at the very start of `run()`.

    Raises `DestinationEscapesWorkspaceError` if the chain does not hold —
    including, since the Fase 5.3.2 follow-up review's finding 1, when
    `Klanten/{client}/{project}` resolves to a DIFFERENT, otherwise
    legitimate location still inside `storage_root` (e.g. another client's
    or project's folder) — `_verify_within_boundary()`'s exact-relative-
    structure comparison, not a bare "is it inside storage_root" check,
    is what actually proves that. Never returns an unverified path."""
    project_workspace_root = _project_workspace_path(storage_root, client, project)
    storage_root_resolved = _resolve_boundary(storage_root, "de bestemmingsroot")
    return _verify_within_boundary(
        project_workspace_root,
        storage_root,
        storage_root_resolved,
        candidate_label="De Project Workspace-map",
        boundary_label="de bestemmingsroot",
    )


# Fase 5.3 — only these two categories get a camera_profile subfolder (see
# the Fase 5.3 target layout). Audio/Onbekend stay flat: Audio's only
# current profile ("Audio") and Onbekend (by construction, see
# classify()'s _unknown()) would make a subfolder redundant/meaningless —
# a data decision, not a structural limitation, revisit if that ever
# changes (e.g. more than one Audio profile).
_CAMERA_AWARE_CATEGORIES = {"Camera", "Drone"}


def _is_safe_profile_segment(camera_profile: str) -> bool:
    """Fase 5.3 — defense in depth for the profile subfolder name. In
    practice `category` being "Camera"/"Drone" already implies classify()
    matched a real CameraProfile: its only fallback, `_unknown()`, always
    pairs with category "Onbekend", never "Camera"/"Drone" — so
    `camera_profile` here is never missing/"Onbekend"/conflicting. This
    check exists anyway so a malformed or unexpected profile label (e.g. a
    future camera_profiles.yaml entry containing a path separator or "..")
    can never be turned into a path-escaping or misleading folder segment:
    it falls back to no subfolder (flat under the category) rather than
    raising or silently misfiling — the same "never guess, never silently
    misfile" stance as the rest of classification."""
    if not camera_profile or camera_profile in (".", ".."):
        return False
    return "/" not in camera_profile and "\\" not in camera_profile


def _build_workspace_path(
    storage_root: Path,
    client: str,
    project: str,
    recording_date: dt.date,
    category: str,
    camera_profile: str,
    filename: str,
) -> Path:
    folder = (
        _project_workspace_path(storage_root, client, project)
        / f"{recording_date.isoformat()}_Raw"
        / category
    )
    if category in _CAMERA_AWARE_CATEGORIES and _is_safe_profile_segment(camera_profile):
        folder = folder / camera_profile
    return folder / filename

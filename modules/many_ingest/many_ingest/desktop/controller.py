"""The Controller layer between the GUI and the ingest engine.

Fase 2 architecture (see the build plan for this phase):

    GUI -> Controller -> IngestService -> Storage -> Manifest -> Report

`build_ingest_service` wires up *exactly* what cli.py's composition root
wires up — same `load_ingest_config`/`load_camera_profiles`, same
`LocalFilesystemStorage`/`JSONManifest` adapters. No second implementation of
any of this; if it ever needs to change, it changes once, here, and both
front-ends (CLI and Desktop) pick it up.

`DryRunWorker` runs `IngestService.run(dry_run=True)` on a background
QThread so the GUI never blocks (see docs/MANY_INGEST_V1_UX_DESIGN.md,
hoofdstuk 3, "geen achtergrond-daemon" — this is a short-lived worker thread
for one explicit action, not a persistent background process). It emits
plain data (an `IngestSummary`, already reused as-is per its own docstring in
core/report.py — "a future GUI reuses IngestSummary directly") and never a
raw exception — every failure is translated to one plain-language message
here, so main_window.py never has to know what a checksum, a manifest, or
ffprobe is.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage
from many_ingest.config import load_camera_profiles, load_ingest_config
from many_ingest.core.ingest_service import IngestService
from many_ingest.core.report import summarize
from many_ingest.metadata_extractor import FfprobeNotFoundError

DEFAULT_CONFIG_PATH = Path("~/.many-ingest/config.yaml").expanduser()
DEFAULT_CAMERA_PROFILES_PATH = Path("~/.many-ingest/camera_profiles.yaml").expanduser()

_FFPROBE_MISSING_MESSAGE = (
    "Deze Mac mist een onderdeel dat nodig is om bestanden te analyseren. "
    "Neem contact op met de beheerder."
)
_CONFIG_INVALID_MESSAGE = (
    "De basisinstellingen voor Many Ingest zijn nog niet klaar op deze Mac. "
    "Neem contact op met de beheerder."
)
_SOURCE_UNREADABLE_MESSAGE = (
    "Kon deze locatie niet meer lezen. Controleer of de schijf nog is "
    "aangesloten en probeer het opnieuw."
)
_UNEXPECTED_ERROR_MESSAGE = "Er ging iets mis tijdens het analyseren. Probeer het opnieuw."


def _summarize_for_preview(report):
    """`summarize()` from core/report.py, with one correction for dry-run
    previews — not a reimplementation, a targeted fix of a real gap found
    while building this phase.

    `IngestSummary.duplicates` counts assets whose *outcome* is
    `DUPLICATE_SKIPPED` — but `_process_asset` in ingest_service.py always
    sets `outcome = PREVIEW` during a dry run (the `dry_run` branch is
    checked before the `is_duplicate` branch), so `summary.duplicates` is
    always 0 for a dry run, even though each `AssetResult.is_duplicate` is
    still set correctly. This is a pre-existing gap in core/report.py's own
    dry-run reporting (the CLI has the same blind spot) — not introduced
    here and not fixed here (that touches shared engine code, out of scope
    for this GUI phase). This only re-aggregates a field the engine already
    computes and exposes on every asset, for the one screen that needs an
    accurate dry-run duplicate count.
    """
    summary = summarize(report)
    if not summary.dry_run:
        return summary
    actual_duplicates = sum(1 for asset in report.assets if asset.is_duplicate)
    return dataclasses.replace(summary, duplicates=actual_duplicates)


def build_ingest_service(config_path: Path, camera_profiles_path: Path) -> IngestService:
    """Same wiring as cli.py's composition root — see the module docstring."""
    ingest_config = load_ingest_config(config_path)
    camera_profiles = load_camera_profiles(camera_profiles_path)
    return IngestService(
        storage=LocalFilesystemStorage(),
        manifest=JSONManifest(ingest_config.manifest_path),
        config=ingest_config,
        camera_profiles=camera_profiles,
    )


class DryRunWorker(QObject):
    """Runs one dry-run analysis. Plain data out via signals — no widgets,
    no text formatting beyond translating exceptions to user-facing copy."""

    progress = Signal(object)  # ProgressUpdate
    finished = Signal(object)  # IngestSummary
    failed = Signal(str)  # plain-language message, never a raw exception

    def __init__(
        self,
        source: Path,
        client: str,
        project: str,
        config_path: Path,
        camera_profiles_path: Path,
    ) -> None:
        super().__init__()
        self._source = source
        self._client = client
        self._project = project
        self._config_path = config_path
        self._camera_profiles_path = camera_profiles_path

    def run(self) -> None:
        try:
            service = build_ingest_service(self._config_path, self._camera_profiles_path)
            report = service.run(
                source=self._source,
                client=self._client,
                project=self._project,
                dry_run=True,
                progress_callback=self.progress.emit,
            )
        except FfprobeNotFoundError:
            self.failed.emit(_FFPROBE_MISSING_MESSAGE)
            return
        except ValueError:
            self.failed.emit(_CONFIG_INVALID_MESSAGE)
            return
        except OSError:
            self.failed.emit(_SOURCE_UNREADABLE_MESSAGE)
            return
        except Exception:  # nooit een stacktrace tonen — altijd vertaald
            self.failed.emit(_UNEXPECTED_ERROR_MESSAGE)
            return

        self.finished.emit(_summarize_for_preview(report))


def start_dry_run(
    source: Path,
    client: str,
    project: str,
    *,
    on_progress,
    on_finished,
    on_failed,
    config_path: Path = DEFAULT_CONFIG_PATH,
    camera_profiles_path: Path = DEFAULT_CAMERA_PROFILES_PATH,
) -> tuple[QThread, DryRunWorker]:
    """Starts a dry run on a background QThread and wires the given
    callbacks to its signals. Returns (thread, worker) so the caller can keep
    them alive for the run's duration (see main_window.py)."""
    worker = DryRunWorker(source, client, project, config_path, camera_profiles_path)
    thread = QThread()
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    # This only safely deletes `thread` once it has actually stopped (Qt
    # emits `finished` after the thread's event loop has exited, not merely
    # after `quit()` was requested) — the standard Qt worker-thread pattern.
    # It does NOT by itself prevent the crash this fixes: dropping the
    # caller's Python reference to `thread` *while it is still running*
    # (e.g. the window closing mid-analysis) crashes regardless of this
    # connection, because Python's own refcounting doesn't know the thread
    # is still alive. That's why MainWindow.closeEvent() now blocks on
    # thread.wait() before letting the window (and this reference) go.

    thread.start()
    return thread, worker

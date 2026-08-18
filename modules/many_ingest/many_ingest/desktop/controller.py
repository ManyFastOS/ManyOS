"""The Controller layer between the GUI and the ingest engine.

Fase 2 architecture (see the build plan for this phase):

    GUI -> Controller -> IngestService -> Storage -> Manifest -> Report

`build_ingest_service` (imported from `many_ingest.service_factory`, kept
Qt-free so `ingest_worker.py`'s separate OS process can use it too — see
that module's docstring) wires up *exactly* what cli.py's composition root
wires up — same `load_ingest_config`/`load_camera_profiles`, same
`LocalFilesystemStorage`/`JSONManifest` adapters. No second implementation of
any of this; if it ever needs to change, it changes once, and every
front-end (CLI, Desktop preview, Desktop real ingest) picks it up.

`DryRunWorker` runs `IngestService.run(dry_run=True)` on a background
QThread so the GUI never blocks (see docs/MANY_INGEST_V1_UX_DESIGN.md,
hoofdstuk 3, "geen achtergrond-daemon" — this is a short-lived worker thread
for one explicit action, not a persistent background process). It emits
plain data (an `IngestSummary`, already reused as-is per its own docstring in
core/report.py — "a future GUI reuses IngestSummary directly") and never a
raw exception — every failure is translated to one plain-language message
here, so main_window.py never has to know what a checksum, a manifest, or
ffprobe is.

**`build_ingest_service()` (and therefore every `yaml.safe_load()` call) is
always invoked from `start_dry_run()`, i.e. on the CALLER's thread — never
inside `DryRunWorker.run()` on the background QThread.** This is a deliberate
fix for a reproducible segfault (found inside a long-lived pytest session
running the full test suite, never in a single isolated test run) with a
stack trace bottoming out in PyYAML's C accelerator (`libyaml`, `yaml.safe_load`
→ `many_ingest.config.load_ingest_config`) invoked from
`build_ingest_service()` at the very start of what was `DryRunWorker.run()`.
Minimal repro scripts ruled out "libyaml + QThread" as inherently unsafe in
isolation (800 sequential real QThreads each doing two `yaml.safe_load()`
calls, in one process, never crashed) — the crash needed the accumulated
state of the full suite (many real ffprobe subprocess calls interleaved with
real QThread churn across many prior tests; a 2-file subset of ~14 similarly
heavy tests also never reproduced it). The exact mechanism is therefore most
likely heap/resource corruption from that broader subprocess+thread churn
surfacing at whatever C-extension call happened to run next — not a defect
in this file's design as such — but `yaml.safe_load()` inside the worker
thread was nonetheless an avoidable, unnecessary C-extension call from a
background QThread, for no benefit: loading two small local YAML files is
the same bounded, synchronous cost already paid at app startup (see
app.py's `_load_storage_root_safely`), so doing it once on the calling
thread, before the worker thread even exists, removes that call from the
worker thread's C-extension footprint entirely, for free.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from many_ingest.core.ingest_service import IngestService
from many_ingest.core.report import summarize
from many_ingest.desktop import thread_lifecycle
from many_ingest.metadata_extractor import FfprobeNotFoundError
from many_ingest.service_factory import build_ingest_service

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


class _AnalysisCancelled(Exception):
    """Internal-only signal that the user cancelled — never crosses out of
    DryRunWorker.run(), never shown to the GUI as an error."""


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


class DryRunWorker(QObject):
    """Runs one dry-run analysis. Plain data out via signals — no widgets,
    no text formatting beyond translating exceptions to user-facing copy.

    Receives an already-constructed `IngestService` (or, if construction
    already failed on the caller's thread, a pre-translated error message) —
    never a config path. See this module's docstring for why: no YAML
    parsing ever happens on this worker's background QThread."""

    progress = Signal(object)  # ProgressUpdate
    finished = Signal(object)  # IngestSummary
    failed = Signal(str)  # plain-language message, never a raw exception
    cancelled = Signal()  # a deliberate user action, never treated as a failure

    def __init__(
        self,
        service: IngestService | None,
        source: Path,
        client: str,
        project: str,
        *,
        preload_error: str | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._source = source
        self._client = client
        self._project = project
        self._preload_error = preload_error
        self._cancel_requested = False

    def request_cancel(self) -> None:
        """Called from the GUI thread (see main_window.py). A single bool
        flag, checked from the worker thread inside `_progress_callback` —
        no lock needed for a simple "has this been requested yet" latch;
        CPython's GIL makes the get/set itself atomic, and the only
        consequence of a one-tick-late read is finishing one extra file
        before stopping, never a correctness issue (dry-run: nothing is
        written either way)."""
        self._cancel_requested = True

    def run(self) -> None:
        # Config/camera-profielen zijn al geladen (of het laden is al
        # mislukt) vóórdat deze worker ooit startte — zie start_dry_run() en
        # de moduledocstring hierboven. Dat is ook waarom een mislukte
        # config-load hier een aparte, losstaande melding blijft i.p.v. in
        # dezelfde except-tak als service.run() hieronder terecht te komen:
        # beide kunnen ooit een OSError zijn, maar met een heel andere
        # oorzaak (ontbrekend/onleesbaar config-bestand op deze Mac vs. een
        # niet meer aangesloten bronschijf) — één gezamenlijke except OSError
        # liet een ontbrekend config.yaml ten onrechte de "schijf niet
        # aangesloten"-melding tonen (gevonden via reproductie: Resolved
        # path/Worker received bleken steeds correct en bestaand, terwijl de
        # fout toch verscheen).
        if self._preload_error is not None:
            self.failed.emit(self._preload_error)
            return

        try:
            report = self._service.run(
                source=self._source,
                client=self._client,
                project=self._project,
                dry_run=True,
                progress_callback=self._progress_callback,
            )
        except _AnalysisCancelled:
            self.cancelled.emit()
            return
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

    def _progress_callback(self, update) -> None:
        """Passed to IngestService.run() unchanged in spirit — the engine
        doesn't know about cancellation, it just calls this after every
        fully-processed file (see ingest_service.py's own docstring: the
        callback runs outside any try/except, so raising here propagates
        straight out of run(), stopping the loop — the current file is
        already safely finished, dry-run writes nothing regardless. This is
        an existing, documented property of the callback contract, not new
        engine behavior."""
        self.progress.emit(update)
        if self._cancel_requested:
            raise _AnalysisCancelled()


def start_dry_run(
    source: Path,
    client: str,
    project: str,
    *,
    on_progress,
    on_finished,
    on_failed,
    on_cancelled=None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    camera_profiles_path: Path = DEFAULT_CAMERA_PROFILES_PATH,
) -> tuple[QThread, DryRunWorker]:
    """Starts a dry run on a background QThread and wires the given
    callbacks to its signals. Returns (thread, worker) so the caller can keep
    them alive for the run's duration (see main_window.py). `worker` also
    exposes `request_cancel()` for the caller to invoke on demand.

    `build_ingest_service()` (config + camera-profiles YAML) is loaded right
    here, synchronously, on whichever thread calls `start_dry_run()` — the
    GUI thread in production — deliberately BEFORE the worker/thread objects
    are even created. See this module's docstring for why: this keeps every
    `yaml.safe_load()` call off the background QThread entirely. A failure
    here is not raised to the caller — it's translated to the same
    plain-language message the worker would have shown, and carried into the
    worker as `preload_error` so the caller still gets its usual
    `(thread, worker)` pair and its usual `on_failed` callback, on the usual
    signal path, with no change to callers (see main_window.py, unchanged)."""
    try:
        service = build_ingest_service(config_path, camera_profiles_path)
        preload_error = None
    except (OSError, ValueError):
        service = None
        preload_error = _CONFIG_INVALID_MESSAGE
    except Exception:  # nooit een stacktrace tonen — altijd vertaald
        service = None
        preload_error = _UNEXPECTED_ERROR_MESSAGE

    worker = DryRunWorker(service, source, client, project, preload_error=preload_error)
    thread = QThread()
    # Registreren zodra de thread bestaat, vóór .start() — zie
    # thread_lifecycle.py. Dit is vandaag de enige plek in de hele desktop-
    # app die een QThread aanmaakt (volume-detectie is volledig synchroon,
    # zie main_window.py's _run_detection()), maar deze registratie hangt
    # niet van die aanname af: elke toekomstige QThread die zich hier of
    # elders registreert, wordt automatisch meegenomen door shutdown().
    thread_lifecycle.register(thread)
    worker.moveToThread(thread)

    thread.started.connect(worker.run)
    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.cancelled.connect(thread.quit)
    if on_cancelled is not None:
        worker.cancelled.connect(on_cancelled)
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

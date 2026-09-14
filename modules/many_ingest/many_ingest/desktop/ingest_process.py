"""The GUI-facing half of the QProcess boundary — one generic runner for
BOTH a preview/dry-run and a real ingest (see many_ingest/ingest_worker.py
for the Qt-free worker half, and its module docstring for why this is a
separate OS process rather than a QThread).

    MainWindow -> IngestRunner (this file) -> QProcess -> ingest_worker.py
    -> IngestService -> Storage/Manifest/Report

`IngestRunner` never touches `IngestService` directly and never parses
business meaning out of anything beyond the JSON-lines protocol
`ingest_worker.py` documents — it only launches the worker process, buffers
and parses its stdout line by line, and re-emits each event as a Qt signal,
reconstructing the exact same `ProgressUpdate`/`IngestSummary` types for
both a preview and a real run (see core/ingest_service.py, core/report.py)
so main_window.py renders both through the same plain-data shapes.

Until this round, preview ran on a background `QThread`
(desktop/controller.py, now removed) while only the real ingest used this
QProcess runner. That split was removed after a real macOS crash report
(`faultingThread: QThread`, `QThread::~QThread()` on the main thread racing
`QObject::~QObject()`/`disconnectNotify` on the background thread —
confirmed via `~/Library/Logs/DiagnosticReports`) traced back to the
preview's unparented `QThread()`: its C++ object could be torn down
concurrently by Python refcounting (dropping the GUI's last reference once
`finished` fired) and by Qt's own `deleteLater()` — a genuine race, not a
timing detail a longer grace period or an extra guard could close. A
`QProcess` has no second thread and no shared Qt/Python object graph for two
threads to fight over, so it has no equivalent failure mode. `dry_run`
below is the only difference between the two call shapes — one worker
entrypoint, one runner class, one protocol.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from many_ingest.core.ingest_service import ProgressUpdate
from many_ingest.core.report import IngestSummary

DEFAULT_CONFIG_PATH = Path("~/.many-ingest/config.yaml").expanduser()
DEFAULT_CAMERA_PROFILES_PATH = Path("~/.many-ingest/camera_profiles.yaml").expanduser()

# Hoe lang IngestRunner.cancel() wacht op een nette stop (SIGTERM, zie
# ingest_worker.py) voordat hij forceert (kill(), SIGKILL) — "probeer eerst
# gecontroleerd te stoppen, forceer alleen als de worker niet tijdig
# reageert." Ruim genoeg om het huidige bestand te laten afronden (v0.1
# kopieert nooit een bestand af zonder het te verifiëren, en onderbreekt
# nooit een schrijfactie halverwege), kort genoeg om niet als "hangt" aan te
# voelen voor de editor.
_CANCEL_GRACE_MS = 5_000

_INGEST_CRASHED_MESSAGE = (
    "Er ging onverwacht iets mis tijdens het kopiëren. Er is niets "
    "overschreven — controleer de verbinding met de schijf en probeer het "
    "opnieuw."
)
_INGEST_COULD_NOT_START_MESSAGE = (
    "Kon het kopiëren niet starten op deze Mac. Neem contact op met de beheerder."
)
_PREVIEW_CRASHED_MESSAGE = (
    "Er ging onverwacht iets mis tijdens het bekijken van de inhoud. Er is "
    "niets gewijzigd — probeer het opnieuw."
)
_PREVIEW_COULD_NOT_START_MESSAGE = (
    "Kon de inhoud niet bekijken op deze Mac. Neem contact op met de beheerder."
)


class IngestRunner(QObject):
    """Wraps one worker `QProcess` — a preview/dry-run OR a real ingest,
    `dry_run` is the only difference (see this module's docstring). Plain
    data out via signals — no widgets, no text formatting beyond what
    `ingest_worker.py` already translated. Never raises a raw exception
    across this boundary."""

    started = Signal(dict)  # {"source", "client", "project"}
    progress = Signal(object)  # ProgressUpdate
    asset_processed = Signal(dict)
    completed = Signal(object)  # IngestSummary
    failed = Signal(str)  # plain-language message, never a raw exception/stacktrace
    cancelled = Signal()

    def __init__(
        self,
        source: Path,
        client: str,
        project: str,
        *,
        config_path: Path,
        camera_profiles_path: Path,
        dry_run: bool = False,
        _command_override: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._buffer = ""
        self._terminal_event_seen = False
        self._dry_run = dry_run

        self._process = QProcess(self)
        if _command_override is not None:
            # Test-only seam (see test_desktop_ingest_process.py): points the
            # underlying QProcess at a different program than the real
            # worker, so cancel-escalation and crash-handling can be tested
            # deterministically (a SIGTERM-ignoring script, a script that
            # segfaults on purpose) without needing a real IngestService run.
            # Never used in production — desktop/main_window.py never passes
            # this argument.
            self._process.setProgram(_command_override[0])
            self._process.setArguments(_command_override[1:])
        else:
            self._process.setProgram(sys.executable)
            args = [
                "-m",
                "many_ingest.ingest_worker",
                "--source",
                str(source),
                "--client",
                client,
                "--project",
                project,
                "--config",
                str(config_path),
                "--camera-profiles",
                str(camera_profiles_path),
                "--mode",
                "copy",
            ]
            if dry_run:
                args.append("--dry-run")
            self._process.setArguments(args)
        self._process.readyReadStandardOutput.connect(self._on_ready_read)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error_occurred)

        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._process.kill)

    def start(self) -> None:
        self._process.start()

    def cancel(self) -> None:
        """See `_CANCEL_GRACE_MS` above. Never removes/touches source files
        — v0.1 only ever copies, so even a forced kill leaves the source
        exactly as it was; the only consequence is that the destination may
        contain a partially-copied file, which is a pre-existing, documented
        gap (see docs/MANY_INGEST_V0.1_READINESS_ASSESSMENT.md, "geen
        afhandeling van een onderbroken run"), not something this cancel
        path introduces or is responsible for cleaning up."""
        if self._process.state() == QProcess.ProcessState.NotRunning:
            return
        self._process.terminate()
        self._kill_timer.start(_CANCEL_GRACE_MS)

    def is_running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    def stop_and_wait(self, timeout_ms: int = _CANCEL_GRACE_MS) -> None:
        """Synchronous, blocking variant of `cancel()` for shutdown paths
        (`MainWindow.closeEvent`/`app.aboutToQuit`, see main_window.py's
        `_wait_for_ingest_to_stop`) that must not return while the worker
        process could still be silently running — the `QProcess` equivalent
        of `thread_lifecycle.shutdown()`'s guarantee for the preview's
        `QThread`.

        Deliberately does not reuse the async `cancel()` + `_kill_timer`
        path: that escalation depends on this object's own `QTimer` firing,
        which needs the Qt event loop to keep running — a correct assumption
        for an interactive Annuleren click (the GUI stays responsive and
        should), but not a safe one to depend on during app shutdown. This
        method blocks directly on `QProcess.waitForFinished()` instead, so
        the guarantee holds regardless of what the event loop is doing.
        """
        if self._process.state() == QProcess.ProcessState.NotRunning:
            return
        self._kill_timer.stop()
        self._process.terminate()
        if not self._process.waitForFinished(timeout_ms):
            self._process.kill()
            self._process.waitForFinished()

    # -- stdout parsing ------------------------------------------------------

    def _read_stdout_chunk(self) -> bytes:
        # Eigen kleine indirectie (i.p.v. de QProcess-aanroep rechtstreeks in
        # `_on_ready_read` hieronder) puur om de regel-bufferlogica in
        # test_desktop_ingest_process.py te kunnen testen met een gecontroleerd,
        # over twee "reads" opgeknipt fragment — zonder een echt subprocess
        # nodig te hebben om exact zo'n opsplitsing betrouwbaar te forceren.
        return bytes(self._process.readAllStandardOutput())

    def _on_ready_read(self) -> None:
        chunk = self._read_stdout_chunk().decode("utf-8", errors="replace")
        self._buffer += chunk
        # Een regel kan over twee losse reads verdeeld binnenkomen — alleen
        # een compleet afgesloten regel (eindigend op "\n") is geldige JSON;
        # de rest blijft in de buffer staan tot de volgende read hem afmaakt.
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if line:
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return  # nooit crashen op een onverwachte/corrupte regel

        event = payload.pop("event", None)

        if event == "ingest_started":
            self.started.emit(payload)
        elif event == "progress":
            self.progress.emit(ProgressUpdate(**payload))
        elif event == "asset_processed":
            self.asset_processed.emit(payload)
        elif event == "ingest_completed":
            self._terminal_event_seen = True
            self.completed.emit(IngestSummary(**payload))
        elif event == "ingest_failed":
            self._terminal_event_seen = True
            self.failed.emit(payload.get("message", self._crashed_message()))
        elif event == "ingest_cancelled":
            self._terminal_event_seen = True
            self.cancelled.emit()
        # Een onbekend event-type wordt genegeerd, niet als fout behandeld —
        # dit blijft een append-only protocol; een toekomstige worker-versie
        # mag een nieuw event-type sturen zonder deze GUI-versie te breken.

    # -- process lifecycle -----------------------------------------------------

    def _crashed_message(self) -> str:
        return _PREVIEW_CRASHED_MESSAGE if self._dry_run else _INGEST_CRASHED_MESSAGE

    def _could_not_start_message(self) -> str:
        return _PREVIEW_COULD_NOT_START_MESSAGE if self._dry_run else _INGEST_COULD_NOT_START_MESSAGE

    def _on_finished(self, exit_code: int, exit_status) -> None:
        self._kill_timer.stop()
        if not self._terminal_event_seen:
            # Het workerproces is gestopt zonder ooit een eindevent te sturen
            # — een crash (Qt-fatal, segfault, gekilld) ergens middenin. De
            # GUI blijft leven; dit wordt vertaald naar dezelfde soort
            # vriendelijke melding als elke andere mislukking, nooit een
            # stacktrace (Design Language hoofdstuk 15).
            self.failed.emit(self._crashed_message())

    def _on_error_occurred(self, error) -> None:
        # Alleen FailedToStart hier apart afhandelen: dat is het ene
        # QProcess-foutgeval waarvoor `finished` per Qt's eigen contract
        # NOOIT alsnog vuurt (het proces is immers nooit begonnen). Elke
        # andere fout (bijv. Crashed tijdens het draaien) triggert wél nog
        # `finished` — die valt al onder `_on_finished`'s eigen "geen
        # eindevent gezien"-afhandeling hierboven, met dezelfde vriendelijke
        # crash-melding; hem hier óók al afvangen zou het verschil tussen
        # "nooit gestart" en "halverwege gecrasht" onterecht verdoezelen.
        if error == QProcess.ProcessError.FailedToStart and not self._terminal_event_seen:
            self._terminal_event_seen = True
            self.failed.emit(self._could_not_start_message())


def _start(runner: IngestRunner, *, on_started, on_progress, on_asset_processed, on_completed, on_failed, on_cancelled) -> IngestRunner:
    """Shared wiring for `start_real_ingest`/`start_preview` — one runner
    class, one place that connects its signals, so the two only ever differ
    in the one thing that's actually different (`dry_run`, passed to
    `IngestRunner` by the caller)."""
    if on_started is not None:
        runner.started.connect(on_started)
    runner.progress.connect(on_progress)
    if on_asset_processed is not None:
        runner.asset_processed.connect(on_asset_processed)
    runner.completed.connect(on_completed)
    runner.failed.connect(on_failed)
    if on_cancelled is not None:
        runner.cancelled.connect(on_cancelled)

    runner.start()
    return runner


def start_real_ingest(
    source: Path,
    client: str,
    project: str,
    *,
    on_started=None,
    on_progress,
    on_asset_processed=None,
    on_completed,
    on_failed,
    on_cancelled=None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    camera_profiles_path: Path = DEFAULT_CAMERA_PROFILES_PATH,
) -> IngestRunner:
    """Starts a real ingest in its own OS process and wires the given
    callbacks to its signals."""
    runner = IngestRunner(
        source,
        client,
        project,
        config_path=config_path,
        camera_profiles_path=camera_profiles_path,
        dry_run=False,
    )
    return _start(
        runner,
        on_started=on_started,
        on_progress=on_progress,
        on_asset_processed=on_asset_processed,
        on_completed=on_completed,
        on_failed=on_failed,
        on_cancelled=on_cancelled,
    )


def start_preview(
    source: Path,
    client: str,
    project: str,
    *,
    on_started=None,
    on_progress,
    on_asset_processed=None,
    on_completed,
    on_failed,
    on_cancelled=None,
    config_path: Path = DEFAULT_CONFIG_PATH,
    camera_profiles_path: Path = DEFAULT_CAMERA_PROFILES_PATH,
) -> IngestRunner:
    """Starts a preview/dry-run in its own OS process and wires the given
    callbacks to its signals — same shape, same runner class, same protocol
    as `start_real_ingest`, only `dry_run=True` differs. Replaces the old
    QThread-based `controller.start_dry_run()` (removed this round — see
    this module's docstring for why)."""
    runner = IngestRunner(
        source,
        client,
        project,
        config_path=config_path,
        camera_profiles_path=camera_profiles_path,
        dry_run=True,
    )
    return _start(
        runner,
        on_started=on_started,
        on_progress=on_progress,
        on_asset_processed=on_asset_processed,
        on_completed=on_completed,
        on_failed=on_failed,
        on_cancelled=on_cancelled,
    )

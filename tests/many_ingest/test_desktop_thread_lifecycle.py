"""Regression tests for the QThread lifecycle crash found while testing
Many Ingest Desktop on macOS ("Python is onverwacht gestopt" / SIGABRT /
"QThread: Destroyed while thread is still running" — confirmed against real
crash reports in ~/Library/Logs/DiagnosticReports, all with an identical
stack: abort() -> QThread::~QThread() -> Shiboken object deallocation).

Root cause: dropping the last Python reference to a QThread while its OS
thread was still running crashed the whole process — a Qt fatal error, not a
catchable Python exception. First round fixed the explicit-close path
(MainWindow.closeEvent() blocking on thread.wait()) — but the crash
persisted in real use, because macOS sends Cmd+Q / the app-menu Quit item as
a direct `QApplication.quit()`, which never delivers a `QCloseEvent` to the
window at all, so closeEvent() never ran (confirmed by reproduction:
`_quit_during_analysis_scenario.py`). Fixed by also wiring
`QApplication.aboutToQuit` (in app.py) to the same wait logic
(`MainWindow._wait_for_analysis_to_stop`), plus:
- `_on_analysis_thread_finished()`, connected to `thread.finished` (not
  `worker.finished`), is now the *only* place `_analysis_thread`/
  `_analysis_worker` are cleared — only once the thread has provably
  stopped, never eagerly.
- controller.start_dry_run() still deletes the QThread only via
  thread.finished.connect(thread.deleteLater) — cleanup timing, not the
  safety guarantee (that's thread.wait(), which blocks regardless of
  deleteLater).

These tests use *real* QThreads (via controller.start_dry_run / a real
MainWindow). An injected fake `start_dry_run`, as used in
test_desktop_main_window.py, calls its callbacks synchronously and would not
exercise this bug at all — it never creates a real background thread.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication

from many_ingest.core.report import IngestSummary
from many_ingest.desktop import controller
from many_ingest.desktop.main_window import PREVIEW_TITLE_TEXT, MainWindow

CAMERA_PROFILES_PATH = (
    Path(__file__).resolve().parents[2]
    / "modules"
    / "many_ingest"
    / "config"
    / "camera_profiles.yaml"
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"storage_root: {tmp_path / 'storage'}\n"
        f"manifest_path: {tmp_path / 'asset_schema.json'}\n"
        f"log_dir: {tmp_path / 'logs'}\n"
    )
    return config_path


_DUMMY_SUMMARY = IngestSummary(
    project="Zomer",
    client="Nike",
    dry_run=True,
    total_files=0,
    video_count=0,
    audio_count=0,
    unknown_type_count=0,
    camera_profile_counts={},
    duplicates=0,
    name_conflicts_resolved=0,
    errors=0,
    total_bytes=0,
    duration_seconds=0.0,
    log_path="",
    safe_to_delete_source=False,
)


class _SlowWorker(QObject):
    """Real work, minimally: just sleeps, then reports done. Used only to
    exercise MainWindow's/controller's *thread lifecycle* handling (does the
    window survive a still-running QThread, does a reference reassignment
    survive) in isolation from real ffprobe/subprocess calls.

    Real subprocess calls under heavy, repeated QThread churn within one
    long-lived pytest process turned out to be a separate, pre-existing
    source of flakiness (a `subprocess`/`selectors` segfault, unrelated to
    the QThread-lifecycle bug this file regression-tests) — see the note on
    `_make_slow_start_dry_run` below. `test_analysis_finishes_normally...`
    still exercises one real engine run end-to-end.
    """

    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(self, delay_seconds: float) -> None:
        super().__init__()
        self._delay_seconds = delay_seconds

    def run(self) -> None:
        time.sleep(self._delay_seconds)
        self.finished.emit(_DUMMY_SUMMARY)


def _make_slow_start_dry_run(delay_seconds: float = 0.5):
    """A `start_dry_run`-compatible fake with the *exact* real thread-wiring
    shape (moveToThread, started->run, finished->quit/deleteLater) but
    trivial work (a sleep) instead of a real engine call — see
    `_SlowWorker`. This is what makes "still running when we act" a
    deterministic property of the test instead of a race against real
    ffprobe speed, without reintroducing the real-subprocess flakiness noted
    above (confirmed: this combination is reliable across 10+ repeated full
    suite runs, where the earlier all-real-subprocess version was not).
    """

    def _start(
        source,
        client,
        project,
        *,
        on_progress,
        on_finished,
        on_failed,
        on_cancelled=None,
        config_path,
        camera_profiles_path,
    ):
        worker = _SlowWorker(delay_seconds)
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

        thread.start()
        return thread, worker

    return _start


class _Waiter(QObject):
    """A real QObject receiver — plain functions/closures as cross-thread
    signal receivers crash (see test_desktop_controller.py); PySide6 can't
    determine a plain function's thread affinity to queue the delivery."""

    done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.summary = None
        self.failed = None

    def on_progress(self, update) -> None:
        pass

    def on_finished(self, summary) -> None:
        self.summary = summary
        self.done.emit()

    def on_failed(self, message) -> None:
        self.failed = message
        self.done.emit()


def _run_and_wait(source, client, project, config_path, timeout_ms=10_000):
    waiter = _Waiter()
    loop = QEventLoop()
    waiter.done.connect(loop.quit)

    thread, _worker = controller.start_dry_run(
        source,
        client,
        project,
        on_progress=waiter.on_progress,
        on_finished=waiter.on_finished,
        on_failed=waiter.on_failed,
        config_path=config_path,
        camera_profiles_path=CAMERA_PROFILES_PATH,
    )

    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(loop.quit)
    timeout_timer.start(timeout_ms)

    loop.exec()
    thread.wait(2000)
    return waiter, thread


class _LoopQuitter(QObject):
    """A real QObject that chains to MainWindow's own on_finished/on_failed
    and then quits a QEventLoop — connecting a *plain function* here instead
    (as an earlier version of this test did) crashed/misbehaved: PySide6
    can't determine a plain closure's thread affinity, so the cross-thread
    signal delivery isn't reliably queued (see test_desktop_controller.py's
    `_ResultCollector` for the same lesson)."""

    def __init__(self, loop: QEventLoop) -> None:
        super().__init__()
        self.loop = loop
        self.inner_finished = None
        self.inner_failed = None

    def on_finished(self, summary) -> None:
        self.inner_finished(summary)
        self.loop.quit()

    def on_failed(self, message: str) -> None:
        self.inner_failed(message)
        self.loop.quit()


# -- 1. Venster sluiten tijdens analyse -----------------------------------------


def test_closing_the_window_during_analysis_does_not_crash_50_times_in_a_row(tmp_path):
    """Runs each repetition in its own fresh process — see
    _close_during_analysis_scenario.py's module docstring for why: this
    exact scenario, run inside the shared pytest process alongside ~90 other
    tests (several of which spawn real ffprobe subprocesses on other
    QThreads), intermittently segfaulted deep inside Python's
    `subprocess`/`selectors` internals — a real race, but strictly between
    *independent* Qt/thread sessions sharing one long-lived process, which
    real usage never creates (closing the window ends the process). A fresh
    process per session is also simply the more faithful reproduction of
    what `many-ingest-desktop` actually does.

    Bumped from a single run to 50 (this round's requested sweep, see
    thread_lifecycle.py) — this scenario uses a fake `start_dry_run` that
    creates its own raw QThread, bypassing controller.start_dry_run()
    entirely, which is exactly the case that exposed a real gap in this
    round's first version of the central thread registry: registering only
    inside controller.start_dry_run() left this test's thread unregistered,
    so `thread_lifecycle.shutdown()` had nothing to wait on even though
    MainWindow was still tracking a live one. See thread_lifecycle.py's
    module docstring for the fix (MainWindow registers too, at the point it
    starts tracking a thread, regardless of which factory produced it)."""
    script = Path(__file__).with_name("_close_during_analysis_scenario.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    for i in range(50):
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, (
            f"run {i}: scenario-proces crashte (returncode={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "OK" in result.stdout, f"run {i}: geen 'OK' in stdout:\n{result.stdout}"


def test_quitting_via_aboutToQuit_during_analysis_does_not_crash_50_times_in_a_row(tmp_path):
    """The exact regression for an earlier round's bug: `QApplication.quit()`
    (Cmd+Q / app-menu Quit — never `window.close()`) called while an
    analysis is still running. Before wiring `aboutToQuit` to
    `MainWindow._wait_for_analysis_to_stop()`, this reliably crashed with
    SIGABRT even after the first `closeEvent()` fix, because this path never
    goes through `closeEvent()` at all.

    50 independent, fresh processes (bumped from 20 this round) — not 50
    iterations in one process — so each run is the most faithful possible
    reproduction of a real `many-ingest-desktop` session (one process, one
    window, one quit), and so a failure on run N doesn't depend on what
    runs 1..N-1 left behind.
    """
    script = Path(__file__).with_name("_quit_during_analysis_scenario.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    for i in range(50):
        run_dir = tmp_path / f"run_{i:02d}"
        run_dir.mkdir()
        result = subprocess.run(
            [sys.executable, str(script), str(run_dir)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, (
            f"run {i}: scenario-proces crashte (returncode={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "OK" in result.stdout, f"run {i}: geen 'OK' in stdout:\n{result.stdout}"


def test_opening_and_closing_the_window_without_any_analysis_does_not_crash_50_times_in_a_row(
    tmp_path,
):
    """Baseline control: nothing is ever started, so no QThread is ever
    created — included in this round's 50x sweep anyway (per the requested
    coverage) so the whole lifecycle is swept, not only the cases already
    known to be risky."""
    script = Path(__file__).with_name("_open_close_no_analysis_scenario.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    for i in range(50):
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, (
            f"run {i}: scenario-proces crashte (returncode={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "OK" in result.stdout, f"run {i}: geen 'OK' in stdout:\n{result.stdout}"


def test_closing_right_after_a_volume_scan_does_not_crash_50_times_in_a_row(tmp_path):
    """Volume detection (`MainWindow._run_detection()`) is entirely
    synchronous on the GUI thread today — no QThread, no worker. Qt's
    single-threaded event loop cannot process a close/quit event while
    `_run_detection()` is still running, so "close while a volume scan is
    active" is not a reachable state in the current architecture, by
    construction. This test (and its scenario script) documents that
    directly and guards against a future change that moves volume detection
    onto a background thread reintroducing the shutdown race this round is
    about, without that thread being registered/waited on correctly."""
    script = Path(__file__).with_name("_close_after_volume_scan_scenario.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    for i in range(50):
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, (
            f"run {i}: scenario-proces crashte (returncode={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "OK" in result.stdout, f"run {i}: geen 'OK' in stdout:\n{result.stdout}"


def test_closing_immediately_after_a_finished_preview_does_not_crash_50_times_in_a_row(tmp_path):
    """The most timing-sensitive case in this round's sweep: closes the
    window from inside the same `worker.finished` signal delivery that
    renders the preview — the earliest possible moment after an analysis
    completes, before the sibling queued `worker.finished -> thread.quit()`
    connection (wired in controller.start_dry_run) is guaranteed to have
    run yet. Uses a real analysis (real ffprobe subprocess) for realistic,
    fast completion timing rather than the artificial 0.5s
    `_SlowWorker` sleep used elsewhere in this file — see
    _close_immediately_after_finish_scenario.py's module docstring for the
    full reasoning, including a test-script-only bug this scenario
    surfaced and fixed along the way (a bare function used as a
    cross-thread signal receiver)."""
    script = Path(__file__).with_name("_close_immediately_after_finish_scenario.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    for i in range(50):
        run_dir = tmp_path / f"run_{i:02d}"
        run_dir.mkdir()
        result = subprocess.run(
            [sys.executable, str(script), str(run_dir)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, (
            f"run {i}: scenario-proces crashte (returncode={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "OK" in result.stdout, f"run {i}: geen 'OK' in stdout:\n{result.stdout}"


def test_start_second_analysis_and_close_during_first_50_times_in_a_row(tmp_path):
    """The combined regression for this round: (1) start a real analysis,
    (2) immediately try to start a second one on top of it, (3) close the
    window while the first is still running — all in one session, repeated
    50 times as 50 independent fresh processes (see
    _thread_lifecycle_stress_scenario.py's module docstring)."""
    script = Path(__file__).with_name("_thread_lifecycle_stress_scenario.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    for i in range(50):
        run_dir = tmp_path / f"run_{i:02d}"
        run_dir.mkdir()
        result = subprocess.run(
            [sys.executable, str(script), str(run_dir)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert result.returncode == 0, (
            f"run {i}: scenario-proces crashte (returncode={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "OK" in result.stdout, f"run {i}: geen 'OK' in stdout:\n{result.stdout}"


# -- 2. Normale afronding van een analyse ---------------------------------------


def test_analysis_finishes_normally_and_the_thread_stops_cleanly(qapp, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    waiter, thread = _run_and_wait(input_dir, "Nike", "Zomer", config_path)

    assert waiter.failed is None
    assert waiter.summary is not None and waiter.summary.total_files == 1
    assert not thread.isRunning()


def test_config_loading_happens_on_the_calling_thread_not_the_worker_thread(qapp, tmp_path, monkeypatch):
    """Regression test for the segfault this round fixed: a reproducible
    crash (found only inside a long-lived full-suite pytest run, never in an
    isolated run) with a stack trace bottoming out in PyYAML's C accelerator
    (`yaml.safe_load`) invoked from `load_ingest_config`, called from
    `build_ingest_service`, which at the time ran inside `DryRunWorker.run()`
    on the background QThread.

    The structural fix (see controller.py's module docstring and
    `start_dry_run()`) moves `build_ingest_service()` — and therefore every
    `yaml.safe_load()` call — to whichever thread calls
    `controller.start_dry_run()`. In production that's always the GUI
    thread; here it's this test function's own thread. This test proves
    that placement directly, by spying on `controller.load_ingest_config`
    and recording which OS thread called it."""
    import threading

    from many_ingest.desktop import controller as controller_module

    calling_thread_id = threading.get_ident()
    observed_thread_ids: list[int] = []

    real_load_ingest_config = controller_module.load_ingest_config

    def spy_load_ingest_config(path):
        observed_thread_ids.append(threading.get_ident())
        return real_load_ingest_config(path)

    monkeypatch.setattr(controller_module, "load_ingest_config", spy_load_ingest_config)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    waiter, thread = _run_and_wait(input_dir, "Nike", "Zomer", config_path)

    assert waiter.failed is None
    assert observed_thread_ids, "load_ingest_config werd nooit aangeroepen"
    assert observed_thread_ids == [calling_thread_id] * len(observed_thread_ids), (
        "load_ingest_config (en dus yaml.safe_load) liep op de achtergrond-"
        "QThread in plaats van op de aanroepende thread — precies de situatie "
        "die de segfault veroorzaakte"
    )


def test_100_preview_runs_in_a_row_do_not_crash(qapp, tmp_path):
    """Direct regression test for the segfault itself: 100 sequential real
    dry-run previews (real QThread, real ffprobe subprocess call, config
    loading on the calling thread per the fix above) in one long-lived
    process must not crash. This is the shape of repeatedly clicking
    "Bekijk inhoud" in one session, and the shape (repeated real-QThread +
    real-subprocess churn in one long-lived process) that made the original
    segfault reproducible."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    for i in range(100):
        waiter, thread = _run_and_wait(input_dir, "Nike", "Zomer", config_path)
        assert waiter.failed is None, f"run {i}: {waiter.failed}"
        assert waiter.summary is not None and waiter.summary.total_files == 1
        assert not thread.isRunning()


# -- 3. Meerdere analyses achter elkaar ------------------------------------------


def test_multiple_sequential_analyses_via_the_controller_do_not_crash(qapp, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    for _ in range(3):
        waiter, thread = _run_and_wait(input_dir, "Nike", "Zomer", config_path)
        assert waiter.failed is None
        assert waiter.summary is not None
        assert not thread.isRunning()


def test_multiple_sequential_analyses_via_the_real_window_do_not_crash(qapp, tmp_path):
    """Uses the lightweight fake worker (see _make_slow_start_dry_run) — this
    test is about MainWindow safely reassigning `self._analysis_thread`
    across several genuinely-completed real QThreads, not about engine
    correctness (already covered by
    `test_analysis_finishes_normally_and_the_thread_stops_cleanly` and
    test_desktop_controller.py)."""
    loop = QEventLoop()
    quitter = _LoopQuitter(loop)
    real_start = _make_slow_start_dry_run(delay_seconds=0.1)

    def observed_start(*args, **kwargs):
        quitter.inner_finished = kwargs["on_finished"]
        quitter.inner_failed = kwargs["on_failed"]
        kwargs["on_finished"] = quitter.on_finished
        kwargs["on_failed"] = quitter.on_failed
        return real_start(*args, **kwargs)

    window = MainWindow(detect_volumes=lambda: [], start_dry_run=observed_start)
    window._set_manual_source(tmp_path)

    for _ in range(3):
        window.client_input().setText("Nike")
        window.project_input().setText("Zomer")
        window.choose_button().click()

        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timeout_timer.timeout.connect(loop.quit)
        timeout_timer.start(10_000)
        loop.exec()

        assert window.current_message() == PREVIEW_TITLE_TEXT
        window._return_to_selection()

    window.close()


# -- 4. Openen/sluiten van de app zonder crash -----------------------------------


def test_opening_and_closing_the_window_without_any_analysis_does_not_crash(qapp):
    window = MainWindow(detect_volumes=lambda: [])
    window.show()
    window.close()
    # Geen expliciete assert nodig: als dit uitkomt zonder SIGABRT, is de
    # kern van deze test al bewezen.
    assert True

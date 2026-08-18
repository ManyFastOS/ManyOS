"""Tests for desktop/ingest_process.py — the Fase 3 GUI-facing half of the
QProcess boundary (see that module's docstring, and test_ingest_worker.py
for the Qt-free worker half).

Runs headless (QT_QPA_PLATFORM=offscreen). Skips cleanly when PySide6 isn't
installed (optional `[gui]` extra). All real-ingest tests use small, synthetic,
temporary files — never real production footage, per this round's testing rules.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from many_ingest.desktop import ingest_process

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


def _wait_until(predicate, qapp, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        qapp.processEvents()
        time.sleep(0.01)
    return predicate()


class _Collector(QObject):
    """A real QObject receiver, not a bare function — kept consistent with
    the rest of this test suite's convention (see test_desktop_controller.py's
    `_ResultCollector`), even though IngestRunner's signals are all emitted on
    the same (GUI) thread as the receiver here, so the cross-thread-closure
    pitfall that convention guards against elsewhere does not actually apply
    to QProcess-backed signals the way it does to QThread-backed ones."""

    done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.progress: list = []
        self.summary = None
        self.failed: str | None = None
        self.cancelled = False

    def on_progress(self, update) -> None:
        self.progress.append(update)

    def on_completed(self, summary) -> None:
        self.summary = summary
        self.done.emit()

    def on_failed(self, message: str) -> None:
        self.failed = message
        self.done.emit()

    def on_cancelled(self) -> None:
        self.cancelled = True
        self.done.emit()


def _run_and_wait(collector: _Collector, runner, timeout_ms: int = 15_000) -> None:
    loop = QEventLoop()
    collector.done.connect(loop.quit)
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(loop.quit)
    timeout_timer.start(timeout_ms)
    loop.exec()
    runner.stop_and_wait(2000)


# -- real, end-to-end ingest via IngestRunner ------------------------------------


def test_real_ingest_via_runner_reports_progress_and_a_reconstructed_summary(qapp, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    collector = _Collector()
    runner = ingest_process.start_real_ingest(
        input_dir,
        "Nike",
        "Zomer",
        on_progress=collector.on_progress,
        on_completed=collector.on_completed,
        on_failed=collector.on_failed,
        config_path=config_path,
        camera_profiles_path=CAMERA_PROFILES_PATH,
    )

    _run_and_wait(collector, runner)

    assert collector.failed is None
    assert collector.summary is not None
    assert (collector.summary.client, collector.summary.project) == ("Nike", "Zomer")
    assert collector.summary.total_files == 1
    assert collector.progress, "verwacht minstens één voortgangsupdate"

    copied = list((tmp_path / "storage").rglob("DJI_0001.MP4"))
    assert len(copied) == 1
    assert copied[0].read_bytes() == b"fake video bytes"


def test_cancel_stops_a_real_ingest_gracefully(qapp, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(50):
        (input_dir / f"C{i:04d}.MP4").write_bytes(b"x" * 2_000_000)
    config_path = _write_config(tmp_path)

    collector = _Collector()
    runner_box: list = []

    def _on_progress(update) -> None:
        collector.on_progress(update)
        if len(collector.progress) == 1:
            runner_box[0].cancel()

    runner = ingest_process.start_real_ingest(
        input_dir,
        "Nike",
        "Zomer",
        on_progress=_on_progress,
        on_completed=collector.on_completed,
        on_failed=collector.on_failed,
        on_cancelled=collector.on_cancelled,
        config_path=config_path,
        camera_profiles_path=CAMERA_PROFILES_PATH,
    )
    runner_box.append(runner)

    _run_and_wait(collector, runner)

    assert collector.cancelled is True
    assert collector.failed is None
    assert collector.summary is None
    assert not runner.is_running()


# -- crash handling: the GUI (this process) must never crash or raise -----------


def test_worker_crash_emits_failed_and_never_raises(qapp, tmp_path):
    runner = ingest_process.IngestRunner(
        tmp_path,
        "Nike",
        "Zomer",
        config_path=tmp_path / "config.yaml",
        camera_profiles_path=tmp_path / "profiles.yaml",
        _command_override=[
            sys.executable,
            "-c",
            "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)",
        ],
    )
    received: list[str] = []
    runner.failed.connect(received.append)
    runner.start()

    assert _wait_until(lambda: bool(received), qapp), "geen 'failed'-signaal ontvangen na de crash"
    assert "Traceback" not in received[0]
    assert "SIGSEGV" not in received[0]  # nooit een technisch detail tonen


def test_worker_that_never_starts_emits_failed(qapp, tmp_path):
    runner = ingest_process.IngestRunner(
        tmp_path,
        "Nike",
        "Zomer",
        config_path=tmp_path / "config.yaml",
        camera_profiles_path=tmp_path / "profiles.yaml",
        _command_override=["/pad/dat/gegarandeerd/niet/bestaat/many-ingest-worker"],
    )
    received: list[str] = []
    runner.failed.connect(received.append)
    runner.start()

    assert _wait_until(lambda: bool(received), qapp), "geen 'failed'-signaal na FailedToStart"
    assert received[0]


def test_cancel_escalates_to_kill_when_sigterm_is_ignored(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(ingest_process, "_CANCEL_GRACE_MS", 300)
    runner = ingest_process.IngestRunner(
        tmp_path,
        "Nike",
        "Zomer",
        config_path=tmp_path / "config.yaml",
        camera_profiles_path=tmp_path / "profiles.yaml",
        _command_override=[
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ],
    )
    runner.start()
    assert _wait_until(lambda: runner.is_running(), qapp), "test-aanname: proces moet gestart zijn"

    runner.cancel()
    assert _wait_until(lambda: not runner.is_running(), qapp, timeout_s=5), (
        "SIGTERM-negerend proces had na de grace period alsnog gekilld moeten worden"
    )


# -- stdout line buffering --------------------------------------------------------


def test_a_json_line_split_across_two_reads_is_parsed_correctly(qapp, tmp_path):
    runner = ingest_process.IngestRunner(
        tmp_path,
        "Nike",
        "Zomer",
        config_path=tmp_path / "config.yaml",
        camera_profiles_path=tmp_path / "profiles.yaml",
        _command_override=[sys.executable, "--version"],  # nooit echt aangesproken in deze test
    )
    received: list[dict] = []
    runner.started.connect(received.append)

    first_half = b'{"event": "ingest_started", "source": "x", "clie'
    second_half = b'nt": "Nike", "project": "Zomer"}\n'

    runner._read_stdout_chunk = lambda: first_half
    runner._on_ready_read()
    assert received == []  # nog geen complete regel

    runner._read_stdout_chunk = lambda: second_half
    runner._on_ready_read()
    assert received == [{"source": "x", "client": "Nike", "project": "Zomer"}]


def test_two_complete_events_in_one_read_are_both_parsed(qapp, tmp_path):
    runner = ingest_process.IngestRunner(
        tmp_path,
        "Nike",
        "Zomer",
        config_path=tmp_path / "config.yaml",
        camera_profiles_path=tmp_path / "profiles.yaml",
        _command_override=[sys.executable, "--version"],
    )
    started: list[dict] = []
    cancelled: list[None] = []
    runner.started.connect(started.append)
    runner.cancelled.connect(lambda: cancelled.append(None))

    chunk = (
        b'{"event": "ingest_started", "source": "x", "client": "Nike", "project": "Zomer"}\n'
        b'{"event": "ingest_cancelled"}\n'
    )
    runner._read_stdout_chunk = lambda: chunk
    runner._on_ready_read()

    assert len(started) == 1
    assert len(cancelled) == 1


def test_an_unparsable_line_is_ignored_not_a_crash(qapp, tmp_path):
    runner = ingest_process.IngestRunner(
        tmp_path,
        "Nike",
        "Zomer",
        config_path=tmp_path / "config.yaml",
        camera_profiles_path=tmp_path / "profiles.yaml",
        _command_override=[sys.executable, "--version"],
    )
    started: list[dict] = []
    runner.started.connect(started.append)

    chunk = (
        b"dit is geen geldige JSON-regel\n"
        b'{"event": "ingest_started", "source": "x", "client": "Nike", "project": "Zomer"}\n'
    )
    runner._read_stdout_chunk = lambda: chunk
    runner._on_ready_read()  # mag niet crashen op de eerste, ongeldige regel

    assert len(started) == 1

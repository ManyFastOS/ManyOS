"""Fase 2: integration tests for the Controller layer.

This is the only place the Desktop app constructs IngestService (see
desktop/controller.py's module docstring: same config/camera-profile
loading, same adapters as cli.py — no second implementation). These tests
run a *real* background QThread against real (tiny) test media files, using
the exact same fixture pattern as test_cli.py, specifically to prove the
Desktop app gets identical engine behavior to the CLI, not a parallel one.

Runs headless (QT_QPA_PLATFORM=offscreen). Skips cleanly when PySide6 isn't
installed (optional `[gui]` extra).
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from many_ingest.desktop import controller

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


class _ResultCollector(QObject):
    """A real QObject (like MainWindow is), not a bare function — so the
    signal connections below get correctly recognized as cross-thread and
    queued onto the main thread, exactly like main_window.py's own bound
    methods are. Connecting to plain closures instead crashes the process:
    PySide6 can't determine a plain function's thread affinity."""

    finished_signal = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.progress: list = []
        self.summary = None
        self.failed: str | None = None

    def on_progress(self, update) -> None:
        self.progress.append(update)

    def on_finished(self, summary) -> None:
        self.summary = summary
        self.finished_signal.emit()

    def on_failed(self, message: str) -> None:
        self.failed = message
        self.finished_signal.emit()


def _run_and_wait(
    qapp, source, client, project, config_path, camera_profiles_path, timeout_ms=10_000
) -> _ResultCollector:
    """Starts a real dry run on a real background thread and blocks the
    calling (test) thread on a QEventLoop until it's done — the same shape
    as `app.exec()` in app.py, not a busy-poll.

    A tight manual `while ...: qapp.processEvents()` loop was tried first and
    intermittently *crashed the process* (subprocess calls — ffprobe — from a
    background QThread, repeatedly re-entering processEvents() from the main
    thread, is a known-flaky combination under macOS's Cocoa runloop). A
    QEventLoop bound to the finishing signal is the standard, non-reentrant
    way to wait and was reliable across 10+ repeated runs; the tight-poll
    variant was not.
    """
    collector = _ResultCollector()
    loop = QEventLoop()
    collector.finished_signal.connect(loop.quit)

    thread, _worker = controller.start_dry_run(
        source,
        client,
        project,
        on_progress=collector.on_progress,
        on_finished=collector.on_finished,
        on_failed=collector.on_failed,
        config_path=config_path,
        camera_profiles_path=camera_profiles_path,
    )

    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(loop.quit)
    timeout_timer.start(timeout_ms)

    loop.exec()

    if collector.summary is None and collector.failed is None:
        raise TimeoutError("Dry run via de Controller was niet op tijd klaar.")
    thread.wait(2000)
    return collector


def test_dry_run_via_controller_matches_a_real_ingestservice_dry_run(qapp, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    result = _run_and_wait(qapp, input_dir, "Nike", "Zomer", config_path, CAMERA_PROFILES_PATH)

    assert result.failed is None
    summary = result.summary
    assert (summary.client, summary.project, summary.dry_run) == ("Nike", "Zomer", True)
    assert summary.total_files == 1
    assert result.progress, "verwacht minstens één voortgangsupdate"

    # De kern van deze test: een dry-run via de Controller raakt bron en
    # bestemming niet aan — dezelfde garantie als de CLI's dry-run (test_cli.py).
    assert not (tmp_path / "storage").exists()
    assert list(input_dir.iterdir()) == [input_dir / "DJI_0001.MP4"]


def test_second_dry_run_after_a_real_run_reports_the_file_as_a_duplicate(qapp, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    # Een echte run, rechtstreeks tegen de engine (niet via de Controller) om
    # een bestaande manifest-entry klaar te zetten — bewijst dat de
    # Controller's dry-run dezelfde ManyFast Asset Schema ziet als de CLI,
    # geen eigen kopie van de status.
    service = controller.build_ingest_service(config_path, CAMERA_PROFILES_PATH)
    service.run(source=input_dir, client="Nike", project="Zomer", dry_run=False)

    result = _run_and_wait(qapp, input_dir, "Nike", "Zomer", config_path, CAMERA_PROFILES_PATH)

    assert result.summary.duplicates == 1


def test_missing_ffprobe_produces_a_friendly_message_not_a_stacktrace(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("many_ingest.core.ingest_service.is_ffprobe_available", lambda: False)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    result = _run_and_wait(qapp, input_dir, "Nike", "Zomer", config_path, CAMERA_PROFILES_PATH)

    assert result.summary is None
    message = result.failed
    assert message and "ffprobe" not in message.lower() and "traceback" not in message.lower()


def test_invalid_config_produces_a_friendly_message_not_a_stacktrace(qapp, tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    broken_config_path = tmp_path / "broken_config.yaml"
    broken_config_path.write_text("manifest_path: /tmp/x.json\n")  # geen storage_root

    result = _run_and_wait(
        qapp, input_dir, "Nike", "Zomer", broken_config_path, CAMERA_PROFILES_PATH
    )

    assert result.summary is None
    assert result.failed


def test_unreadable_source_produces_a_friendly_message_not_a_stacktrace(qapp, tmp_path):
    missing_source = tmp_path / "does_not_exist"
    config_path = _write_config(tmp_path)

    result = _run_and_wait(qapp, missing_source, "Nike", "Zomer", config_path, CAMERA_PROFILES_PATH)

    assert result.summary is None
    assert result.failed

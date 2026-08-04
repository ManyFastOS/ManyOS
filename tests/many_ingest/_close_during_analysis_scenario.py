"""Standalone scenario script for test_closing_the_window_during_analysis_does_not_crash.

Run as its own fresh process (see that test) rather than inside the shared
pytest process. Reproduces exactly one real app session — window opens, an
analysis starts, the window closes mid-analysis — which is what a real
`many-ingest-desktop` run looks like: one process, ending when the window
closes. Running it in-process alongside ~90 other tests (many of which spawn
real ffprobe subprocesses on other QThreads) turned out to intermittently
segfault deep inside Python's `subprocess`/`selectors` internals — a real,
reproducible race, but one between *unrelated, independent* Qt/thread
sessions sharing a single long-lived process, which cannot happen in actual
usage (closing the window ends the process; nothing else runs afterward in
the same process). Isolating this scenario in its own process sidesteps that
race by construction instead of chasing a fix for a condition real usage
never creates.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import MainWindow


class _SlowWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def run(self) -> None:
        time.sleep(0.5)
        self.finished.emit(None)


def _fake_start_dry_run(source, client, project, *, on_progress, on_finished, on_failed, config_path, camera_profiles_path):
    worker = _SlowWorker()
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


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow(detect_volumes=lambda: [], start_dry_run=_fake_start_dry_run)
    window._set_manual_source(Path.cwd())
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer")
    window.choose_button().click()

    assert window._analysis_thread.isRunning(), "test-aanname: de analyse moet nog bezig zijn"

    window.close()  # dit crashte vroeger het hele proces (SIGABRT)

    assert window._analysis_thread is None or not window._analysis_thread.isRunning()
    print("OK")


if __name__ == "__main__":
    main()

"""Fast, in-process unit tests for `desktop/thread_lifecycle.py` — the
central registry that guarantees QApplication never quits while a QThread
is still alive (see that module's docstring, and
test_desktop_thread_lifecycle.py for the slower, subprocess-based
integration/crash-regression coverage of the same guarantee under real app
sessions).
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QThread, QEventLoop, Signal
from PySide6.QtWidgets import QApplication

from many_ingest.desktop import thread_lifecycle


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_registry(qapp):
    """Normalizes registry state between tests. Without this, a thread from
    a previous test whose OS-level work is done but whose queued `finished`
    deregistration event was never pumped (see
    `test_a_finished_thread_deregisters_itself`'s docstring) would leak a
    stale `active_thread_count()` into the next test. `shutdown()` is the
    correct tool for this exact problem — it doesn't depend on the queued
    event having been drained, which is also why production code uses it
    rather than relying on passive deregistration alone."""
    thread_lifecycle.shutdown()
    yield
    thread_lifecycle.shutdown()


class _Worker(QObject):
    finished = Signal()

    def __init__(self, delay_seconds: float = 0.05) -> None:
        super().__init__()
        self._delay_seconds = delay_seconds

    def run(self) -> None:
        time.sleep(self._delay_seconds)
        self.finished.emit()


def _make_registered_thread(delay_seconds: float = 0.05) -> tuple[QThread, _Worker]:
    worker = _Worker(delay_seconds)
    thread = QThread()
    thread_lifecycle.register(thread)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker


def test_register_increments_active_count(qapp):
    before = thread_lifecycle.active_thread_count()
    thread, worker = _make_registered_thread()
    assert thread_lifecycle.active_thread_count() == before + 1

    worker.finished.connect(thread.quit)
    thread.start()
    thread.wait(2000)


def test_a_finished_thread_deregisters_itself(qapp):
    """`thread.finished` has two independent queued connections here: the
    registry's own `_on_thread_finished` (connected first, inside
    `register()`) and this test's own wait-loop-quitter (connected after).
    Queued connections to the same signal are posted as separate events —
    nothing guarantees both are drained within the same event-loop
    iteration (this was discovered the hard way while building this fix:
    an earlier version of this test used a single `QEventLoop` quit by
    `thread.finished` directly and was flaky for exactly this reason). So
    after `thread.wait()` confirms the OS thread itself has stopped, this
    explicitly pumps the event queue a few times to let the registry's own
    queued deregistration event drain, instead of assuming one loop
    iteration was enough — this is precisely why `shutdown()` (tested
    separately below) does not rely on this passive path alone and clears
    its list explicitly."""
    before = thread_lifecycle.active_thread_count()
    thread, worker = _make_registered_thread()
    worker.finished.connect(thread.quit)

    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    thread.start()
    loop.exec()
    thread.wait(2000)

    for _ in range(50):
        if thread_lifecycle.active_thread_count() == before:
            break
        qapp.processEvents()
        time.sleep(0.01)

    assert thread_lifecycle.active_thread_count() == before


def test_shutdown_blocks_until_the_thread_has_genuinely_stopped(qapp):
    before = thread_lifecycle.active_thread_count()
    thread, worker = _make_registered_thread(delay_seconds=0.2)
    worker.finished.connect(thread.quit)
    thread.start()

    assert thread.isRunning()
    thread_lifecycle.shutdown()

    assert not thread.isRunning()
    assert thread_lifecycle.active_thread_count() == before


def test_shutdown_with_no_active_threads_is_a_harmless_no_op(qapp):
    assert thread_lifecycle.active_thread_count() == 0
    thread_lifecycle.shutdown()  # mag nooit crashen of hangen
    assert thread_lifecycle.active_thread_count() == 0


def test_shutdown_is_idempotent_when_called_twice_in_a_row(qapp):
    thread, worker = _make_registered_thread(delay_seconds=0.05)
    worker.finished.connect(thread.quit)
    thread.start()

    thread_lifecycle.shutdown()
    thread_lifecycle.shutdown()  # tweede keer moet net zo veilig zijn als de eerste

    assert not thread.isRunning()
    assert thread_lifecycle.active_thread_count() == 0


def test_shutdown_stops_multiple_concurrently_registered_threads(qapp):
    before = thread_lifecycle.active_thread_count()
    threads = []
    for _ in range(5):
        thread, worker = _make_registered_thread(delay_seconds=0.1)
        worker.finished.connect(thread.quit)
        threads.append(thread)

    for thread in threads:
        thread.start()

    assert thread_lifecycle.active_thread_count() == before + 5

    thread_lifecycle.shutdown()

    assert all(not t.isRunning() for t in threads)
    assert thread_lifecycle.active_thread_count() == before

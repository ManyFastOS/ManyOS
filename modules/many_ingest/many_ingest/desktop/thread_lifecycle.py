"""Central registry and shutdown guarantee for every QThread the desktop
app creates.

Why this exists, on top of the per-window `_wait_for_analysis_to_stop()`
hardening from earlier rounds: that logic only ever knew about ONE specific
thread (`MainWindow._analysis_thread`). It is correct for that one thread,
but it is a hand-written, ad hoc guarantee that has to be re-derived and
re-wired correctly for every new background QThread this app might ever
grow (a future volume-scan thread, a future second worker kind, ...) — easy
to forget, and each miss reopens the exact "QThread: Destroyed while thread
is still running" / SIGABRT-in-QThread::~QThread class of crash this
project has already hit twice.

The rule this module enforces, structurally rather than by convention:
**QApplication must never be allowed to actually quit while this registry's
thread list is non-empty.** Every QThread the desktop app creates registers
itself here at creation time (see `register()`); `shutdown()` is the single
choke point every quit path must call before letting the app actually exit
— it requests every still-registered thread to stop, waits for each one in
turn, and only returns once none of them are running anymore. Whether that
turns out to be zero threads, one, or several, callers don't need to know
or enumerate them.

Usage — registered from TWO independent call sites, deliberately, not one:
- `controller.start_dry_run()` registers the thread it creates, right after
  construction. This protects every caller of the real factory function,
  including callers that go straight to the Controller layer without a
  MainWindow at all (see e.g. test_desktop_controller.py's `_run_and_wait`).
- `MainWindow._start_preview()` ALSO registers whatever thread it ends up
  tracking as `self._analysis_thread`, regardless of which `start_dry_run`
  implementation supplied it. This is not redundant with the point above:
  MainWindow accepts an INJECTED `start_dry_run` (real or a test double —
  see main_window.py's constructor), and a test double is free to create
  its own raw `QThread` without ever going through
  `controller.start_dry_run()`. A first version of this fix registered only
  at the Controller call site and broke exactly that case: a regression
  test using such a double (`_close_during_analysis_scenario.py`, a fake
  `start_dry_run` with its own `moveToThread`/`QThread()`) went back to
  reproducing the original SIGABRT, because the thread it created was never
  in the registry, so `shutdown()` had nothing to wait on even though
  MainWindow was still tracking a live thread. Registering at the point
  MainWindow starts tracking a thread — its actual ownership boundary —
  closes that gap regardless of which factory produced the thread.
  `register()` is idempotent (see below), so both call sites firing for the
  one real production path is harmless.
- Deregistration is automatic, driven by the thread's own `finished` signal
  (Qt only emits this once the OS thread has genuinely stopped, never
  merely once `quit()` was requested) — never by a caller explicitly
  forgetting to remove it, and never before that signal fires (see
  `shutdown()`'s docstring for why that ordering matters, and why
  `shutdown()` does not rely on this passive path alone).
- Whoever owns the app's quit paths (MainWindow.closeEvent(),
  QApplication.aboutToQuit — see main_window.py and app.py) calls
  `shutdown()` before actually letting the window/app close. Safe to call
  from multiple quit paths and multiple times — idempotent by construction:
  a thread that already stopped is simply no longer in the list.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread


class _ThreadRegistry(QObject):
    """A QObject (not a bare module-level list) specifically so the
    `finished` deregistration slot below is a genuine bound method — a
    plain function/lambda has no thread affinity PySide can determine,
    which silently downgrades a cross-thread signal connection to a direct
    (same-thread-as-emitter) call instead of a queued one. That exact
    mistake, made once already while building this fix's own regression
    scenario, produced "QObject::setParent: Cannot set parent, new parent
    is in a different thread" and "QThread::wait: Thread tried to wait on
    itself" — this class is written to make that mistake structurally
    impossible here, using the same `sender()`-based pattern MainWindow
    already uses for its own `_on_analysis_thread_finished`."""

    def __init__(self) -> None:
        super().__init__()
        self._active: list[QThread] = []

    def register(self, thread: QThread) -> None:
        # Idempotent: the same thread can legitimately be registered from
        # more than one call site (see this module's docstring on why both
        # controller.start_dry_run() and MainWindow register — this guards
        # against double-registering the SAME thread when both fire for the
        # one production path, without needing either call site to know
        # about the other).
        if thread in self._active:
            return
        self._active.append(thread)
        thread.finished.connect(self._on_thread_finished)

    def _on_thread_finished(self) -> None:
        # Alleen verwijderen in reactie op het echte finished-signaal (Qt
        # geeft dit pas nadat de OS-thread daadwerkelijk gestopt is, niet
        # zodra quit() slechts is aangevraagd) — nooit vroeger.
        thread = self.sender()
        if thread in self._active:
            self._active.remove(thread)

    def shutdown(self) -> None:
        # Kopie: `_on_thread_finished` kan `self._active` muteren zodra
        # `wait()` hieronder de event-verwerking voor die thread laat
        # doorlopen — itereren over de originele lijst zou dan een item
        # kunnen overslaan.
        for thread in list(self._active):
            if thread.isRunning():
                thread.quit()
                thread.wait()
            assert not thread.isRunning(), (
                "QApplication mag nooit afsluiten terwijl er nog een QThread draait"
            )
        # Belt-and-braces: `_on_thread_finished` zou de lijst inmiddels al
        # geleegd moeten hebben via de finished-signalen hierboven, maar dit
        # garandeert het expliciet i.p.v. daarvan afhankelijk te zijn.
        self._active.clear()

    def active_count(self) -> int:
        return len(self._active)


_registry = _ThreadRegistry()


def register(thread: QThread) -> None:
    """Registers a QThread right after it's created (before `.start()`).
    See this module's docstring — call this for every QThread the desktop
    app ever creates, not only the analysis worker's."""
    _registry.register(thread)


def shutdown() -> None:
    """Requests every still-registered thread to stop, waits for each one,
    and confirms none are running anymore, before returning. Call this
    before actually letting the window/app close (see MainWindow.closeEvent()
    and app.py's `aboutToQuit` wiring) — never let QApplication quit while
    `active_thread_count()` could still be non-zero."""
    _registry.shutdown()


def active_thread_count() -> int:
    """For tests/diagnostics: how many QThreads this registry currently
    believes are still active (registered, not yet confirmed finished)."""
    return _registry.active_count()

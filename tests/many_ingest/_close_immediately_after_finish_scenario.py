"""Standalone scenario script: closes the window from INSIDE the same signal
delivery that reports a finished analysis — i.e. as early as physically
possible after `worker.finished` fires, before the sibling queued connection
(`worker.finished.connect(thread.quit)`, wired in controller.start_dry_run)
has necessarily had a chance to run.

Why this is the adversarial case: `worker.finished` (emitted from the
background QThread) has two independent queued connections delivered onto
the GUI thread's event loop: one to MainWindow's own `_on_analysis_finished`
(renders the preview) and one to `thread.quit()` (owned by controller.py's
`start_dry_run()`). Queued connections to the same signal are posted as
separate events; nothing guarantees `thread.quit()`'s event is drained
before `_on_analysis_finished`'s event, or before this script acts on it.
If the window is closed at the earliest possible moment after
`_on_analysis_finished` runs, `self._analysis_thread` may still be a live,
running QThread whose own `quit()` request hasn't been processed yet
(closeEvent()'s own `thread.quit(); thread.wait()` should still cover this
via `_wait_for_analysis_to_stop()` — this script's purpose is to prove that,
under real subprocess-driven timing, not the artificial 0.5s sleep of
`_SlowWorker`).

Uses a real analysis (real ffprobe subprocess call) rather than the
`_SlowWorker` fake used by the other scenario scripts in this directory, to
get realistic (fast, sub-tick) completion timing instead of an artificially
slow one — the fast case is the one that stresses the queued-event-ordering
race described above.

IMPORTANT implementation note: the "close immediately" hook below patches
`MainWindow._render_preview` (a plain method called FROM `_on_analysis_finished`,
never itself a signal receiver), not `_on_analysis_finished` itself. An
earlier version of this script patched `_on_analysis_finished` directly with
a bare function and reconnected it as the `worker.finished` receiver — this
is exactly the plain-function-as-cross-thread-receiver anti-pattern this
codebase's own tests warn about elsewhere (see `_Waiter`/`_ResultCollector`
in the other scenario/test files): PySide6 cannot determine a bare
function's thread affinity, so the connection silently ran on the
*background* worker thread instead of being queued to the GUI thread —
producing "QObject::setParent: Cannot set parent, new parent is in a
different thread" and "QThread::wait: Thread tried to wait on itself" (a
thread waiting on itself), and hanging the whole process. That was a bug in
this test script, not in the application: `_on_analysis_finished` stays the
real, correctly-thread-affine bound method (MainWindow is a QObject), so it
is still delivered via a genuine queued cross-thread connection exactly as
in production; only the plain internal method it calls is swapped out.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)

    tmp_dir = Path(sys.argv[1])
    input_dir = tmp_dir / "input"
    input_dir.mkdir(exist_ok=True)
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = tmp_dir / "config.yaml"
    config_path.write_text(
        f"storage_root: {tmp_dir / 'storage'}\n"
        f"manifest_path: {tmp_dir / 'asset_schema.json'}\n"
        f"log_dir: {tmp_dir / 'logs'}\n"
    )
    camera_profiles_path = (
        Path(__file__).resolve().parents[2]
        / "modules"
        / "many_ingest"
        / "config"
        / "camera_profiles.yaml"
    )

    window = MainWindow(
        detect_volumes=lambda: [], config_path=config_path, camera_profiles_path=camera_profiles_path
    )
    app.aboutToQuit.connect(window._wait_for_analysis_to_stop)  # exactly what app.py does
    window._set_manual_source(input_dir)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer")

    # `_on_analysis_finished` blijft de echte, bound method (correct
    # thread-affine als signal-ontvanger — zie de moduledocstring hierboven).
    # Alleen de gewone interne aanroep die het doet (`_render_preview`, geen
    # signal-ontvanger) wordt vervangen, zodat de sluitactie nog steeds
    # gegarandeerd op de GUI-thread uitgevoerd wordt, binnen dezelfde
    # afhandeling van het `finished`-signaal.
    real_render_preview = window._render_preview

    def render_then_close_immediately(summary) -> None:
        real_render_preview(summary)  # exactly what the real code does
        # Sluit het venster op het vroegst mogelijke moment ná afronding —
        # nog binnen de afhandeling van hetzelfde `finished`-signaal, vóór de
        # event-loop kans heeft gehad om de andere, los gequeuede
        # `thread.quit()`-verbinding (in controller.start_dry_run) te
        # verwerken.
        window.close()
        app.quit()
        print("OK")

    window._render_preview = render_then_close_immediately
    window.choose_button().click()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

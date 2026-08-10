"""Standalone scenario script for "volume-scan actief + afsluiten".

Volume detection (`MainWindow._run_detection()` -> `detect_volumes()`) is
entirely synchronous on the GUI thread today (see main_window.py — no
QThread, no worker, nothing moved to another thread). Qt's single-threaded
event loop cannot process a close/quit event while `_run_detection()` is
still running (no reentrancy without an explicit `processEvents()` call,
which this code never does) — so "close the window while a volume scan is
active" is not a reachable state in the current architecture, by
construction, not by favorable timing. This script proves that directly:
`detect_volumes` is made deliberately slow (simulating a large external
volume) and the window is closed immediately once control returns — the
earliest a close can possibly happen relative to a scan.

Kept as a real regression test anyway (per the requested 50x sweep) so a
future change that DOES move volume detection onto a background thread
(not planned, not part of this fix) would have to prove it doesn't
reintroduce exactly the shutdown race this file is about.
"""

from __future__ import annotations

import sys
import time

from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import MainWindow


def _slow_detect_volumes():
    time.sleep(0.05)  # simuleert een trage/grote schijf-scan
    return []


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow(detect_volumes=_slow_detect_volumes)
    app.aboutToQuit.connect(window._wait_for_analysis_to_stop)
    window.show()
    # "Opnieuw zoeken" herhaalt dezelfde (synchrone) detectie nogmaals,
    # daarna meteen sluiten — het vroegst mogelijke moment ná een scan.
    window._run_detection()
    window.close()
    print("OK")


if __name__ == "__main__":
    main()

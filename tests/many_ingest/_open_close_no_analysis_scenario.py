"""Standalone scenario script: open and close the window without ever
starting an analysis — the baseline "nothing is running" case. No QThread
is ever created here (detect_volumes returns nothing, no click happens), so
this should be trivially safe; included as a control alongside the other
scenarios so the full 50x/50x/.../50x regression sweep covers the whole
lifecycle, not only the cases already known to be risky.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow(detect_volumes=lambda: [])
    app.aboutToQuit.connect(window._wait_for_analysis_to_stop)  # exactly what app.py does
    window.show()
    window.close()
    print("OK")


if __name__ == "__main__":
    main()

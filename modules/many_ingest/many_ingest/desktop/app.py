"""Composition root for Many Ingest Desktop — wiring only, no business logic.

Mirrors cli.py's role (see CLAUDE.md: "CLI is a thin adapter only").

Until Fase 3.5, this read a fixed `storage_root` from config.yaml purely so
source-volume detection could exclude "the" destination disk. As of Fase 3.5
(Dynamic Destination Selection) there is no longer a single, fixed
destination to exclude — ManyFast uses several external destination disks,
and which one is used is chosen per ingest, inside MainWindow, after a
source is already selected (see desktop/volumes.py's
`list_destination_volumes`, which excludes the chosen SOURCE's disk instead
— the safety rule now runs in the other direction). Source detection here is
therefore unconditional again, same as Fase 1.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import MainWindow
from many_ingest.desktop.theme import STYLE_SHEET


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE_SHEET)

    window = MainWindow()
    # Required in addition to MainWindow.closeEvent(), not instead of it:
    # QApplication.quit() — what macOS actually sends for Cmd+Q / the
    # app-menu Quit item, confirmed by reproduction — never delivers a
    # QCloseEvent to the window, so closeEvent() alone misses that path.
    # aboutToQuit fires for every quit path. See
    # MainWindow._wait_for_preview_to_stop, _wait_for_ingest_to_stop and
    # _wait_for_eject_to_stop for the shared, idempotent logic (each stops
    # its own QProcess-backed runner).
    app.aboutToQuit.connect(window._wait_for_preview_to_stop)
    app.aboutToQuit.connect(window._wait_for_ingest_to_stop)
    app.aboutToQuit.connect(window._wait_for_eject_to_stop)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

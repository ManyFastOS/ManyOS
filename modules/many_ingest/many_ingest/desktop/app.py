"""Composition root for Many Ingest Desktop — wiring only, no business logic.

Mirrors cli.py's role (see CLAUDE.md: "CLI is a thin adapter only"). Fase 0
does not construct IngestService or any adapter yet — it only opens the
window and lets the user pick a folder via the native dialog.
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
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

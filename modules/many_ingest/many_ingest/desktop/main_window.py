"""Fase 0: a bare application window — no coupling to the ingest engine yet.

Shows the empty state and lets the user manually pick a source folder via the
native macOS folder picker. Later phases wire this to IngestService; this
window doesn't know the engine exists (see docs/MANY_INGEST_V1_UX_DESIGN.md,
hoofdstuk 10.1/10.2, and the Fase 0 scope in the implementatieplan, hoofdstuk 11).

The click handler and the state update are deliberately split
(`_on_choose_source_clicked` vs. `_set_source`) so the resulting UI state is
testable without driving the native, un-automatable system dialog.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QVBoxLayout, QWidget

EMPTY_STATE_TEXT = "Sluit een SD-kaart of SSD aan om te beginnen."
CHOOSE_BUTTON_TEXT = "Kies een map"
CHOOSE_ANOTHER_BUTTON_TEXT = "Andere map kiezen"


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Many Ingest")
        self.setMinimumSize(480, 320)

        self.selected_source: Path | None = None

        self._status_label = QLabel(EMPTY_STATE_TEXT)
        self._status_label.setObjectName("statusLabel")
        self._status_label.setWordWrap(True)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._choose_button = QPushButton(CHOOSE_BUTTON_TEXT)
        self._choose_button.setObjectName("primaryButton")
        self._choose_button.clicked.connect(self._on_choose_source_clicked)

        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self._status_label)
        layout.addSpacing(20)
        layout.addWidget(self._choose_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    def _on_choose_source_clicked(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Kies een bronmap")
        if chosen:
            self._set_source(Path(chosen))

    def _set_source(self, path: Path) -> None:
        self.selected_source = path
        self._status_label.setText(path.name)
        self._choose_button.setText(CHOOSE_ANOTHER_BUTTON_TEXT)

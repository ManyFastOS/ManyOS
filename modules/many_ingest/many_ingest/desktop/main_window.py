"""Fase 1: automatic source detection layered on the Fase 0 shell.

Still no coupling to the ingest engine (see docs/MANY_INGEST_V1_UX_DESIGN.md,
hoofdstuk 3/4, and CLAUDE.md: no business logic in the GUI). Volume detection
itself lives in `desktop/volumes.py`, a separate, testable OS-integration
layer — this window only renders whatever it returns and reacts to clicks.

Four states, one window (content is swapped, no new dialogs/screens):
- empty: no candidates found — manual picker + "Opnieuw zoeken"
- single: exactly one candidate — auto-selected immediately
- chooser: multiple candidates — one card per volume
- selected: name + quick media summary, never a full path

`detect_volumes` is injected (defaults to the real /Volumes scan) so tests can
drive every state deterministically without touching the real filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from many_ingest.desktop.volumes import (
    VolumeInfo,
    format_size,
    list_candidate_volumes,
    scan_media_summary,
)

EMPTY_STATE_TEXT = "Sluit een SD-kaart of SSD aan om te beginnen."
CHOOSE_BUTTON_TEXT = "Kies een map"
CHOOSE_ANOTHER_BUTTON_TEXT = "Andere map kiezen"
OTHER_DISK_TEXT = "Andere schijf gebruiken"
RETRY_TEXT = "Opnieuw zoeken"
CHOOSER_TITLE_TEXT = "Welke schijf wil je gebruiken?"

DetectVolumes = Callable[[], list[VolumeInfo]]


class MainWindow(QWidget):
    def __init__(self, detect_volumes: DetectVolumes | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Many Ingest")
        self.setMinimumSize(480, 320)

        self._detect_volumes: DetectVolumes = detect_volumes or (
            lambda: list_candidate_volumes(storage_root=None)
        )
        self.selected_source: Path | None = None

        self._outer_layout = QVBoxLayout(self)
        self._content: QWidget | None = None

        self._run_detection()

    # -- public introspection (used by the app and by tests) ----------------

    def current_message(self) -> str:
        label = self._content.findChild(QLabel, "statusLabel") if self._content else None
        return label.text() if label else ""

    def current_detail(self) -> str:
        label = self._content.findChild(QLabel, "captionLabel") if self._content else None
        return label.text() if label else ""

    def choose_button(self) -> QPushButton | None:
        return self._content.findChild(QPushButton, "primaryButton") if self._content else None

    def secondary_action_button(self) -> QPushButton | None:
        return self._content.findChild(QPushButton, "linkButton") if self._content else None

    def volume_cards(self) -> list[QPushButton]:
        return self._content.findChildren(QPushButton, "volumeCard") if self._content else []

    # -- detection ------------------------------------------------------------

    def _run_detection(self) -> None:
        candidates = self._detect_volumes()
        if len(candidates) == 1:
            self._select_volume(candidates[0])
        elif len(candidates) > 1:
            self._render_chooser(candidates)
        else:
            self._render_empty_state()

    def _select_volume(self, volume: VolumeInfo) -> None:
        self.selected_source = volume.path
        self._render_selected(
            name=volume.name,
            media_file_count=volume.media_file_count,
            media_total_bytes=volume.media_total_bytes,
            via_auto_detection=True,
        )

    # -- manual folder picking (native dialog, unchanged behavior) -----------

    def _on_choose_source_clicked(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Kies een bronmap")
        if chosen:
            self._set_manual_source(Path(chosen))

    def _set_manual_source(self, path: Path) -> None:
        self.selected_source = path
        media_file_count, media_total_bytes = scan_media_summary(path)
        self._render_selected(
            name=path.name,
            media_file_count=media_file_count,
            media_total_bytes=media_total_bytes,
            via_auto_detection=False,
        )

    # -- rendering --------------------------------------------------------------

    def _set_content(self, widget: QWidget) -> None:
        if self._content is not None:
            self._outer_layout.removeWidget(self._content)
            self._content.deleteLater()
        self._content = widget
        self._outer_layout.addWidget(widget)

    def _render_empty_state(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        label = QLabel(EMPTY_STATE_TEXT)
        label.setObjectName("statusLabel")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addSpacing(20)

        choose_button = QPushButton(CHOOSE_BUTTON_TEXT)
        choose_button.setObjectName("primaryButton")
        choose_button.clicked.connect(self._on_choose_source_clicked)
        layout.addWidget(choose_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(12)

        retry_button = QPushButton(RETRY_TEXT)
        retry_button.setObjectName("linkButton")
        retry_button.clicked.connect(self._run_detection)
        layout.addWidget(retry_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)

    def _render_chooser(self, candidates: list[VolumeInfo]) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        title = QLabel(CHOOSER_TITLE_TEXT)
        title.setObjectName("statusLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(16)

        for volume in candidates:
            card = QPushButton(
                f"{volume.name}\n{format_size(volume.capacity_bytes)} · "
                f"{_pluralize_media(volume.media_file_count)}"
            )
            card.setObjectName("volumeCard")
            card.clicked.connect(lambda checked=False, v=volume: self._select_volume(v))
            layout.addWidget(card)
            layout.addSpacing(8)

        layout.addStretch()
        self._set_content(content)

    def _render_selected(
        self,
        *,
        name: str,
        media_file_count: int,
        media_total_bytes: int,
        via_auto_detection: bool,
    ) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addStretch()

        name_label = QLabel(name)
        name_label.setObjectName("statusLabel")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        detail_label = QLabel(
            f"{_pluralize_media(media_file_count)} · {format_size(media_total_bytes)}"
        )
        detail_label.setObjectName("captionLabel")
        detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(detail_label)
        layout.addSpacing(16)

        other_button = QPushButton(OTHER_DISK_TEXT if via_auto_detection else CHOOSE_ANOTHER_BUTTON_TEXT)
        other_button.setObjectName("linkButton")
        other_button.clicked.connect(self._on_choose_source_clicked)
        layout.addWidget(other_button, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        self._set_content(content)


def _pluralize_media(count: int) -> str:
    return f"{count} mediabestand" if count == 1 else f"{count} mediabestanden"

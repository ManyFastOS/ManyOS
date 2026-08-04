"""Fase 0 smoke test for the desktop shell.

Runs headless (QT_QPA_PLATFORM=offscreen) so it works without a display, both
in CI and in this sandbox. Only tests state MainWindow itself manages
(_set_source) — the native QFileDialog can't be driven headlessly and isn't
our logic to test.

Skips cleanly (not a hard failure) when PySide6 isn't installed, since it's an
optional `[gui]` extra — a CLI-only dev install must still pass the rest of
the suite untouched.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import (
    CHOOSE_ANOTHER_BUTTON_TEXT,
    CHOOSE_BUTTON_TEXT,
    EMPTY_STATE_TEXT,
    MainWindow,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_initial_state_shows_empty_state_and_choose_button(qapp):
    window = MainWindow()

    assert window._status_label.text() == EMPTY_STATE_TEXT
    assert window._choose_button.text() == CHOOSE_BUTTON_TEXT
    assert window.selected_source is None


def test_selecting_a_source_shows_only_the_folder_name(qapp, tmp_path):
    window = MainWindow()
    chosen = tmp_path / "SD_CARD_1"
    chosen.mkdir()

    window._set_source(chosen)

    assert window.selected_source == chosen
    assert window._status_label.text() == "SD_CARD_1"
    assert window._choose_button.text() == CHOOSE_ANOTHER_BUTTON_TEXT

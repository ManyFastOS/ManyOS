"""Desktop shell tests — Fase 0 (bare window) + Fase 1 (volume detection UI).

Runs headless (QT_QPA_PLATFORM=offscreen). `detect_volumes` is always injected
so these tests never touch the real /Volumes or depend on what's plugged into
the machine running them — see desktop/volumes.py for the OS-integration
layer these UI tests deliberately don't exercise directly.

Skips cleanly (not a hard failure) when PySide6 isn't installed, since it's an
optional `[gui]` extra.
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
    OTHER_DISK_TEXT,
    RETRY_TEXT,
    MainWindow,
)
from many_ingest.desktop.volumes import VolumeInfo


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _volume(name: str, path, *, capacity=500_000_000_000, media_count=10, media_bytes=1_000_000):
    return VolumeInfo(
        name=name,
        path=path,
        capacity_bytes=capacity,
        media_file_count=media_count,
        media_total_bytes=media_bytes,
    )


# -- Fase 0 behavior, now driven through an injected (empty) detection result --


def test_no_candidates_shows_empty_state_and_choose_button(qapp):
    window = MainWindow(detect_volumes=lambda: [])

    assert window.current_message() == EMPTY_STATE_TEXT
    assert window.choose_button().text() == CHOOSE_BUTTON_TEXT
    assert window.selected_source is None


def test_selecting_a_manual_folder_shows_only_name_and_media_summary(qapp, tmp_path):
    window = MainWindow(detect_volumes=lambda: [])
    chosen = tmp_path / "SD_CARD_1"
    chosen.mkdir()
    (chosen / "clip.mp4").write_bytes(b"x" * 1000)
    (chosen / "notes.txt").write_bytes(b"irrelevant")

    window._set_manual_source(chosen)

    assert window.selected_source == chosen
    assert window.current_message() == "SD_CARD_1"
    assert window.current_detail() == "1 mediabestand · 1000 B"
    assert window.secondary_action_button().text() == CHOOSE_ANOTHER_BUTTON_TEXT


def test_retry_button_re_runs_detection(qapp, tmp_path):
    results = [[], [_volume("SD_CARD_1", tmp_path)]]
    window = MainWindow(detect_volumes=lambda: results.pop(0))

    assert window.current_message() == EMPTY_STATE_TEXT
    assert window.secondary_action_button().text() == RETRY_TEXT
    window.secondary_action_button().click()

    assert window.current_message() == "SD_CARD_1"
    assert window.selected_source == tmp_path


# -- Fase 1: automatic selection ------------------------------------------------


def test_single_candidate_is_selected_automatically(qapp, tmp_path):
    volume = _volume("SD_CARD_1", tmp_path, media_count=214, media_bytes=214_000_000_000)
    window = MainWindow(detect_volumes=lambda: [volume])

    assert window.selected_source == tmp_path
    assert window.current_message() == "SD_CARD_1"
    assert window.current_detail() == "214 mediabestanden · 199.3 GB"
    assert window.secondary_action_button().text() == OTHER_DISK_TEXT


def test_multiple_candidates_show_a_chooser(qapp, tmp_path):
    volumes = [
        _volume("SD_CARD_1", tmp_path / "a", capacity=64_000_000_000, media_count=64),
        _volume("EXT_DRIVE_2", tmp_path / "b", capacity=1_000_000_000_000, media_count=340),
    ]
    window = MainWindow(detect_volumes=lambda: volumes)

    assert window.selected_source is None
    cards = window.volume_cards()
    assert len(cards) == 2
    assert "SD_CARD_1" in cards[0].text()
    assert "64 mediabestanden" in cards[0].text()
    assert "EXT_DRIVE_2" in cards[1].text()


def test_choosing_a_card_selects_that_volume(qapp, tmp_path):
    volumes = [
        _volume("SD_CARD_1", tmp_path / "a"),
        _volume("EXT_DRIVE_2", tmp_path / "b"),
    ]
    window = MainWindow(detect_volumes=lambda: volumes)

    window.volume_cards()[1].click()

    assert window.selected_source == tmp_path / "b"
    assert window.current_message() == "EXT_DRIVE_2"
    assert window.secondary_action_button().text() == OTHER_DISK_TEXT

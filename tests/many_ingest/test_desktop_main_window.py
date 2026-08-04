"""Desktop shell tests — Fase 0 (bare window), Fase 1 (volume detection UI)
and Fase 2 (dry-run preview UI).

Runs headless (QT_QPA_PLATFORM=offscreen). `detect_volumes` and
`start_dry_run` are always injected so these tests never touch the real
/Volumes, a real config, or a real background thread — see
desktop/volumes.py and desktop/controller.py (and
tests/many_ingest/test_desktop_controller.py) for that OS/engine-integration
layer, exercised for real elsewhere. Here we only verify MainWindow renders
whatever it's handed, correctly.

Skips cleanly (not a hard failure) when PySide6 isn't installed, since it's an
optional `[gui]` extra.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from many_ingest.core.ingest_service import ProgressUpdate
from many_ingest.core.report import IngestSummary
from many_ingest.desktop.main_window import (
    ANALYZING_TEXT,
    BACK_TEXT,
    CHOOSE_ANOTHER_BUTTON_TEXT,
    CHOOSE_BUTTON_TEXT,
    EMPTY_STATE_TEXT,
    OTHER_DISK_TEXT,
    PREVIEW_TITLE_TEXT,
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


class _CapturingStartDryRun:
    """A fake Controller entry point — records each call's arguments and
    callbacks instead of touching IngestService or a real thread, so tests
    can drive on_progress/on_finished/on_failed at will and inspect exactly
    what MainWindow asked for."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self,
        source,
        client,
        project,
        *,
        on_progress,
        on_finished,
        on_failed,
        config_path,
        camera_profiles_path,
    ):
        self.calls.append(
            {
                "source": source,
                "client": client,
                "project": project,
                "on_progress": on_progress,
                "on_finished": on_finished,
                "on_failed": on_failed,
            }
        )
        return None, None


def _make_summary(**overrides) -> IngestSummary:
    defaults = dict(
        project="Zomer Campagne",
        client="Nike",
        dry_run=True,
        total_files=5,
        video_count=4,
        audio_count=1,
        unknown_type_count=1,
        camera_profile_counts={"Sony FX6": 3, "Sony FX3": 1, "Onbekend": 1},
        duplicates=2,
        name_conflicts_resolved=1,
        errors=0,
        total_bytes=5_000_000_000,
        duration_seconds=3.2,
        log_path="/irrelevant/for/this/test.jsonl",
        safe_to_delete_source=False,
    )
    defaults.update(overrides)
    return IngestSummary(**defaults)


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


# -- Fase 2: client/project form + dry-run preview ------------------------------


def test_preview_button_disabled_until_both_fields_are_filled(qapp, tmp_path):
    window = MainWindow(detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)])

    assert window.choose_button().isEnabled() is False

    window.client_input().setText("Nike")
    assert window.choose_button().isEnabled() is False

    window.project_input().setText("Zomer Campagne")
    assert window.choose_button().isEnabled() is True

    window.client_input().setText("   ")  # alleen witruimte telt niet als ingevuld
    assert window.choose_button().isEnabled() is False


def test_clicking_bekijk_inhoud_calls_the_controller_with_source_client_project(qapp, tmp_path):
    starter = _CapturingStartDryRun()
    window = MainWindow(detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)], start_dry_run=starter)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer Campagne")

    window.choose_button().click()

    assert len(starter.calls) == 1
    call = starter.calls[0]
    assert (call["source"], call["client"], call["project"]) == (tmp_path, "Nike", "Zomer Campagne")
    assert window.current_message() == ANALYZING_TEXT


def test_progress_updates_drive_the_progress_bar_and_status(qapp, tmp_path):
    starter = _CapturingStartDryRun()
    window = MainWindow(detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)], start_dry_run=starter)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer Campagne")
    window.choose_button().click()

    on_progress = starter.calls[0]["on_progress"]
    on_progress(ProgressUpdate(processed=1, total=4, current_file="a.mp4", bytes_processed=100))

    bar = window.progress_bar()
    assert (bar.minimum(), bar.maximum(), bar.value()) == (0, 4, 1)
    assert window.current_detail() == "1 van 4 bestanden bekeken"

    on_progress(ProgressUpdate(processed=4, total=4, current_file="d.mp4", bytes_processed=400))
    assert window.progress_bar().value() == 4


def test_finished_analysis_shows_the_preview_in_plain_language(qapp, tmp_path):
    starter = _CapturingStartDryRun()
    window = MainWindow(detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)], start_dry_run=starter)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer Campagne")
    window.choose_button().click()

    starter.calls[0]["on_finished"](_make_summary())

    assert window.current_message() == PREVIEW_TITLE_TEXT
    assert window.current_detail() == "Nike → Zomer Campagne"
    lines = window.preview_lines()
    assert "5 mediabestanden · 4.7 GB" in lines
    assert "4 video's" in lines
    assert "1 audio" in lines
    assert "Sony FX3: 1" in lines
    assert "Sony FX6: 3" in lines
    assert "Niet herkend: 1" in lines
    assert "Duplicaten (worden overgeslagen): 2" in lines
    assert "Naamconflicten (worden automatisch opgelost): 1" in lines
    # Nooit technische termen (Design Language hoofdstuk 15):
    joined = " ".join(lines).lower()
    for forbidden in ("checksum", "manifest", "storage_root", "dry_run", "outcome"):
        assert forbidden not in joined


def test_back_from_preview_returns_to_the_selection_form(qapp, tmp_path):
    starter = _CapturingStartDryRun()
    window = MainWindow(detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)], start_dry_run=starter)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer Campagne")
    window.choose_button().click()
    starter.calls[0]["on_finished"](_make_summary())

    assert window.secondary_action_button().text() == BACK_TEXT
    window.secondary_action_button().click()

    assert window.current_message() == "SD_CARD_1"
    assert window.selected_source == tmp_path


def test_failed_analysis_shows_a_friendly_message_never_a_stacktrace(qapp, tmp_path):
    starter = _CapturingStartDryRun()
    window = MainWindow(detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)], start_dry_run=starter)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer Campagne")
    window.choose_button().click()

    starter.calls[0]["on_failed"](
        "Kon deze locatie niet meer lezen. Controleer of de schijf nog is "
        "aangesloten en probeer het opnieuw."
    )

    assert "niet meer lezen" in window.current_message()
    assert "Traceback" not in window.current_message()
    assert window.secondary_action_button().text() == BACK_TEXT

    window.secondary_action_button().click()
    assert window.current_message() == "SD_CARD_1"

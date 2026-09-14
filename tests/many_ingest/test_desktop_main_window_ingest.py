"""Fase 3: MainWindow tests for the real Start Ingest flow (see
desktop/ingest_process.py, many_ingest/ingest_worker.py).

`start_preview` and `start_real_ingest` are always injected (same pattern as
test_desktop_main_window.py's Fase 2 tests), so most tests here never touch a
real background process — only that MainWindow renders and wires whatever
it's handed, correctly. The one exception is
`test_closing_the_window_during_a_real_ingest_does_not_crash`, which uses a
real preview and a real QProcess end to end (via a standalone scenario
script, same pattern as test_desktop_preview_process.py), because that is
precisely the shutdown-while-active-work guarantee that only a real QProcess
can prove.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Fase 3.5's same-physical-device safety rule is real (see
# device_identity.py) — but the real-QProcess tests near the bottom of this
# file necessarily use one tmp_path for both source and destination (no
# portable way to fake a second real device). QProcess inherits this
# process's environment by default, so setting it here propagates down.
os.environ.setdefault("MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS", "1")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from many_ingest.core.ingest_service import CLIENT_FOLDER_NAME, ProgressUpdate
from many_ingest.core.report import IngestSummary
from many_ingest.desktop import main_window as main_window_module
from many_ingest.desktop.main_window import (
    CANCEL_BUTTON_TEXT,
    CANCEL_INGEST_CONFIRM_MESSAGE,
    CONFIRM_CANCEL_INGEST_TEXT,
    CONTINUE_INGEST_TEXT,
    DO_NOT_DISCONNECT_TEXT,
    ETA_PLACEHOLDER_TEXT,
    INGEST_DONE_TITLE_TEXT,
    INGEST_PARTIAL_TITLE_TEXT,
    INGESTING_TEXT,
    SAFE_TO_DELETE_NO_TEXT,
    SAFE_TO_DELETE_YES_TEXT,
    SAFETY_STOP_MESSAGE,
    START_INGEST_BUTTON_TEXT,
    MainWindow,
)
from many_ingest.desktop.volumes import DestinationInfo, VolumeInfo


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


def _destination(name: str, path, *, free_bytes=1_500_000_000_000) -> DestinationInfo:
    return DestinationInfo(name=name, path=path, free_bytes=free_bytes)


class _FakePreviewRunner:
    def __init__(self) -> None:
        self.cancel_called = False
        self._running = True

    def is_running(self) -> bool:
        return self._running

    def cancel(self) -> None:
        self.cancel_called = True

    def stop_and_wait(self, timeout_ms: int | None = None) -> None:
        self._running = False


class _CapturingStartPreview:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.runners: list[_FakePreviewRunner] = []

    def __call__(
        self,
        source,
        client,
        project,
        *,
        destination_root,
        on_progress,
        on_completed,
        on_failed,
        on_cancelled=None,
        on_started=None,
        on_asset_processed=None,
        config_path,
        camera_profiles_path,
    ):
        self.calls.append(
            {
                "source": source,
                "client": client,
                "project": project,
                "destination_root": destination_root,
                "on_progress": on_progress,
                "on_completed": on_completed,
                "on_failed": on_failed,
                "on_cancelled": on_cancelled,
                "on_asset_processed": on_asset_processed,
            }
        )
        runner = _FakePreviewRunner()
        self.runners.append(runner)
        return runner


class _FakeIngestRunner:
    def __init__(self) -> None:
        self.cancel_called = False
        self._running = True

    def cancel(self) -> None:
        self.cancel_called = True

    def is_running(self) -> bool:
        return self._running

    def stop_and_wait(self, timeout_ms: int | None = None) -> None:
        self._running = False


class _CapturingStartRealIngest:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.runners: list[_FakeIngestRunner] = []

    def __call__(
        self,
        source,
        client,
        project,
        *,
        destination_root,
        on_progress,
        on_completed,
        on_failed,
        on_cancelled=None,
        on_started=None,
        on_asset_processed=None,
        config_path,
        camera_profiles_path,
    ):
        self.calls.append(
            {
                "source": source,
                "client": client,
                "project": project,
                "destination_root": destination_root,
                "on_progress": on_progress,
                "on_completed": on_completed,
                "on_failed": on_failed,
                "on_cancelled": on_cancelled,
                "on_asset_processed": on_asset_processed,
            }
        )
        runner = _FakeIngestRunner()
        self.runners.append(runner)
        return runner


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


def _window_with_finished_preview(qapp, tmp_path, **summary_overrides):
    preview_starter = _CapturingStartPreview()
    ingest_starter = _CapturingStartRealIngest()
    window = MainWindow(
        detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)],
        detect_destinations=lambda source_path: [_destination("Chris", tmp_path / "Chris")],
        start_preview=preview_starter,
        start_real_ingest=ingest_starter,
    )
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer Campagne")
    window.destination_cards()[0].click()
    window.choose_button().click()
    preview_starter.calls[0]["on_completed"](_make_summary(dry_run=True, **summary_overrides))
    return window, preview_starter, ingest_starter


# -- gating: Start Ingest always uses the exact input of the shown preview ------


def test_start_ingest_calls_the_injected_starter_with_the_preview_input(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)

    start_button = window.choose_button()
    assert start_button.text() == START_INGEST_BUTTON_TEXT
    assert start_button.isEnabled() is True
    start_button.click()

    assert len(ingest_starter.calls) == 1
    call = ingest_starter.calls[0]
    assert (call["source"], call["client"], call["project"]) == (
        tmp_path,
        "Nike",
        "Zomer Campagne",
    )
    # Fase 3.5: real ingest gebruikt exact dezelfde bestemming als de preview:
    assert call["destination_root"] == tmp_path / "Chris"
    assert window.current_message() == INGESTING_TEXT


def test_start_ingest_does_nothing_without_a_confirmed_preview(qapp, tmp_path):
    # Niet bereikbaar via de UI (de knop bestaat pas ná een preview) — dit
    # bewijst de expliciete zelfbescherming in _on_start_ingest_clicked
    # (hoofdstuk 14 van de Fase 3-opdracht), niet UI-navigatie.
    ingest_starter = _CapturingStartRealIngest()
    window = MainWindow(detect_volumes=lambda: [], start_real_ingest=ingest_starter)

    window._on_start_ingest_clicked()

    assert ingest_starter.calls == []


def test_start_ingest_uses_the_latest_preview_after_a_repreview_with_different_input(qapp, tmp_path):
    window, preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)

    # Terug, en een NIEUWE preview met een andere klant/project:
    window.secondary_action_button().click()
    window.client_input().setText("Adidas")
    window.project_input().setText("Winter")
    window.choose_button().click()
    preview_starter.calls[1]["on_completed"](
        _make_summary(dry_run=True, client="Adidas", project="Winter")
    )

    window.choose_button().click()  # Start Ingest

    assert len(ingest_starter.calls) == 1
    call = ingest_starter.calls[0]
    assert (call["client"], call["project"]) == ("Adidas", "Winter")


def test_a_second_start_ingest_click_while_one_is_running_is_ignored(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)

    window.choose_button().click()  # start echte ingest
    assert len(ingest_starter.calls) == 1

    window._on_start_ingest_clicked()  # niet bereikbaar via de UI (geen knop op dit scherm)

    assert len(ingest_starter.calls) == 1


# -- progress ---------------------------------------------------------------------


def test_ingest_progress_updates_the_progress_bar_and_caption(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    on_progress = ingest_starter.calls[0]["on_progress"]
    on_progress(
        ProgressUpdate(processed=2, total=5, current_file="a.mp4", bytes_processed=2_000_000_000)
    )

    bar = window.progress_bar()
    assert (bar.minimum(), bar.maximum(), bar.value()) == (0, 5, 2)
    assert "2 van 5 bestanden" in window.current_detail()
    assert "GB" in window.current_detail()


def test_prominent_percentage_reflects_the_same_progress_as_the_bar(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    on_progress = ingest_starter.calls[0]["on_progress"]
    on_progress(ProgressUpdate(processed=2, total=5, current_file="a.mp4", bytes_processed=100))
    assert window.percentage_label_text() == "40%"
    assert window.progress_bar().value() == 2

    on_progress(ProgressUpdate(processed=5, total=5, current_file="e.mp4", bytes_processed=500))
    assert window.percentage_label_text() == "100%"
    assert window.progress_bar().value() == 5


def test_speed_is_calculated_from_elapsed_time_and_bytes_processed(qapp, tmp_path, monkeypatch):
    times = iter([100.0, 102.0])  # start, dan één progress-event 2s later
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: next(times))

    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()  # zet _ingest_start_time = 100.0

    on_progress = ingest_starter.calls[0]["on_progress"]
    on_progress(
        ProgressUpdate(
            processed=1, total=10, current_file="a.mp4", bytes_processed=20 * 1024 * 1024
        )
    )  # 20 MiB / 2s = 10 MB/s

    assert "10.0 MB/s" in window.speed_label_text()


def test_eta_shows_placeholder_with_insufficient_data(qapp, tmp_path, monkeypatch):
    times = iter([100.0, 100.5])  # 0.5s verstreken, onder _ETA_MIN_ELAPSED_SECONDS
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: next(times))

    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    on_progress = ingest_starter.calls[0]["on_progress"]
    on_progress(ProgressUpdate(processed=1, total=10, current_file="a.mp4", bytes_processed=1000))

    assert ETA_PLACEHOLDER_TEXT in window.speed_label_text()


def test_eta_shows_a_usable_value_once_enough_data_is_available(qapp, tmp_path, monkeypatch):
    times = iter([100.0, 160.0])  # 60s verstreken, 2 van de 10 bestanden klaar
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: next(times))

    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    on_progress = ingest_starter.calls[0]["on_progress"]
    on_progress(ProgressUpdate(processed=2, total=10, current_file="b.mp4", bytes_processed=2000))
    # eta = 60s * (10-2)/2 = 240s = 4 min
    assert "± 4 min resterend" in window.speed_label_text()
    assert ETA_PLACEHOLDER_TEXT not in window.speed_label_text()


def test_do_not_disconnect_warning_is_visible_during_ingest(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    assert window.do_not_disconnect_warning_visible() is True


# -- completion, both variants ----------------------------------------------------


def test_ingest_completed_without_errors_shows_klaar_and_safe_to_delete(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    ingest_starter.calls[0]["on_completed"](
        _make_summary(
            dry_run=False,
            total_files=312,
            errors=0,
            safe_to_delete_source=True,
        )
    )

    assert window.current_message() == INGEST_DONE_TITLE_TEXT
    lines = window.preview_lines()
    assert "312 bestanden" in lines
    assert SAFE_TO_DELETE_YES_TEXT in lines

    # Geen primaire knop meer op dit scherm — nogmaals "Start Ingest"
    # aanklikken zonder nieuwe preview kan hier niet meer, structureel:
    assert window.choose_button() is None


def test_ingest_completed_with_errors_shows_bijna_klaar_and_not_safe_to_delete(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    ingest_starter.calls[0]["on_completed"](
        _make_summary(
            dry_run=False,
            total_files=312,
            errors=4,
            safe_to_delete_source=False,
        )
    )

    assert window.current_message() == INGEST_PARTIAL_TITLE_TEXT
    lines = window.preview_lines()
    assert "4" in lines
    assert SAFE_TO_DELETE_NO_TEXT in lines


def test_ingest_report_shows_the_same_real_storage_layout_breadcrumb_as_the_preview(
    qapp, tmp_path
):
    """Fase 3.5 UX fix requirement: the eindscherm (ingest report) must show
    the exact same real-storage-layout breadcrumb as the preview — both
    call the same _destination_breadcrumb_lines(), this proves it end to
    end through the actual preview -> Start Ingest -> completed flow, not
    just by reading the shared implementation."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: ManyFast/Footage\n"
        "manifest_subpath: ManyFast/ManyOS/AssetSchema/asset_schema.json\n"
        "log_subpath: ManyFast/ManyOS/Logs\n"
    )
    preview_starter = _CapturingStartPreview()
    ingest_starter = _CapturingStartRealIngest()
    window = MainWindow(
        detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)],
        detect_destinations=lambda source_path: [_destination("Chris", tmp_path / "Chris")],
        start_preview=preview_starter,
        start_real_ingest=ingest_starter,
        config_path=config_path,
    )
    window.client_input().setText("ManyOS Test")
    window.project_input().setText("Sony Metadata Test")
    window.destination_cards()[0].click()
    window.choose_button().click()
    preview_starter.calls[0]["on_completed"](
        _make_summary(dry_run=True, client="ManyOS Test", project="Sony Metadata Test")
    )

    preview_lines = window.preview_lines()
    assert (
        preview_lines.index("Chris")
        < preview_lines.index("ManyFast")
        < preview_lines.index("Footage")
        < preview_lines.index(CLIENT_FOLDER_NAME)
        < preview_lines.index("ManyOS Test")
    )

    window.choose_button().click()  # Start Ingest
    ingest_starter.calls[0]["on_completed"](
        _make_summary(
            dry_run=False,
            client="ManyOS Test",
            project="Sony Metadata Test",
            errors=0,
            safe_to_delete_source=True,
        )
    )

    report_lines = window.preview_lines()
    assert (
        report_lines.index("Chris")
        < report_lines.index("ManyFast")
        < report_lines.index("Footage")
        < report_lines.index(CLIENT_FOLDER_NAME)
        < report_lines.index("ManyOS Test")
    )


# -- failure and cancellation ------------------------------------------------------


def test_ingest_failed_shows_a_friendly_message_never_a_stacktrace(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    ingest_starter.calls[0]["on_failed"](
        "Er ging onverwacht iets mis tijdens het kopiëren. Er is niets overschreven."
    )

    assert "niets overschreven" in window.current_message()
    assert "Traceback" not in window.current_message()
    assert window.secondary_action_button() is not None  # "Terug" blijft bereikbaar


def test_cancel_click_shows_a_confirmation_instead_of_cancelling_immediately(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    assert window.current_message() == INGESTING_TEXT
    window.secondary_action_button().click()  # Annuleren -> toont bevestiging, cancelt nog niet

    assert ingest_starter.runners[0].cancel_called is False
    assert window.cancel_confirmation_message() == CANCEL_INGEST_CONFIRM_MESSAGE
    assert window.choose_button().text() == CONTINUE_INGEST_TEXT
    assert window.secondary_action_button().text() == CONFIRM_CANCEL_INGEST_TEXT


def test_continue_ingest_dismisses_the_confirmation_without_cancelling(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    window.secondary_action_button().click()  # Annuleren -> bevestiging

    window.choose_button().click()  # "Doorgaan met ingest"

    assert ingest_starter.runners[0].cancel_called is False
    assert window.current_message() == INGESTING_TEXT
    assert window.secondary_action_button().text() == CANCEL_BUTTON_TEXT  # weer normaal
    assert window.cancel_confirmation_message() == ""


def test_confirming_cancel_calls_cancel_on_the_runner(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    window.secondary_action_button().click()  # Annuleren -> bevestiging
    window.secondary_action_button().click()  # "Ingest annuleren" (nu de bevestig-knop)

    assert ingest_starter.runners[0].cancel_called is True


def test_progress_is_preserved_when_the_cancel_confirmation_is_shown(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    ingest_starter.calls[0]["on_progress"](
        ProgressUpdate(processed=3, total=10, current_file="c.mp4", bytes_processed=300)
    )

    window.secondary_action_button().click()  # her-rendert het scherm

    assert window.percentage_label_text() == "30%"


def test_ingest_cancelled_returns_to_the_selection_form(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    ingest_starter.calls[0]["on_cancelled"]()

    assert window.current_message() == "SD_CARD_1"
    assert window.client_input() is not None


# -- safety-stop: te veel opeenvolgende mislukkingen tijdens een echte ingest -----


def _fail(source_path: str = "x.mp4") -> dict:
    return {"outcome": "failed_verification", "source_path": source_path}


def _copied(source_path: str = "x.mp4") -> dict:
    return {"outcome": "copied", "source_path": source_path}


def _duplicate(source_path: str = "x.mp4") -> dict:
    return {"outcome": "duplicate_skipped", "source_path": source_path}


def test_four_consecutive_failures_do_not_trigger_the_safety_stop(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    on_asset_processed = ingest_starter.calls[0]["on_asset_processed"]

    for _ in range(4):
        on_asset_processed(_fail())

    assert ingest_starter.runners[0].cancel_called is False


def test_five_consecutive_failures_trigger_the_safety_stop(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    on_asset_processed = ingest_starter.calls[0]["on_asset_processed"]

    for _ in range(5):
        on_asset_processed(_fail())

    assert ingest_starter.runners[0].cancel_called is True


def test_a_copied_outcome_resets_the_failure_counter(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    on_asset_processed = ingest_starter.calls[0]["on_asset_processed"]

    for _ in range(4):
        on_asset_processed(_fail())
    on_asset_processed(_copied())  # reset naar 0
    for _ in range(4):
        on_asset_processed(_fail())

    assert ingest_starter.runners[0].cancel_called is False


def test_a_duplicate_skipped_outcome_is_neutral_and_does_not_reset_the_counter(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    on_asset_processed = ingest_starter.calls[0]["on_asset_processed"]

    for _ in range(4):
        on_asset_processed(_fail())
    on_asset_processed(_duplicate())  # neutraal, teller blijft op 4
    on_asset_processed(_fail())  # de 5e mislukking op rij

    assert ingest_starter.runners[0].cancel_called is True


def test_safety_stop_triggers_cancel_only_once(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    on_asset_processed = ingest_starter.calls[0]["on_asset_processed"]

    for _ in range(5):
        on_asset_processed(_fail())
    runner = ingest_starter.runners[0]
    runner.cancel_called = False  # reset om een eventuele tweede aanroep te kunnen zien
    for _ in range(3):
        on_asset_processed(_fail())  # late events ná de trigger

    assert runner.cancel_called is False, "cancel() mag na de trigger niet nogmaals aangeroepen worden"


def test_safety_stop_cancellation_shows_a_different_message_than_manual_cancellation(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    on_asset_processed = ingest_starter.calls[0]["on_asset_processed"]

    for _ in range(5):
        on_asset_processed(_fail())
    ingest_starter.calls[0]["on_cancelled"]()  # de worker bevestigt de annulering

    assert window.current_message() == SAFETY_STOP_MESSAGE
    assert "Traceback" not in window.current_message()
    # Geen stille terugkeer naar het formulier zoals bij een handmatige annulering:
    assert window.client_input() is None


def test_manual_cancellation_still_returns_to_the_selection_form(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    on_asset_processed = ingest_starter.calls[0]["on_asset_processed"]

    on_asset_processed(_fail())  # slechts 1 mislukking, geen safety-stop
    ingest_starter.calls[0]["on_cancelled"]()  # gebruiker annuleerde zelf

    assert window.current_message() == "SD_CARD_1"
    assert window.client_input() is not None


def test_preview_never_triggers_the_safety_stop(qapp, tmp_path):
    """De preview-starter krijgt nooit een on_asset_processed-callback van
    MainWindow (zie _start_preview) — er is dus structureel geen pad waarop
    preview-activiteit de echte-ingest-teller kan raken."""
    preview_starter = _CapturingStartPreview()
    ingest_starter = _CapturingStartRealIngest()
    window = MainWindow(
        detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)],
        detect_destinations=lambda source_path: [_destination("Chris", tmp_path / "Chris")],
        start_preview=preview_starter,
        start_real_ingest=ingest_starter,
    )
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer Campagne")
    window.destination_cards()[0].click()
    window.choose_button().click()

    assert preview_starter.calls[0]["on_asset_processed"] is None


def test_a_new_ingest_resets_the_failure_counter_and_safety_stop_state(qapp, tmp_path):
    window, _preview_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()
    on_asset_processed = ingest_starter.calls[0]["on_asset_processed"]
    for _ in range(5):
        on_asset_processed(_fail())
    ingest_starter.calls[0]["on_cancelled"]()  # safety-stop-melding getoond

    # Terug naar het formulier is hier niet bereikbaar (safety-stop toont een
    # foutscherm, geen "Terug"-pad naar dezelfde preview) — een verse sessie
    # met een nieuwe, geslaagde preview simuleert "nieuwe ingest":
    window2, _preview_starter2, ingest_starter2 = _window_with_finished_preview(qapp, tmp_path)
    window2.choose_button().click()

    assert ingest_starter2.runners[0].cancel_called is False
    on_asset_processed2 = ingest_starter2.calls[0]["on_asset_processed"]
    for _ in range(4):
        on_asset_processed2(_fail())
    assert ingest_starter2.runners[0].cancel_called is False, (
        "een nieuwe ingest-sessie mag geen teller van een eerdere sessie erven"
    )


# -- real QProcess, real crash-regression shutdown scenario ------------------------


def test_closing_the_window_during_a_real_ingest_does_not_crash(tmp_path):
    """End-to-end: a real dry-run, a real Start Ingest click, a real
    QProcess — and the window is closed while it's still copying. Proves
    MainWindow._wait_for_ingest_to_stop actually stops the worker process
    before the app is allowed to quit, without crashing (see
    _close_during_real_ingest_scenario.py)."""
    script = Path(__file__).with_name("_close_during_real_ingest_scenario.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")

    for i in range(5):
        run_dir = tmp_path / f"run_{i:02d}"
        run_dir.mkdir()
        result = subprocess.run(
            [sys.executable, str(script), str(run_dir)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        assert result.returncode == 0, (
            f"run {i}: scenario-proces crashte (returncode={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "OK" in result.stdout, f"run {i}: geen 'OK' in stdout:\n{result.stdout}"


# -- real QProcess, real safety-stop end-to-end -------------------------------------

CAMERA_PROFILES_PATH = (
    Path(__file__).resolve().parents[2]
    / "modules"
    / "many_ingest"
    / "config"
    / "camera_profiles.yaml"
)


def test_safety_stop_fires_over_a_real_worker_process(qapp, tmp_path):
    """End-to-end met een echte worker (geen fakes): forceert 6 échte
    failed_verification-uitkomsten door de bestemming onbeschrijfbaar te
    maken, en bewijst dat de veiligheidsstop ook door de echte JSON-lines-
    events (niet alleen via geïnjecteerde fakes) getriggerd wordt — de
    string-matching op payload["outcome"] in _on_ingest_asset_processed werkt
    dus ook tegen de echte worker-output, niet alleen tegen testdata."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(6):
        (input_dir / f"C{i:04d}.MP4").write_bytes(b"x" * 1000)
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    storage_root = destination_root / "storage"
    storage_root.mkdir()
    storage_root.chmod(0o555)  # elke copy() faalt -> failed_verification
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: storage\nmanifest_subpath: asset_schema.json\nlog_subpath: logs\n"
    )

    window = MainWindow(
        detect_volumes=lambda: [],
        detect_destinations=lambda source_path: [_destination("TestDisk", destination_root)],
        config_path=config_path,
        camera_profiles_path=CAMERA_PROFILES_PATH,
    )
    try:
        window._set_manual_source(input_dir)
        window.client_input().setText("Nike")
        window.project_input().setText("Zomer")
        window.destination_cards()[0].click()
        window.choose_button().click()  # echte preview

        loop = QEventLoop()
        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        timeout_timer.timeout.connect(loop.quit)
        poll_timer = QTimer()
        poll_timer.timeout.connect(
            lambda: window.current_message() == "Wat we hebben gevonden" and loop.quit()
        )
        poll_timer.start(20)
        timeout_timer.start(15_000)
        loop.exec()
        assert window.current_message() == "Wat we hebben gevonden", "preview kwam niet op tijd"

        window.choose_button().click()  # Start Ingest — echte QProcess

        loop2 = QEventLoop()
        timeout_timer2 = QTimer()
        timeout_timer2.setSingleShot(True)
        timeout_timer2.timeout.connect(loop2.quit)
        poll_timer2 = QTimer()
        poll_timer2.timeout.connect(
            lambda: window.current_message() == SAFETY_STOP_MESSAGE and loop2.quit()
        )
        poll_timer2.start(20)
        timeout_timer2.start(30_000)
        loop2.exec()

        assert window.current_message() == SAFETY_STOP_MESSAGE, (
            "de veiligheidsstop-melding verscheen niet op tijd via de echte worker"
        )
    finally:
        storage_root.chmod(0o755)
        window._wait_for_ingest_to_stop()

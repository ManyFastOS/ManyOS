"""Fase 3: MainWindow tests for the real Start Ingest flow (see
desktop/ingest_process.py, many_ingest/ingest_worker.py).

`start_dry_run` and `start_real_ingest` are always injected (same pattern as
test_desktop_main_window.py's Fase 2 tests), so most tests here never touch a
real background QThread or a real QProcess — only that MainWindow renders and
wires whatever it's handed, correctly. The one exception is
`test_closing_the_window_during_a_real_ingest_does_not_crash`, which uses a
real dry-run and a real QProcess end to end (via a standalone scenario
script, same pattern as test_desktop_thread_lifecycle.py), because that is
precisely the shutdown-while-active-work guarantee that only a real QProcess
can prove.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from many_ingest.core.ingest_service import ProgressUpdate
from many_ingest.core.report import IngestSummary
from many_ingest.desktop.main_window import (
    INGEST_DONE_TITLE_TEXT,
    INGEST_PARTIAL_TITLE_TEXT,
    INGESTING_TEXT,
    SAFE_TO_DELETE_NO_TEXT,
    SAFE_TO_DELETE_YES_TEXT,
    START_INGEST_BUTTON_TEXT,
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


class _FakeWorker:
    def __init__(self) -> None:
        self.cancel_requested = False

    def request_cancel(self) -> None:
        self.cancel_requested = True


class _CapturingStartDryRun:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.workers: list[_FakeWorker] = []

    def __call__(
        self,
        source,
        client,
        project,
        *,
        on_progress,
        on_finished,
        on_failed,
        on_cancelled=None,
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
                "on_cancelled": on_cancelled,
            }
        )
        worker = _FakeWorker()
        self.workers.append(worker)
        return None, worker


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
                "on_progress": on_progress,
                "on_completed": on_completed,
                "on_failed": on_failed,
                "on_cancelled": on_cancelled,
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
    dry_run_starter = _CapturingStartDryRun()
    ingest_starter = _CapturingStartRealIngest()
    window = MainWindow(
        detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)],
        start_dry_run=dry_run_starter,
        start_real_ingest=ingest_starter,
    )
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer Campagne")
    window.choose_button().click()
    dry_run_starter.calls[0]["on_finished"](_make_summary(dry_run=True, **summary_overrides))
    return window, dry_run_starter, ingest_starter


# -- gating: Start Ingest always uses the exact input of the shown preview ------


def test_start_ingest_calls_the_injected_starter_with_the_preview_input(qapp, tmp_path):
    window, _dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)

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
    window, dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)

    # Terug, en een NIEUWE preview met een andere klant/project:
    window.secondary_action_button().click()
    window.client_input().setText("Adidas")
    window.project_input().setText("Winter")
    window.choose_button().click()
    dry_run_starter.calls[1]["on_finished"](
        _make_summary(dry_run=True, client="Adidas", project="Winter")
    )

    window.choose_button().click()  # Start Ingest

    assert len(ingest_starter.calls) == 1
    call = ingest_starter.calls[0]
    assert (call["client"], call["project"]) == ("Adidas", "Winter")


def test_a_second_start_ingest_click_while_one_is_running_is_ignored(qapp, tmp_path):
    window, _dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)

    window.choose_button().click()  # start echte ingest
    assert len(ingest_starter.calls) == 1

    window._on_start_ingest_clicked()  # niet bereikbaar via de UI (geen knop op dit scherm)

    assert len(ingest_starter.calls) == 1


# -- progress ---------------------------------------------------------------------


def test_ingest_progress_updates_the_progress_bar_and_caption(qapp, tmp_path):
    window, _dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    on_progress = ingest_starter.calls[0]["on_progress"]
    on_progress(
        ProgressUpdate(processed=2, total=5, current_file="a.mp4", bytes_processed=2_000_000_000)
    )

    bar = window.progress_bar()
    assert (bar.minimum(), bar.maximum(), bar.value()) == (0, 5, 2)
    assert "2 van 5 bestanden" in window.current_detail()
    assert "GB" in window.current_detail()


# -- completion, both variants ----------------------------------------------------


def test_ingest_completed_without_errors_shows_klaar_and_safe_to_delete(qapp, tmp_path):
    window, _dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
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
    window, _dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
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


# -- failure and cancellation ------------------------------------------------------


def test_ingest_failed_shows_a_friendly_message_never_a_stacktrace(qapp, tmp_path):
    window, _dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    ingest_starter.calls[0]["on_failed"](
        "Er ging onverwacht iets mis tijdens het kopiëren. Er is niets overschreven."
    )

    assert "niets overschreven" in window.current_message()
    assert "Traceback" not in window.current_message()
    assert window.secondary_action_button() is not None  # "Terug" blijft bereikbaar


def test_cancel_ingest_button_calls_cancel_on_the_runner(qapp, tmp_path):
    window, _dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    assert window.current_message() == INGESTING_TEXT
    window.secondary_action_button().click()  # Annuleren

    assert ingest_starter.runners[0].cancel_called is True


def test_ingest_cancelled_returns_to_the_selection_form(qapp, tmp_path):
    window, _dry_run_starter, ingest_starter = _window_with_finished_preview(qapp, tmp_path)
    window.choose_button().click()

    ingest_starter.calls[0]["on_cancelled"]()

    assert window.current_message() == "SD_CARD_1"
    assert window.client_input() is not None


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

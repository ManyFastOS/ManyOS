"""Desktop shell tests — Fase 0 (bare window), Fase 1 (volume detection UI),
Fase 2 (preview UI), and Fase 3.5 (destination-picker UI).

Runs headless (QT_QPA_PLATFORM=offscreen). `detect_volumes`, `detect_destinations`
and `start_preview` are always injected so these tests never touch the real
/Volumes, a real config, or a real background process — see
desktop/volumes.py and desktop/ingest_process.py (and
tests/many_ingest/test_desktop_ingest_process.py /
test_desktop_preview_process.py) for that OS/engine-integration layer,
exercised for real elsewhere. Here we only verify MainWindow renders
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

from many_ingest.core.ingest_service import CLIENT_FOLDER_NAME, ProgressUpdate
from many_ingest.core.report import IngestSummary
from many_ingest.desktop.main_window import (
    ANALYZING_TEXT,
    BACK_TEXT,
    CANCEL_BUTTON_TEXT,
    CHOOSE_ANOTHER_BUTTON_TEXT,
    CHOOSE_BUTTON_TEXT,
    DESTINATION_ORG_LABEL,
    EMPTY_STATE_TEXT,
    NO_DESTINATIONS_TEXT,
    OTHER_DISK_TEXT,
    PREVIEW_TITLE_TEXT,
    RETRY_TEXT,
    SELECTED_DESTINATION_PREFIX,
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


class _FakeRunner:
    """Stands in for `ingest_process.IngestRunner` — only what MainWindow
    actually touches on it: `is_running()`/`cancel()`."""

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
    """A fake `ingest_process.start_preview` — records each call's arguments
    and callbacks instead of touching IngestService or a real process, so
    tests can drive on_progress/on_completed/on_failed/on_cancelled at will
    and inspect exactly what MainWindow asked for."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.runners: list[_FakeRunner] = []

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
            }
        )
        runner = _FakeRunner()
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


def _window(
    tmp_path,
    *,
    volumes=None,
    destinations=None,
    start_preview=None,
):
    volumes = volumes if volumes is not None else [_volume("SD_CARD_1", tmp_path)]
    destinations = (
        destinations if destinations is not None else [_destination("Chris", tmp_path / "Chris")]
    )
    return MainWindow(
        detect_volumes=lambda: volumes,
        detect_destinations=lambda source_path: destinations,
        start_preview=start_preview,
    )


def _fill_form(window, client: str = "Nike", project: str = "Zomer Campagne", *, destination_index=0):
    window.client_input().setText(client)
    window.project_input().setText(project)
    cards = window.destination_cards()
    if cards:
        cards[destination_index].click()


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


# -- Fase 3.5: bestemmingsschijf-keuze -------------------------------------------


def test_destination_cards_show_name_and_free_space(qapp, tmp_path):
    window = _window(
        tmp_path,
        destinations=[
            _destination("Chris", tmp_path / "Chris", free_bytes=1_500_000_000_000),
            _destination("Sharpwaves", tmp_path / "Sharpwaves", free_bytes=991_000_000_000),
        ],
    )

    cards = window.destination_cards()
    assert len(cards) == 2
    assert "Chris" in cards[0].text()
    assert "TB" in cards[0].text() or "GB" in cards[0].text()
    assert "Sharpwaves" in cards[1].text()


def test_no_destinations_shows_a_clear_message_and_a_retry_link(qapp, tmp_path):
    from PySide6.QtWidgets import QLabel, QPushButton

    window = _window(tmp_path, destinations=[])

    assert window.destination_cards() == []
    caption_texts = [label.text() for label in window._content.findChildren(QLabel, "captionLabel")]
    assert NO_DESTINATIONS_TEXT in caption_texts
    link_texts = [button.text() for button in window._content.findChildren(QPushButton, "linkButton")]
    assert "Opnieuw zoeken" in link_texts


def test_preview_button_disabled_until_client_project_and_destination_are_filled(qapp, tmp_path):
    window = _window(tmp_path)

    assert window.choose_button().isEnabled() is False

    window.client_input().setText("Nike")
    assert window.choose_button().isEnabled() is False

    window.project_input().setText("Zomer Campagne")
    assert window.choose_button().isEnabled() is False  # nog geen bestemming gekozen

    window.destination_cards()[0].click()
    assert window.choose_button().isEnabled() is True

    window.client_input().setText("   ")  # alleen witruimte telt niet als ingevuld
    assert window.choose_button().isEnabled() is False


def test_clicking_a_destination_card_shows_the_chosen_destination(qapp, tmp_path):
    window = _window(
        tmp_path, destinations=[_destination("Chris", tmp_path / "Chris", free_bytes=1_500_000_000_000)]
    )

    window.destination_cards()[0].click()

    assert window.selected_destination_text().startswith(SELECTED_DESTINATION_PREFIX)
    assert "Chris" in window.selected_destination_text()


def test_choosing_a_new_source_clears_the_previously_chosen_destination(qapp, tmp_path):
    volumes = [
        _volume("SD_CARD_1", tmp_path / "a"),
        _volume("EXT_DRIVE_2", tmp_path / "b"),
    ]
    window = MainWindow(
        detect_volumes=lambda: volumes,
        detect_destinations=lambda source_path: [_destination("Chris", tmp_path / "Chris")],
    )
    window.volume_cards()[0].click()
    window.destination_cards()[0].click()
    assert window.selected_destination_text() != ""

    window.volume_cards()  # (nog steeds op de kiezer-lijst — kies opnieuw)
    window._select_volume(volumes[1])

    assert window.selected_destination_text() == ""


# -- Fase 2: client/project form + dry-run preview ------------------------------


def test_clicking_bekijk_inhoud_starts_a_preview_with_source_client_project_and_destination(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)

    window.choose_button().click()

    assert len(starter.calls) == 1
    call = starter.calls[0]
    assert (call["source"], call["client"], call["project"]) == (tmp_path, "Nike", "Zomer Campagne")
    assert call["destination_root"] == tmp_path / "Chris"
    assert window.current_message() == ANALYZING_TEXT


def test_progress_updates_drive_the_progress_bar_and_status(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)
    window.choose_button().click()

    on_progress = starter.calls[0]["on_progress"]
    on_progress(ProgressUpdate(processed=1, total=4, current_file="a.mp4", bytes_processed=100))

    bar = window.progress_bar()
    assert (bar.minimum(), bar.maximum(), bar.value()) == (0, 4, 1)
    assert window.current_detail() == "1 van 4 bestanden bekeken"

    on_progress(ProgressUpdate(processed=4, total=4, current_file="d.mp4", bytes_processed=400))
    assert window.progress_bar().value() == 4


def test_finished_analysis_shows_the_preview_in_plain_language(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(
        tmp_path,
        destinations=[_destination("Chris", tmp_path / "Chris", free_bytes=1_500_000_000_000)],
        start_preview=starter,
    )
    _fill_form(window)
    window.choose_button().click()

    starter.calls[0]["on_completed"](
        _make_summary(
            total_files=327,
            camera_profile_counts={"Sony FX6": 188, "Sony FX3": 121, "DJI": 14, "Audio": 18, "Onbekend": 6},
            duplicates=3,
            name_conflicts_resolved=0,
            total_bytes=914_000_000_000,
        )
    )

    assert window.current_message() == PREVIEW_TITLE_TEXT
    lines = window.preview_lines()

    # Bron
    assert "SD_CARD_1" in lines
    # Bestemming — in mensentaal, geen storage_root/pad (Fase 3.5: nu ook de
    # gekozen schijfnaam en de vrije ruimte, requirement 8)
    assert "Chris" in lines
    assert lines.index("Chris") < lines.index(DESTINATION_ORG_LABEL) < lines.index("Nike") < lines.index(
        "Zomer Campagne"
    )
    assert any("vrij" in line for line in lines)
    # Bestanden
    assert "327 bestanden" in lines
    # Camera's — hardware eerst aflopend op aantal, Audio en Onbekend altijd als laatste twee
    assert lines.index("Sony FX6: 188") < lines.index("Sony FX3: 121") < lines.index("DJI: 14")
    assert lines.index("DJI: 14") < lines.index("Audio: 18") < lines.index("Onbekend: 6")
    # Duplicaten / Naamconflicten
    assert "3" in lines
    assert "0" in lines
    # Bijzonderheden
    assert "6 bestanden konden niet automatisch worden herkend. Ze worden wel meegenomen." in lines

    # De "Start Ingest"-knop staat klaar en is actief na een geslaagde preview (Fase 3)
    start_button = window.choose_button()
    assert start_button.text() == START_INGEST_BUTTON_TEXT
    assert start_button.isEnabled() is True

    # Nooit technische termen (Design Language hoofdstuk 15):
    joined = " ".join(lines).lower()
    for forbidden in ("checksum", "manifest", "storage_root", "dry_run", "outcome", "volumes"):
        assert forbidden not in joined


def test_destination_breadcrumb_reflects_the_real_storage_layout_not_a_hardcoded_label(
    qapp, tmp_path
):
    """Regression test for a real user report (2026-09-14): the breadcrumb
    used to show only 'ManyFast', skipping the real Footage/Klanten levels
    the engine actually creates (config.py's footage_subpath +
    ingest_service.py's CLIENT_FOLDER_NAME) — a user browsing to the shown
    path couldn't find their project folder. Uses a DISTINCTIVE
    footage_subpath (not the real 'ManyFast/Footage' value) to prove the
    breadcrumb is genuinely sourced from config.yaml, not re-hardcoded in
    the GUI — if this were still hardcoded, "Studio"/"RawFootage" could
    never appear here."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: Studio/RawFootage\n"
        "manifest_subpath: Studio/System/asset_schema.json\n"
        "log_subpath: Studio/System/Logs\n"
    )
    starter = _CapturingStartPreview()
    window = MainWindow(
        detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)],
        detect_destinations=lambda source_path: [_destination("Chris", tmp_path / "Chris")],
        start_preview=starter,
        config_path=config_path,
    )
    _fill_form(window)
    window.choose_button().click()
    starter.calls[0]["on_completed"](_make_summary())

    lines = window.preview_lines()
    assert (
        lines.index("Chris")
        < lines.index("Studio")
        < lines.index("RawFootage")
        < lines.index(CLIENT_FOLDER_NAME)
        < lines.index("Nike")
        < lines.index("Zomer Campagne")
    )
    assert "ManyFast" not in lines  # geen hardcoded label meer overgebleven


def test_destination_breadcrumb_shows_footage_and_klanten_for_the_real_manyfast_layout(
    qapp, tmp_path
):
    """The concrete real-world case the user reported: with the actual
    production footage_subpath ('ManyFast/Footage'), the breadcrumb must
    show Footage AND Klanten, not skip straight from ManyFast to the
    client — this is the exact structure that was hidden before this fix."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: ManyFast/Footage\n"
        "manifest_subpath: ManyFast/ManyOS/AssetSchema/asset_schema.json\n"
        "log_subpath: ManyFast/ManyOS/Logs\n"
    )
    starter = _CapturingStartPreview()
    window = MainWindow(
        detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)],
        detect_destinations=lambda source_path: [_destination("Chris", tmp_path / "Chris")],
        start_preview=starter,
        config_path=config_path,
    )
    _fill_form(window, client="ManyOS Test", project="Sony Metadata Test")
    window.choose_button().click()
    starter.calls[0]["on_completed"](
        _make_summary(client="ManyOS Test", project="Sony Metadata Test")
    )

    lines = window.preview_lines()
    assert (
        lines.index("Chris")
        < lines.index("ManyFast")
        < lines.index("Footage")
        < lines.index(CLIENT_FOLDER_NAME)
        < lines.index("ManyOS Test")
        < lines.index("Sony Metadata Test")
    )


def test_destination_breadcrumb_falls_back_gracefully_when_config_is_unreadable(qapp, tmp_path):
    """A missing/invalid config.yaml must never break the preview screen —
    this is a cosmetic breadcrumb, not the safety-critical ingest path (the
    worker process's own config validation still guards a real run)."""
    starter = _CapturingStartPreview()
    window = MainWindow(
        detect_volumes=lambda: [_volume("SD_CARD_1", tmp_path)],
        detect_destinations=lambda source_path: [_destination("Chris", tmp_path / "Chris")],
        start_preview=starter,
        config_path=tmp_path / "does_not_exist.yaml",
    )
    _fill_form(window)
    window.choose_button().click()
    starter.calls[0]["on_completed"](_make_summary())

    lines = window.preview_lines()
    assert DESTINATION_ORG_LABEL in lines
    assert window.current_message() == PREVIEW_TITLE_TEXT


def test_preview_with_an_empty_source_folder(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)
    window.choose_button().click()

    starter.calls[0]["on_completed"](
        _make_summary(
            total_files=0,
            video_count=0,
            audio_count=0,
            unknown_type_count=0,
            camera_profile_counts={},
            duplicates=0,
            name_conflicts_resolved=0,
            total_bytes=0,
        )
    )

    lines = window.preview_lines()
    assert "0 bestanden" in lines
    # Geen Camera's-sectie zonder één relevant bestand, en geen Bijzonderheden:
    assert not any("herkend" in line for line in lines)
    assert all(":" not in line or line.split(":")[0] not in ("Sony FX6", "Sony FX3", "DJI") for line in lines)


def test_preview_with_only_audio_files(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)
    window.choose_button().click()

    starter.calls[0]["on_completed"](
        _make_summary(
            total_files=18,
            video_count=0,
            audio_count=18,
            unknown_type_count=0,
            camera_profile_counts={"Audio": 18},
            duplicates=0,
            name_conflicts_resolved=0,
            total_bytes=1_800_000_000,
        )
    )

    lines = window.preview_lines()
    assert "18 bestanden" in lines
    assert "Audio: 18" in lines
    assert not any("herkend" in line for line in lines)  # geen Bijzonderheden, niets is onbekend


def test_preview_with_only_unrecognized_files(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)
    window.choose_button().click()

    starter.calls[0]["on_completed"](
        _make_summary(
            total_files=6,
            video_count=6,
            audio_count=0,
            unknown_type_count=6,
            camera_profile_counts={"Onbekend": 6},
            duplicates=0,
            name_conflicts_resolved=0,
            total_bytes=600_000_000,
        )
    )

    lines = window.preview_lines()
    assert "6 bestanden" in lines
    assert "Onbekend: 6" in lines
    assert "6 bestanden konden niet automatisch worden herkend. Ze worden wel meegenomen." in lines


def test_cancelling_during_analysis_requests_it_on_the_runner_and_returns_to_the_form(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)
    window.choose_button().click()

    assert window.current_message() == ANALYZING_TEXT
    assert window.secondary_action_button().text() == CANCEL_BUTTON_TEXT

    window.secondary_action_button().click()

    runner = starter.runners[0]
    assert runner.cancel_called is True

    # Bevestigt wat de echte worker doet na een geslaagde annulering (zie
    # ingest_worker.py's `ingest_cancelled`-event): de on_cancelled-callback
    # vuurt, en de GUI gaat terug naar het formulier — geen foutscherm, een
    # annulering is geen fout.
    starter.calls[0]["on_cancelled"]()

    assert window.current_message() == "SD_CARD_1"
    assert window.client_input() is not None


def test_a_second_preview_click_while_one_is_running_is_ignored(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)

    window._start_preview("Nike", "Zomer Campagne")  # echte start (niet bereikbaar via de UI 2x)
    assert len(starter.calls) == 1

    window._start_preview("Nike", "Zomer Campagne")

    assert len(starter.calls) == 1


def test_back_from_preview_returns_to_the_selection_form(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)
    window.choose_button().click()
    starter.calls[0]["on_completed"](_make_summary())

    assert window.secondary_action_button().text() == BACK_TEXT
    window.secondary_action_button().click()

    assert window.current_message() == "SD_CARD_1"
    assert window.selected_source == tmp_path


def test_failed_analysis_shows_a_friendly_message_never_a_stacktrace(qapp, tmp_path):
    starter = _CapturingStartPreview()
    window = _window(tmp_path, start_preview=starter)
    _fill_form(window)
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

"""End-to-end, real-QProcess tests for the preview/dry-run lifecycle through
a real `MainWindow` — the preview's equivalent of
test_desktop_main_window_ingest.py::test_closing_the_window_during_a_real_ingest_does_not_crash.

Until this round the preview ran on a background `QThread`
(desktop/controller.py, removed — see git history and
docs/MANY_INGEST_BUILD_PLAN.md's crash notes). These tests are the direct
replacement for that file's real-QThread regression coverage, now proving
the same properties (repeated real runs, second-attempt blocked, close while
active) for the QProcess-based preview instead.

Runs headless (QT_QPA_PLATFORM=offscreen). All tests use small, synthetic,
temporary files — never real production footage, per this round's testing
rules.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Fase 3.5's same-physical-device safety rule is real (see
# device_identity.py) — but every test here necessarily uses one tmp_path
# for both source and destination (no portable way to fake a second real
# device). QProcess inherits this process's environment by default, so
# setting it here propagates to every worker subprocess these tests spawn.
os.environ.setdefault("MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS", "1")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import PREVIEW_TITLE_TEXT, START_INGEST_BUTTON_TEXT, MainWindow
from many_ingest.desktop.volumes import DestinationInfo

CAMERA_PROFILES_PATH = (
    Path(__file__).resolve().parents[2]
    / "modules"
    / "many_ingest"
    / "config"
    / "camera_profiles.yaml"
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: storage\nmanifest_subpath: asset_schema.json\nlog_subpath: logs\n"
    )
    return config_path


def _detect_destinations(tmp_path):
    return lambda source_path: [DestinationInfo(name="TestDisk", path=tmp_path, free_bytes=1_000_000_000)]


def _run_preview_and_wait(window: MainWindow, timeout_ms: int = 15_000) -> None:
    loop = QEventLoop()
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(loop.quit)

    def _check() -> None:
        if window.current_message() in (PREVIEW_TITLE_TEXT,) or window._preview_runner is None:
            loop.quit()

    poll_timer = QTimer()
    poll_timer.timeout.connect(_check)
    poll_timer.start(20)
    timeout_timer.start(timeout_ms)
    loop.exec()


def test_repeated_real_previews_in_a_row_do_not_crash(qapp, tmp_path):
    """Direct regression test for the QThread-era segfault this round
    removed: several sequential real previews (real QProcess, real ffprobe
    subprocess call inside the worker) in one long-lived GUI process must
    not crash. This is the shape of repeatedly clicking "Bekijk inhoud" in
    one session."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    window = MainWindow(
        detect_volumes=lambda: [],
        detect_destinations=_detect_destinations(tmp_path / "destination"),
        config_path=config_path,
        camera_profiles_path=CAMERA_PROFILES_PATH,
    )
    window._set_manual_source(input_dir)
    window.destination_cards()[0].click()

    for i in range(10):
        window.client_input().setText("Nike")
        window.project_input().setText("Zomer")
        window.choose_button().click()

        _run_preview_and_wait(window)

        assert window.current_message() == PREVIEW_TITLE_TEXT, f"run {i}: geen preview getoond"
        assert window._preview_runner is None
        window.secondary_action_button().click()  # Terug, klaar voor de volgende

    window.close()


def test_real_preview_followed_by_a_real_start_ingest_completes_both(qapp, tmp_path):
    """The exact preview -> Start Ingest transition (hoofdstuk 14 van de
    Fase 3-opdracht) end-to-end met twee echte QProcess-runs na elkaar — het
    bewijst dat er geen teardown-race meer nodig is om dit veilig te laten
    verlopen (zie main_window.py's `_on_start_ingest_clicked`, de oude
    10ms-retry-guard is verwijderd, niet vervangen)."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    window = MainWindow(
        detect_volumes=lambda: [],
        detect_destinations=_detect_destinations(tmp_path / "destination"),
        config_path=config_path,
        camera_profiles_path=CAMERA_PROFILES_PATH,
    )
    window._set_manual_source(input_dir)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer")
    window.destination_cards()[0].click()
    window.choose_button().click()

    _run_preview_and_wait(window)
    assert window.current_message() == PREVIEW_TITLE_TEXT
    assert window.choose_button().text() == START_INGEST_BUTTON_TEXT

    window.choose_button().click()  # Start Ingest — meteen ná de preview, geen guard/vertraging

    loop = QEventLoop()
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(loop.quit)
    poll_timer = QTimer()
    poll_timer.timeout.connect(lambda: (window._ingest_runner is None) and loop.quit())
    poll_timer.start(20)
    timeout_timer.start(15_000)
    loop.exec()

    assert window._ingest_runner is None, "de echte ingest is niet op tijd afgerond"
    copied = list((tmp_path / "destination" / "storage").rglob("DJI_0001.MP4"))
    assert len(copied) == 1

    window.close()


def test_closing_the_window_during_a_real_preview_does_not_crash(tmp_path):
    """End-to-end: a real preview (real QProcess), window closed while it's
    still running. Proves MainWindow._wait_for_preview_to_stop actually
    stops the worker process before the app is allowed to quit, without
    crashing (see _close_during_preview_scenario.py) — the direct
    replacement for the old QThread-era
    test_closing_right_after_a_volume_scan_scenario-style coverage."""
    script = Path(__file__).with_name("_close_during_preview_scenario.py")
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


def test_second_preview_blocked_and_close_during_first_does_not_crash(tmp_path):
    """The combined regression: (1) start a real preview, (2) immediately
    try to start a second one on top of it, (3) close the window while the
    first is still running — all in one session (see
    _preview_lifecycle_stress_scenario.py)."""
    script = Path(__file__).with_name("_preview_lifecycle_stress_scenario.py")
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

"""Standalone scenario script: starts a REAL ingest (Fase 3, via QProcess —
see desktop/ingest_process.py) and closes the window while it is still
running. Proves the app doesn't crash and the underlying worker process is
stopped before the app actually exits (see
MainWindow._wait_for_ingest_to_stop, wired from both closeEvent() and
app.aboutToQuit — same two-quit-path pattern as the preview's QThread, see
_quit_during_analysis_scenario.py).

Run as its own fresh process (see test_desktop_thread_lifecycle.py's
scenario scripts) — a faithful, isolated reproduction of a real
many-ingest-desktop session, not sharing state with unrelated tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import PREVIEW_TITLE_TEXT, MainWindow

_MAX_POLLS = 500  # 500 * 20ms = 10s veiligheidsgrens tegen een oneindige wachtlus
_poll_count = 0


def main() -> None:
    app = QApplication(sys.argv)

    tmp_dir = Path(sys.argv[1])
    input_dir = tmp_dir / "input"
    input_dir.mkdir(exist_ok=True)
    for i in range(30):
        (input_dir / f"C{i:04d}.MP4").write_bytes(b"x" * 2_000_000)
    config_path = tmp_dir / "config.yaml"
    config_path.write_text(
        f"storage_root: {tmp_dir / 'storage'}\n"
        f"manifest_path: {tmp_dir / 'asset_schema.json'}\n"
        f"log_dir: {tmp_dir / 'logs'}\n"
    )
    camera_profiles_path = (
        Path(__file__).resolve().parents[2]
        / "modules"
        / "many_ingest"
        / "config"
        / "camera_profiles.yaml"
    )

    window = MainWindow(
        detect_volumes=lambda: [], config_path=config_path, camera_profiles_path=camera_profiles_path
    )
    app.aboutToQuit.connect(window._wait_for_analysis_to_stop)
    app.aboutToQuit.connect(window._wait_for_ingest_to_stop)
    window._set_manual_source(input_dir)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer")

    def wait_for_preview_then_start_ingest() -> None:
        global _poll_count
        if window.current_message() == PREVIEW_TITLE_TEXT:
            window.choose_button().click()  # Start Ingest
            QTimer.singleShot(50, close_during_ingest)
            return
        _poll_count += 1
        if _poll_count > _MAX_POLLS:
            print("TIMEOUT: preview kwam niet op tijd")
            app.quit()
            return
        QTimer.singleShot(20, wait_for_preview_then_start_ingest)

    def close_during_ingest() -> None:
        assert window._ingest_runner is not None and window._ingest_runner.is_running(), (
            "test-aanname: de echte ingest moet nog bezig zijn"
        )
        window.close()
        print("OK")
        # Zie de andere scenario-scripts: window.close() alleen is in
        # headless/offscreen-modus niet altijd genoeg om
        # quitOnLastWindowClosed te laten vuren.
        app.quit()

    def start_real_dry_run() -> None:
        window.choose_button().click()  # Bekijk inhoud (echte dry-run)
        QTimer.singleShot(20, wait_for_preview_then_start_ingest)

    QTimer.singleShot(0, start_real_dry_run)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

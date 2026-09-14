"""Standalone scenario script: starts a REAL preview (via QProcess — see
desktop/ingest_process.py) and closes the window while it is still running.
Proves the app doesn't crash and the underlying worker process is stopped
before the app actually exits (see MainWindow._wait_for_preview_to_stop,
wired from both closeEvent() and app.aboutToQuit — same two-quit-path
pattern as a real ingest, see _close_during_real_ingest_scenario.py).

Run as its own fresh process (see other scenario scripts in this directory)
— a faithful, isolated reproduction of a real many-ingest-desktop session,
not sharing state with unrelated tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import MainWindow
from many_ingest.desktop.volumes import DestinationInfo

_MAX_POLLS = 500  # 500 * 20ms = 10s veiligheidsgrens tegen een oneindige wachtlus
_poll_count = 0


def main() -> None:
    app = QApplication(sys.argv)

    tmp_dir = Path(sys.argv[1])
    input_dir = tmp_dir / "input"
    input_dir.mkdir(exist_ok=True)
    for i in range(30):
        (input_dir / f"C{i:04d}.MP4").write_bytes(b"x" * 2_000_000)
    destination_root = tmp_dir / "destination"
    destination_root.mkdir(exist_ok=True)
    config_path = tmp_dir / "config.yaml"
    config_path.write_text(
        "footage_subpath: storage\nmanifest_subpath: asset_schema.json\nlog_subpath: logs\n"
    )
    camera_profiles_path = (
        Path(__file__).resolve().parents[2]
        / "modules"
        / "many_ingest"
        / "config"
        / "camera_profiles.yaml"
    )

    window = MainWindow(
        detect_volumes=lambda: [],
        detect_destinations=lambda source_path: [DestinationInfo(
            name="TestDisk", path=destination_root, free_bytes=1_000_000_000
        )],
        config_path=config_path,
        camera_profiles_path=camera_profiles_path,
    )
    app.aboutToQuit.connect(window._wait_for_preview_to_stop)
    app.aboutToQuit.connect(window._wait_for_ingest_to_stop)
    window._set_manual_source(input_dir)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer")
    window.destination_cards()[0].click()

    def close_during_preview() -> None:
        assert window._preview_runner is not None and window._preview_runner.is_running(), (
            "test-aanname: de preview moet nog bezig zijn"
        )
        window.close()
        print("OK")
        # Zie de andere scenario-scripts: window.close() alleen is in
        # headless/offscreen-modus niet altijd genoeg om
        # quitOnLastWindowClosed te laten vuren.
        app.quit()

    def start_real_preview() -> None:
        window.choose_button().click()  # Bekijk inhoud (echte preview)
        QTimer.singleShot(20, close_during_preview)

    QTimer.singleShot(0, start_real_preview)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

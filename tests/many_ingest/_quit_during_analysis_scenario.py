"""Standalone scenario script: starts a real dry-run analysis, then calls
`QApplication.quit()` directly — NOT `window.close()` — while it's still
running.

This is what macOS actually sends for Cmd+Q / the app-menu "Quit" item
(confirmed by direct reproduction): it never delivers a `QCloseEvent` to the
window, so `MainWindow.closeEvent()` alone never runs. Before wiring
`app.aboutToQuit` to `MainWindow._wait_for_analysis_to_stop()` in app.py,
this exact sequence reliably crashed with SIGABRT
("QThread: Destroyed while thread is still running"), even though
`closeEvent()` had already been fixed in a previous round — because this
path never went through `closeEvent()` at all.

Run as its own fresh process (see test_desktop_thread_lifecycle.py) — the
most faithful reproduction of a real `many-ingest-desktop` session, and
avoids sharing process state with unrelated tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)

    tmp_dir = Path(sys.argv[1])
    input_dir = tmp_dir / "input"
    input_dir.mkdir(exist_ok=True)
    for i in range(20):
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
    app.aboutToQuit.connect(window._wait_for_analysis_to_stop)  # exactly what app.py does
    window._set_manual_source(input_dir)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer")

    def start_then_quit() -> None:
        window.choose_button().click()
        assert window._analysis_thread is not None and window._analysis_thread.isRunning(), (
            "test-aanname: de analyse moet nog bezig zijn"
        )
        app.quit()  # Cmd+Q-equivalent — geen window.close()
        print("OK")

    # Moet binnen een echt lopende eventloop gebeuren — anders vuurt
    # aboutToQuit niet zoals in de echte app (dat was de bug in een eerdere
    # versie van dit script: het riep app.quit() aan vóórdat app.exec() ooit
    # draaide, waardoor de wacht-logica niet echt werd getriggerd).
    QTimer.singleShot(0, start_then_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""Standalone scenario script combining two cases in one real app session:

1. Start a real preview (a real `IngestRunner`/`QProcess`, see
   desktop/ingest_process.py).
2. Immediately try to start a second one (direct call, bypassing whatever
   the UI would normally allow/disallow — the more adversarial of the two,
   since a real click can't even reach `_start_preview` twice this fast: the
   button is gone from the moment the first click renders the "analyzing"
   state) — must be a no-op, the first runner must stay in place.
3. Close the window while the (first, still-only) preview is still running.

Until this round, this scenario exercised the preview's QThread lifecycle
(see git history — `_thread_lifecycle_stress_scenario.py`). The preview now
runs the same way a real ingest already did: a one-shot `IngestRunner`
wrapping a `QProcess`, so there is no QThread left to race.

Run as its own fresh process so each repetition is an independent, faithful
`many-ingest-desktop` session, and a failure on run N never depends on what
runs 1..N-1 left behind.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from many_ingest.desktop.main_window import MainWindow
from many_ingest.desktop.volumes import DestinationInfo


def main() -> None:
    app = QApplication(sys.argv)

    tmp_dir = Path(sys.argv[1])
    input_dir = tmp_dir / "input"
    input_dir.mkdir(exist_ok=True)
    for i in range(20):
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
    app.aboutToQuit.connect(window._wait_for_eject_to_stop)
    window._set_manual_source(input_dir)
    window.client_input().setText("Nike")
    window.project_input().setText("Zomer")
    window.destination_cards()[0].click()  # nodig: _start_preview() vereist een gekozen bestemming

    def run_scenario() -> None:
        # 1. start
        window._start_preview("Nike", "Zomer")
        first_runner = window._preview_runner
        assert first_runner is not None and first_runner.is_running(), (
            "test-aanname: de eerste preview moet nog bezig zijn"
        )

        # 2. direct een tweede proberen te starten
        window._start_preview("Nike", "Zomer")
        assert window._preview_runner is first_runner, (
            "de eerste, nog actieve runner-referentie mag nooit overschreven worden "
            "door een tweede startpoging"
        )

        # 3. venster sluiten tijdens de (nog lopende) preview
        window.close()

        print("OK")
        # `window.close()` alleen is in headless/offscreen-modus niet altijd
        # genoeg om quitOnLastWindowClosed te laten vuren — expliciet
        # app.quit() aanroepen zodat dit script zelf niet blijft hangen.
        app.quit()

    QTimer.singleShot(0, run_scenario)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

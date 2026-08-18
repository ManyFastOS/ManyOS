"""Composition root for Many Ingest Desktop — wiring only, no business logic.

Mirrors cli.py's role (see CLAUDE.md: "CLI is a thin adapter only"). Fase 1
still does not construct IngestService or any adapter — it only reads
`storage_root` from the existing config (same config cli.py already reads) so
volume detection can exclude the destination disk, and opens the window.

Loading config is best-effort: if it's missing or invalid, the app still
opens — it just can't exclude the destination volume yet (see
docs/MANY_INGEST_V0.1_READINESS_ASSESSMENT.md on failing loudly vs.
degrading gracefully — a missing *optional* piece of context degrades here,
unlike the ffprobe pre-flight check, which is required for a real run).
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path

import yaml
from PySide6.QtWidgets import QApplication

from many_ingest.config import load_ingest_config
from many_ingest.desktop.main_window import MainWindow
from many_ingest.desktop.theme import STYLE_SHEET
from many_ingest.desktop.volumes import list_candidate_volumes

DEFAULT_CONFIG_PATH = Path("~/.many-ingest/config.yaml").expanduser()


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE_SHEET)

    storage_root = _load_storage_root_safely(DEFAULT_CONFIG_PATH)
    detect_volumes = functools.partial(list_candidate_volumes, storage_root=storage_root)

    window = MainWindow(detect_volumes=detect_volumes)
    # Required in addition to MainWindow.closeEvent(), not instead of it:
    # QApplication.quit() — what macOS actually sends for Cmd+Q / the
    # app-menu Quit item, confirmed by reproduction — never delivers a
    # QCloseEvent to the window, so closeEvent() alone misses that path.
    # aboutToQuit fires for every quit path. See
    # MainWindow._wait_for_analysis_to_stop for the shared, idempotent logic,
    # and _wait_for_ingest_to_stop for the Fase 3 (QProcess) equivalent.
    app.aboutToQuit.connect(window._wait_for_analysis_to_stop)
    app.aboutToQuit.connect(window._wait_for_ingest_to_stop)
    window.show()

    sys.exit(app.exec())


def _load_storage_root_safely(config_path: Path) -> Path | None:
    try:
        return load_ingest_config(config_path).storage_root
    except (OSError, ValueError, yaml.YAMLError):
        return None


if __name__ == "__main__":
    main()

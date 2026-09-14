"""Composition helper: wires the exact same IngestService as cli.py.

Deliberately Qt-free — this must be importable (and imported) by
`ingest_worker.py`, which runs as a separate OS process specifically so it
never shares PySide6/Qt state with the desktop GUI process (see
ingest_worker.py's module docstring). `desktop/controller.py` used to define
this function itself; it now imports it from here instead, since importing
`controller.py` directly would drag PySide6 into the worker's import graph
just to reach this one wiring function.
"""

from __future__ import annotations

from pathlib import Path

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage
from many_ingest.config import load_camera_profiles, load_storage_layout, resolve_ingest_config
from many_ingest.core.ingest_service import IngestService


def build_ingest_service(
    config_path: Path, camera_profiles_path: Path, destination_root: Path
) -> IngestService:
    """Same wiring as cli.py's composition root — no second implementation
    of adapter selection anywhere in the codebase.

    `destination_root` is the physical disk chosen for THIS ingest (Fase
    3.5) — config.yaml only holds the relative storage layout (see
    config.py); the two are joined here, once, right before `IngestConfig`
    is built. `IngestService` itself never sees `destination_root` as such,
    only the resolved, absolute `IngestConfig` it always took."""
    layout = load_storage_layout(config_path)
    ingest_config = resolve_ingest_config(layout, destination_root)
    camera_profiles = load_camera_profiles(camera_profiles_path)
    return IngestService(
        storage=LocalFilesystemStorage(),
        manifest=JSONManifest(ingest_config.manifest_path),
        config=ingest_config,
        camera_profiles=camera_profiles,
    )

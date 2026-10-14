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

import os
from pathlib import Path

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage
from many_ingest.config import load_camera_profiles, load_storage_layout, resolve_ingest_config
from many_ingest.core.ingest_service import DestinationUnavailableError, IngestService
from many_ingest.device_identity import ALLOW_SAME_DEVICE_ENV_VAR, same_physical_device


def build_ingest_service(
    config_path: Path, camera_profiles_path: Path, destination_root: Path, source: Path
) -> IngestService:
    """Same wiring as cli.py's composition root — no second implementation
    of adapter selection anywhere in the codebase.

    `destination_root` is the physical disk chosen for THIS ingest (Fase
    3.5) — config.yaml only holds the relative storage layout (see
    config.py); the two are joined here, once, right before `IngestConfig`
    is built. `IngestService` itself never sees `destination_root` as such,
    only the resolved, absolute `IngestConfig` it always took.

    `source` (Fase 5.3.2 follow-up review, point 3) is required so this can
    independently re-check the Fase 3.5 hard safety rule — source and
    destination may never be the same physical disk — against the FULLY
    RESOLVED `storage_root`, not just the raw `destination_root` a caller
    (cli.py/ingest_worker.py) already checked before this function was even
    called. `same_physical_device()`'s own `stat()`-based `device_id()`
    already follows symlinks natively, so this closes a real gap: a symlink
    inside `footage_subpath` (config.yaml's relative layout) could redirect
    `storage_root` back onto the source disk even though `destination_root`
    itself is genuinely a different device — empirically confirmed during
    the follow-up review as a theoretical risk worth closing. Applies to
    both a dry-run and a real ingest identically (same reasoning the
    existing, shallower destination_root-level check already uses for both
    modes — "same physical device" is a setup-sanity check, not a
    destination-availability check like `_ensure_destination_is_writable`,
    so there is no dry-run exemption here). Raises the same
    `DestinationUnavailableError` the caller already catches around
    `IngestService.run()` — no new exception type needed.

    `os.environ.get(ALLOW_SAME_DEVICE_ENV_VAR) != "1"` is the same test-only
    escape hatch `ingest_worker.py`'s own, shallower check already uses
    (device_identity.py) — automated tests run source and destination under
    one shared `pytest tmp_path`, necessarily one physical device, with no
    portable way to fake a second one."""
    layout = load_storage_layout(config_path)
    ingest_config = resolve_ingest_config(layout, destination_root)
    if os.environ.get(ALLOW_SAME_DEVICE_ENV_VAR) != "1" and same_physical_device(
        source, ingest_config.storage_root
    ):
        raise DestinationUnavailableError(
            "De bron en de uiteindelijke bestemmingsmap bevinden zich op "
            "dezelfde fysieke schijf (mogelijk via een symlink in de "
            "opslagstructuur). Kies een andere bestemmingsschijf."
        )
    camera_profiles = load_camera_profiles(camera_profiles_path)
    return IngestService(
        storage=LocalFilesystemStorage(),
        manifest=JSONManifest(ingest_config.manifest_path),
        config=ingest_config,
        camera_profiles=camera_profiles,
    )

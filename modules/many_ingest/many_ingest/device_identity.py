"""Physical-device identity — shared by the GUI, CLI, and worker so "is this
the same physical disk" is answered by exactly one implementation, not three
(see CLAUDE.md: Fase 3.5 — Dynamic Destination Selection).

Deliberately Qt-free (importable from ingest_worker.py and cli.py without
pulling in PySide6) and filesystem-only (`st_dev`) — never path strings or
volume names. Two different-looking paths can be the same physical device
(a chosen destination root and a not-yet-created subfolder under it, or a
symlink); two different-sounding volume names prove nothing either way. This
is the one safety-net Many Ingest uses everywhere it needs to know whether a
source and a chosen destination are, physically, the same disk.
"""

from __future__ import annotations

from pathlib import Path


def device_id(path: Path) -> int | None:
    """The filesystem device id of the nearest existing ancestor of `path`.

    `path` itself may not exist yet (e.g. a destination subfolder Many
    Ingest hasn't created yet) — walks up to whatever ancestor is actually
    reachable. `None` only if no ancestor at all is reachable (a fully
    unreadable/gone path), which callers should treat as "can't prove
    same-device", not as "definitely different".
    """
    current = Path(path)
    while True:
        try:
            return current.stat().st_dev
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            return None
        current = parent


def same_physical_device(path_a: Path, path_b: Path) -> bool:
    """True only if both paths resolve to a reachable ancestor with an
    identical `st_dev` — never inferred from path strings or volume names."""
    device_a = device_id(path_a)
    device_b = device_id(path_b)
    return device_a is not None and device_a == device_b

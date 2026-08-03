"""Storage port: how Many Ingest reads from and writes to a location.

`copy`/`checksum` were added here (not in v0.1's first cut) once the copy engine
actually needed them — no speculative interface surface ahead of a real need
(VISION.md: Simplicity Wins).
"""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Iterator


class Storage(abc.ABC):
    @abc.abstractmethod
    def list_files(self, root: Path) -> Iterator[Path]:
        """Yield every relevant file under `root`, recursively.

        Implementations must skip system/hidden files (e.g. .DS_Store) rather than
        surface them as ingestable assets.
        """

    @abc.abstractmethod
    def checksum(self, path: Path) -> str:
        """Return a SHA-256 hex digest of the file's contents."""

    @abc.abstractmethod
    def copy(self, source: Path, destination: Path) -> None:
        """Copy `source` to `destination`, creating parent directories as needed.

        Never touches `source` — v0.1 is copy-only, no move (see CLAUDE.md).
        """

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
    def exists(self, path: Path) -> bool:
        """Return True if a file already exists at `path`.

        Added for collision protection: the ingest pipeline must know whether a
        destination is already occupied before ever writing to it.
        """

    @abc.abstractmethod
    def checksum(self, path: Path) -> str:
        """Return a SHA-256 hex digest of the file's contents."""

    @abc.abstractmethod
    def copy(self, source: Path, destination: Path) -> str | None:
        """Copy `source` to `destination`, creating parent directories as needed.

        Never touches `source` — v0.1 is copy-only, no move (see CLAUDE.md).

        Content and metadata (timestamps/mode/xattrs/BSD flags) are two
        different concerns with two different failure severities. A failure
        to copy the actual bytes must raise `OSError` — that's a real
        failure, the caller cannot trust the destination. A failure to
        replicate metadata onto an otherwise-fully-copied file must NOT
        raise: the content is fine and will still be checksum-verified by
        the caller. Instead, return a short, human-readable warning message
        describing what metadata could not be preserved; return `None` when
        metadata was replicated without issue. See adapters/local_fs_storage.py
        for the concrete case this distinction exists for (`os.chflags()`
        EPERM on exFAT).
        """

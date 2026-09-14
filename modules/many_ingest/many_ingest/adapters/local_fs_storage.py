"""Local filesystem implementation of the Storage port."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Iterator

from many_ingest.ports.storage import Storage

_IGNORED_NAMES = {".DS_Store", "Thumbs.db", ".Spotlight-V100", ".Trashes", ".fseventsd"}


class LocalFilesystemStorage(Storage):
    def list_files(self, root: Path) -> Iterator[Path]:
        root = Path(root)
        if not root.is_dir():
            raise NotADirectoryError(f"Inputmap bestaat niet of is geen map: {root}")

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.name in _IGNORED_NAMES or path.name.startswith("."):
                continue
            yield path

    def exists(self, path: Path) -> bool:
        return Path(path).exists()

    def checksum(self, path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def copy(self, source: Path, destination: Path) -> str | None:
        """Content first, metadata best-effort — deliberately not a single
        `shutil.copy2()` call. `copy2()` is `copyfile()` + `copystat()`
        internally, and its own error handling only tolerates
        ENOTSUP/EOPNOTSUPP from the final `chflags()` step, re-raising
        everything else — including `EPERM`, which a real Sony camera card's
        protected index files (e.g. SONYCARD.IND, marked `uchg`/immutable on
        exFAT) reliably trigger when that flag can't be replicated onto an
        exFAT destination. Splitting the two calls means a genuine content
        failure (`copyfile` raising) still propagates as a real error, while
        a metadata-only failure (`copystat` raising after the bytes are
        already safely on disk) becomes a warning instead of a false
        "failed_verification" on a perfectly good copy."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        try:
            shutil.copystat(source, destination)
        except OSError as exc:
            return (
                "Bestand is correct gekopieerd, maar metadata (tijden/rechten/"
                f"vlaggen) kon niet volledig worden overgenomen: {exc}"
            )
        return None

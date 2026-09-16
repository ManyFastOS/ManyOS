"""JSON-file implementation of the Manifest port — the v0.1 ManyFast Asset Schema store.

Temporary storage choice (see CLAUDE.md and docs/MANY_INGEST_BUILD_PLAN.md): behind
the same Manifest interface, so moving to SQLite/Postgres later is an adapter swap,
not a rewrite.

`register()` writes atomically (temp file + rename, see `_write_atomic` below) —
added after a real manual test drove a destination disk to 0 bytes free
mid-run (2026-09-15). The previous plain `write_text()` truncates the file
the instant it opens, before writing a single byte; an ENOSPC (or any other
write failure) partway through would have silently lost every
previously-registered asset. `Path.replace()` only swaps the directory entry
once the new content is fully and successfully written, so a failed write
now always leaves the existing, valid manifest exactly as it was — the
`OSError` still propagates unchanged for the caller (core/ingest_service.py)
to classify.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from many_ingest.ports.manifest import AssetRecord, Manifest


class JSONManifest(Manifest):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def is_duplicate(self, checksum: str) -> bool:
        return checksum in self._load_asset_ids()

    def register(self, record: AssetRecord) -> None:
        data = self._load()
        asset_dict = dataclasses.asdict(record)
        asset_dict["original_path"] = str(record.original_path)
        asset_dict["destination_path"] = str(record.destination_path)
        asset_dict["source_relative_path"] = (
            str(record.source_relative_path) if record.source_relative_path is not None else None
        )
        data.setdefault("assets", []).append(asset_dict)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(json.dumps(data, indent=2, ensure_ascii=False))

    def _write_atomic(self, text: str) -> None:
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        try:
            tmp_path.write_text(text, encoding="utf-8")
            tmp_path.replace(self._path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise

    def _load(self) -> dict:
        if not self._path.exists():
            return {"assets": []}
        return json.loads(self._path.read_text() or '{"assets": []}')

    def _load_asset_ids(self) -> set[str]:
        return {asset["asset_id"] for asset in self._load().get("assets", []) if "asset_id" in asset}

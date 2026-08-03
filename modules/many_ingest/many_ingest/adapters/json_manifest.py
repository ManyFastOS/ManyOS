"""JSON-file implementation of the Manifest port — the v0.1 ManyFast Asset Schema store.

Temporary storage choice (see CLAUDE.md and docs/MANY_INGEST_BUILD_PLAN.md): behind
the same Manifest interface, so moving to SQLite/Postgres later is an adapter swap,
not a rewrite.
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
        data.setdefault("assets", []).append(asset_dict)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _load(self) -> dict:
        if not self._path.exists():
            return {"assets": []}
        return json.loads(self._path.read_text() or '{"assets": []}')

    def _load_asset_ids(self) -> set[str]:
        return {asset["asset_id"] for asset in self._load().get("assets", []) if "asset_id" in asset}

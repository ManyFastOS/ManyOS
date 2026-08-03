"""Manifest port: the ManyFast Asset Schema contract.

`register` was added here once the copy engine needed it, extending the read-only
interface from the dry-run step — an extension, not a redesign (see CLAUDE.md).
"""

from __future__ import annotations

import abc
import dataclasses
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class AssetRecord:
    asset_id: str  # SHA-256 checksum
    client_id: str
    project_id: str
    ingest_run_id: str
    operator: str
    source_machine: str
    original_path: Path
    destination_path: Path
    category: str
    camera_profile: str
    confidence: str
    ingested_at: str


class Manifest(abc.ABC):
    @abc.abstractmethod
    def is_duplicate(self, checksum: str) -> bool:
        """Return True if an asset with this checksum is already recorded."""

    @abc.abstractmethod
    def register(self, record: AssetRecord) -> None:
        """Persist a newly copied asset's record."""

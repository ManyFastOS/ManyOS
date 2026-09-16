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
    # Fase 5.0 — additief. Defaulted (not just nullable) so existing call sites
    # that construct an AssetRecord without them keep working unchanged; every
    # NEW record written by IngestService always passes real values explicitly
    # (see core/ingest_service.py). Old, already-persisted JSON manifest
    # entries simply lack these keys entirely — no migration in Fase 5.0, and
    # nothing here requires one (is_duplicate() only ever reads asset_id).
    media_type: str = "unknown"
    manufacturer: str | None = None
    model: str | None = None
    source_relative_path: Path | None = None
    # Fase 5.1 — additief, same backward-compat pattern as Fase 5.0 above.
    # classification_source is typed as plain str (the enum's .value), not
    # the ClassificationSource enum itself — same deliberate reason
    # `confidence` above is already a plain str: this ports module must not
    # depend on the classification package's implementation details.
    # Technical metadata fields are nullable per Fase 5.1's None-vs-False
    # rule: None means "no reliable probe data" (ffprobe didn't run/failed),
    # False means "ffprobe ran and definitively found no such stream" — never
    # conflated.
    classification_source: str = "no_signal_matched"
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: str | None = None
    duration_seconds: float | None = None
    has_video_stream: bool | None = None
    has_audio_stream: bool | None = None
    # Fase 5.2 — additief, same backward-compat pattern as above.
    # relationship_evidence is typed as plain str (the RelationshipEvidence
    # enum's .value), same deliberate ports/classification decoupling reason
    # as classification_source above. sidecar_of_asset_id is the main
    # media's own asset_id (SHA-256) — never a new kind of identity; a
    # sidecar keeps its own separate asset_id/checksum unchanged.
    sidecar_of_asset_id: str | None = None
    relationship_evidence: str = "none"


class Manifest(abc.ABC):
    @abc.abstractmethod
    def is_duplicate(self, checksum: str) -> bool:
        """Return True if an asset with this checksum is already recorded."""

    @abc.abstractmethod
    def register(self, record: AssetRecord) -> None:
        """Persist a newly copied asset's record."""

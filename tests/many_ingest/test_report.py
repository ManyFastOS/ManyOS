"""Tests for the human-readable ingest summary (core/report.py).

`IngestSummary`/`summarize` are plain data derived from an `IngestReport` — tests
build small `IngestReport`/`AssetResult` fixtures directly rather than running a
full ingest, since the counting/rendering logic doesn't depend on real files.
"""

from __future__ import annotations

from pathlib import Path

from many_ingest.classification.camera_profiles import Confidence
from many_ingest.classification.file_types import FileType
from many_ingest.core.ingest_service import AssetOutcome, AssetResult, IngestReport
from many_ingest.core.report import render_report, summarize


def _asset(
    outcome: AssetOutcome,
    file_type: FileType = FileType.VIDEO,
    camera_profile: str = "Sony FX6",
    name_conflict_resolved: bool = False,
    metadata_warning: str | None = None,
) -> AssetResult:
    return AssetResult(
        source_path=Path("/in/clip.mp4"),
        destination_path=Path("/out/clip.mp4"),
        file_type=file_type,
        category="Camera",
        camera_profile=camera_profile,
        confidence=Confidence.HIGH,
        checksum="abc123",
        is_duplicate=(outcome == AssetOutcome.DUPLICATE_SKIPPED),
        outcome=outcome,
        name_conflict_resolved=name_conflict_resolved,
        metadata_warning=metadata_warning,
    )


def _report(assets: list[AssetResult], dry_run: bool = False) -> IngestReport:
    return IngestReport(
        source=Path("/in"),
        client="ManyFast",
        project="Jan Rotmans",
        dry_run=dry_run,
        run_id="run-1",
        log_path=Path("/logs/run-1.jsonl"),
        duration_seconds=761.0,  # 12m 41s
        total_bytes=914 * 1024**3,
        assets=assets,
        project_workspace_path=Path("/out/Klanten/ManyFast/Jan Rotmans"),
    )


def test_summarize_counts_by_type_profile_and_outcome():
    assets = [
        _asset(AssetOutcome.COPIED, FileType.VIDEO, "Sony FX6"),
        _asset(AssetOutcome.COPIED, FileType.VIDEO, "Sony FX6"),
        _asset(AssetOutcome.COPIED, FileType.VIDEO, "Sony FX3"),
        _asset(AssetOutcome.COPIED, FileType.AUDIO, "Audio"),
        _asset(AssetOutcome.DUPLICATE_SKIPPED, FileType.VIDEO, "Sony FX6"),
        _asset(AssetOutcome.COPIED, FileType.VIDEO, "Onbekend", name_conflict_resolved=True),
        _asset(AssetOutcome.FAILED_VERIFICATION, FileType.VIDEO, "Sony FX3"),
    ]
    summary = summarize(_report(assets))

    assert summary.total_files == 7
    assert summary.video_count == 6
    assert summary.audio_count == 1
    assert summary.camera_profile_counts == {
        "Sony FX6": 3,
        "Sony FX3": 2,
        "Audio": 1,
        "Onbekend": 1,
    }
    assert summary.duplicates == 1
    assert summary.name_conflicts_resolved == 1
    assert summary.errors == 1
    assert summary.metadata_warnings == 0


def test_summarize_counts_metadata_warnings_separately_from_errors():
    """A metadata-only warning (outcome COPIED, see ingest_service.py's
    false-negative fix) must never be counted as an error, and must never
    affect safe_to_delete_source — only a real failed_verification does."""
    assets = [
        _asset(AssetOutcome.COPIED, metadata_warning="tijden/rechten/vlaggen niet overgenomen"),
        _asset(AssetOutcome.COPIED),
    ]
    summary = summarize(_report(assets, dry_run=False))

    assert summary.metadata_warnings == 1
    assert summary.errors == 0
    assert summary.safe_to_delete_source is True


def test_safe_to_delete_is_false_when_there_are_errors():
    assets = [_asset(AssetOutcome.COPIED), _asset(AssetOutcome.FAILED_VERIFICATION)]
    summary = summarize(_report(assets, dry_run=False))
    assert summary.safe_to_delete_source is False


def test_safe_to_delete_is_true_when_a_real_run_has_no_errors():
    assets = [_asset(AssetOutcome.COPIED), _asset(AssetOutcome.DUPLICATE_SKIPPED)]
    summary = summarize(_report(assets, dry_run=False))
    assert summary.safe_to_delete_source is True


def test_safe_to_delete_is_never_true_for_a_dry_run_even_without_errors():
    assets = [_asset(AssetOutcome.PREVIEW)]
    summary = summarize(_report(assets, dry_run=True))
    assert summary.safe_to_delete_source is False


def test_render_report_includes_key_figures_and_ja_when_safe():
    assets = [_asset(AssetOutcome.COPIED, camera_profile="Sony FX6")]
    text = render_report(summarize(_report(assets, dry_run=False)))

    assert "✅ INGEST VOLTOOID" in text
    assert "Jan Rotmans" in text
    assert "Sony FX6:" in text
    assert "914.0 GB" in text
    assert "12m 41s" in text
    assert "Veilig om bronmedia te verwijderen:\nJA" in text


def test_render_report_shows_warning_header_and_nee_on_errors():
    assets = [_asset(AssetOutcome.FAILED_VERIFICATION)]
    text = render_report(summarize(_report(assets, dry_run=False)))

    assert "INGEST VOLTOOID MET FOUTEN" in text
    assert "Veilig om bronmedia te verwijderen:\nNEE" in text


def test_render_report_marks_dry_run_as_not_applicable():
    assets = [_asset(AssetOutcome.PREVIEW)]
    text = render_report(summarize(_report(assets, dry_run=True)))

    assert "PREVIEW VOLTOOID" in text
    assert "N.V.T." in text
    assert "JA" not in text.split("Veilig om bronmedia te verwijderen:")[1]


def test_render_report_shows_metadata_warnings_and_still_reports_ja_when_safe():
    """A metadata-only warning must be visible/traceable in the report, but
    must not turn the header into an error header or flip safe-to-delete."""
    assets = [
        _asset(AssetOutcome.COPIED, metadata_warning="tijden/rechten/vlaggen niet overgenomen")
    ]
    text = render_report(summarize(_report(assets, dry_run=False)))

    assert "✅ INGEST VOLTOOID" in text
    assert "Metadata-waarschuwingen:\n1" in text
    assert "Veilig om bronmedia te verwijderen:\nJA" in text


def test_render_report_omits_metadata_warnings_line_when_there_are_none():
    assets = [_asset(AssetOutcome.COPIED)]
    text = render_report(summarize(_report(assets, dry_run=False)))

    assert "Metadata-waarschuwingen" not in text


def test_summarize_exposes_the_engine_resolved_project_workspace_path():
    """Fase 4: the resolved Project Workspace path is engine-computed
    (IngestReport.project_workspace_path), never reconstructed by a caller —
    summarize() must carry it through unchanged, just stringified."""
    assets = [_asset(AssetOutcome.COPIED)]
    summary = summarize(_report(assets, dry_run=False))

    assert summary.destination_path == "/out/Klanten/ManyFast/Jan Rotmans"

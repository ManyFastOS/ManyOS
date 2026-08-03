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

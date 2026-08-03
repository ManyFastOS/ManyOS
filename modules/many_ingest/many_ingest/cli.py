"""Thin CLI layer — composition root only, no business logic (see CLAUDE.md)."""

from __future__ import annotations

from pathlib import Path

import click

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage
from many_ingest.config import load_camera_profiles, load_ingest_config
from many_ingest.core.ingest_service import (
    AssetOutcome,
    IngestReport,
    IngestService,
    ProgressUpdate,
)
from many_ingest.core.report import render_report, summarize
from many_ingest.metadata_extractor import FfprobeNotFoundError

DEFAULT_CONFIG_PATH = Path("~/.many-ingest/config.yaml").expanduser()
DEFAULT_CAMERA_PROFILES_PATH = Path("~/.many-ingest/camera_profiles.yaml").expanduser()

_OUTCOME_MARKERS = {
    AssetOutcome.COPIED: " [gekopieerd]",
    AssetOutcome.DUPLICATE_SKIPPED: " [DUPLICAAT — overgeslagen]",
}


@click.group()
def main() -> None:
    """Many Ingest — ManyOS's asset ingestion engine."""


@main.command()
@click.option(
    "--source", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option("--client", required=True)
@click.option("--project", required=True)
@click.option("--dry-run", is_flag=True, default=False)
@click.option(
    "--config", "config_path", type=click.Path(path_type=Path), default=DEFAULT_CONFIG_PATH
)
@click.option(
    "--camera-profiles",
    "camera_profiles_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_CAMERA_PROFILES_PATH,
)
def run(
    source: Path,
    client: str,
    project: str,
    dry_run: bool,
    config_path: Path,
    camera_profiles_path: Path,
) -> None:
    """Scan SOURCE and organize its video files into the Project Workspace."""
    ingest_config = load_ingest_config(config_path)
    camera_profiles = load_camera_profiles(camera_profiles_path)

    service = IngestService(
        storage=LocalFilesystemStorage(),
        manifest=JSONManifest(ingest_config.manifest_path),
        config=ingest_config,
        camera_profiles=camera_profiles,
    )

    try:
        report = service.run(
            source=source,
            client=client,
            project=project,
            dry_run=dry_run,
            progress_callback=_print_progress,
        )
    except FfprobeNotFoundError as exc:
        click.echo(f"\n{exc}", err=True)
        raise SystemExit(1) from exc

    click.echo()  # sluit de laatste voortgangsregel af met een newline
    _print_report(report)

    summary = summarize(report)
    report_text = render_report(summary)
    click.echo("\n" + report_text)

    report_path = report.log_path.with_name(f"{report.run_id}_report.txt")
    report_path.write_text(report_text, encoding="utf-8")


def _print_progress(update: ProgressUpdate) -> None:
    percentage = round((update.processed / update.total) * 100) if update.total else 100
    click.echo(
        f"\rVerwerken {update.processed}/{update.total} ({percentage}%) "
        f"— {update.current_file}" + " " * 20,
        nl=False,
    )


def _print_report(report: IngestReport) -> None:
    if report.dry_run:
        click.echo(
            f"Preview — er is niets gewijzigd ({len(report.assets)} bestand(en) "
            f"gevonden in {report.source}):"
        )
    else:
        click.echo(
            f"Ingest-run {report.run_id} — {len(report.assets)} bestand(en) "
            f"gevonden in {report.source}:"
        )

    for asset in report.assets:
        if report.dry_run:
            marker = " [DUPLICAAT]" if asset.is_duplicate else ""
        elif asset.outcome == AssetOutcome.FAILED_VERIFICATION:
            marker = f" [MISLUKT: {asset.error}]"
        else:
            marker = _OUTCOME_MARKERS.get(asset.outcome, "")

        if asset.name_conflict_resolved:
            marker += " [hernoemd i.v.m. naamconflict]"

        click.echo(
            f"  {asset.source_path.name} -> {asset.destination_path} "
            f"[{asset.camera_profile}, confidence={asset.confidence.value}]{marker}"
        )

    if report.dry_run:
        cmd = (
            f'many-ingest run --source {report.source} '
            f'--client "{report.client}" --project "{report.project}"'
        )
        click.echo(f"\nOm dit daadwerkelijk uit te voeren: {cmd}")
    else:
        copied = sum(1 for a in report.assets if a.outcome == AssetOutcome.COPIED)
        skipped = sum(1 for a in report.assets if a.outcome == AssetOutcome.DUPLICATE_SKIPPED)
        failed = sum(1 for a in report.assets if a.outcome == AssetOutcome.FAILED_VERIFICATION)
        click.echo(f"\nSamenvatting: {copied} gekopieerd, {skipped} duplicaten overgeslagen, {failed} mislukt.")

    click.echo(f"Actielogboek: {report.log_path}")


if __name__ == "__main__":
    main()

"""Thin CLI layer — composition root only, no business logic (see CLAUDE.md)."""

from __future__ import annotations

from pathlib import Path

import click

from many_ingest.adapters.json_manifest import JSONManifest
from many_ingest.adapters.local_fs_storage import LocalFilesystemStorage
from many_ingest.config import load_camera_profiles, load_ingest_config
from many_ingest.core.ingest_service import AssetOutcome, IngestReport, IngestService

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

    report = service.run(source=source, client=client, project=project, dry_run=dry_run)
    _print_report(report)


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

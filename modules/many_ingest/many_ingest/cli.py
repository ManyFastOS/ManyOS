"""Thin CLI layer — composition root only, no business logic (see CLAUDE.md)."""

from __future__ import annotations

from pathlib import Path

import click

from many_ingest.core.ingest_service import (
    AssetOutcome,
    DestinationFullError,
    DestinationUnavailableError,
    IngestReport,
    ProgressUpdate,
)
from many_ingest.core.report import render_report, summarize
from many_ingest.device_identity import same_physical_device
from many_ingest.metadata_extractor import FfprobeNotFoundError
from many_ingest.service_factory import build_ingest_service

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
# Verplicht, geen fallback (Fase 3.5 — Dynamic Destination Selection):
# ManyFast gebruikt meerdere externe bestemmingsschijven, niet één vaste, dus
# config.yaml bevat sinds deze ronde alleen nog de relatieve laag-structuur
# (zie config.py) — de fysieke schijf wordt altijd expliciet meegegeven, ook
# via de CLI. Bewust geen legacy-pad dat terugvalt op een oude, absolute
# storage_root-sleutel in config.yaml: één architectuur, geen twee.
@click.option(
    "--destination",
    "destination_root",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
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
    destination_root: Path,
    dry_run: bool,
    config_path: Path,
    camera_profiles_path: Path,
) -> None:
    """Scan SOURCE and organize its video files into the Project Workspace
    on DESTINATION."""
    # Harde safety rule: bron en bestemming mogen nooit dezelfde fysieke
    # schijf zijn (zie device_identity.py — hetzelfde safety-net als de GUI
    # en de worker gebruiken, niet een aparte CLI-eigen implementatie).
    if same_physical_device(source, destination_root):
        click.echo(
            "De bron en de gekozen bestemmingsschijf zijn dezelfde fysieke schijf. "
            "Kies een andere bestemmingsschijf.",
            err=True,
        )
        raise SystemExit(1)

    service = build_ingest_service(config_path, camera_profiles_path, destination_root)

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
    except DestinationFullError as exc:
        click.echo(f"\n{exc}", err=True)
        raise SystemExit(1) from exc
    except DestinationUnavailableError as exc:
        click.echo(f"\n{exc}", err=True)
        raise SystemExit(1) from exc

    click.echo()  # sluit de laatste voortgangsregel af met een newline
    _print_report(report)

    summary = summarize(report)
    report_text = render_report(summary)
    click.echo("\n" + report_text)

    report_path = report.log_path.with_name(f"{report.run_id}_report.txt")
    # CLI schrijft dit leesbare rapport altijd, ook bij --dry-run — ActionLogger
    # maakt log_dir sinds deze ronde alleen nog aan voor een echte run (zie
    # logger.py), dus deze eigen write moet zijn eigen map kunnen aanmaken.
    report_path.parent.mkdir(parents=True, exist_ok=True)
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
            f'many-ingest run --source {report.source} --destination <schijf> '
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

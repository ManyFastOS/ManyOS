"""Standalone ingest worker — runs BOTH a real ingest and a preview/dry-run
in their own OS process, started by the desktop app via QProcess (see
desktop/ingest_process.py), deliberately NOT as a QThread. One worker, one
process protocol, for both — see `--dry-run` below, not a second
implementation.

Why a separate process, not a background QThread (this used to be true only
for the real ingest; the preview used a QThread via desktop/controller.py
until this round): both a real ingest run and a preview repeat the same
heavy pattern — many real `ffprobe` subprocess calls interleaved with I/O,
for as long as the source has files — that this project's crash history
conclusively traced to a genuine PySide/Shiboken QThread-lifetime race (an
unparented `QThread()` whose C++ object could be torn down concurrently by
Python refcounting and by Qt's own `deleteLater()`, confirmed via a real
macOS crash report: `faultingThread: QThread`, `QThread::~QThread()` on the
main thread racing `QObject::~QObject()`/`disconnectNotify` on the
background thread). Repeated 10ms-retry/lifecycle-registry guards around
that QThread narrowed the race but never closed it. A separate OS process
has no such race by construction: there is no second thread, and no shared
Qt/Python object graph for two threads to tear down concurrently. A crash in
the worker (a segfault, an aborted C-extension call) cannot take the GUI
process down with it, and does not depend on being "rare enough" to avoid.

Zero GUI-toolkit knowledge — this module never imports PySide6/Qt — and can
be run standalone from a terminal for debugging:

    python -m many_ingest.ingest_worker --source /Volumes/SD_CARD_1 \\
        --client Nike --project "Zomer Campagne" \\
        --config ~/.many-ingest/config.yaml \\
        --camera-profiles ~/.many-ingest/camera_profiles.yaml --mode copy

    # preview / dry-run — add --dry-run, nothing else changes:
    python -m many_ingest.ingest_worker --source /Volumes/SD_CARD_1 \\
        --client Nike --project "Zomer Campagne" \\
        --config ~/.many-ingest/config.yaml \\
        --camera-profiles ~/.many-ingest/camera_profiles.yaml --mode copy \\
        --dry-run

Mirrors cli.py's role (composition root, no business logic — see CLAUDE.md):
it wires the exact same `IngestService` via `service_factory.py` and calls
`IngestService.run(dry_run=..., ...)` unchanged. No second ingest
implementation.

Communicates exclusively via JSON-lines on stdout — one self-contained JSON
object per line. The same event set is used for both modes (see
desktop/ingest_process.py — one generic runner, not two):

    ingest_started    {source, client, project}
    progress          {processed, total, current_file, bytes_processed}
                      — one per file, straight from IngestService's own
                      progress_callback, real-time.
    asset_processed   {source_path, destination_path, camera_profile,
                      is_duplicate, name_conflict_resolved, outcome, error}
                      — one per asset, emitted from the finished
                      IngestReport once the whole run is done. The engine's
                      progress_callback only carries aggregate counters, not
                      per-asset outcome, so this cannot be streamed live
                      without changing IngestService — out of scope for this
                      phase (CLAUDE.md: no engine changes for the GUI).
    ingest_completed  the IngestSummary fields (summarize()'s own shape,
                      unchanged — core/report.py), reused as-is so the GUI
                      can reconstruct a real IngestSummary directly.
    ingest_failed     {message} — already translated to plain language,
                      worded for whichever mode is running (see
                      `_unexpected_error_message()` below). Never a stack
                      trace on stdout.
    ingest_cancelled  {} — a deliberate stop via SIGTERM, never a failure.

For a dry run, `ingest_completed` additionally gets its `duplicates` count
corrected before being emitted — see `_corrected_summary()` below for why
(a real, pre-existing gap in `core/report.py`'s own dry-run reporting, not
introduced here and not fixed there, since that's shared engine code).

Cancellation: SIGTERM sets a module-level flag, checked from inside the
progress callback IngestService already calls after every file — a signal
handler, since there is no in-process object to call a method on across an
OS process boundary. The current file is always allowed to finish (a dry run
never writes; a real run is copy-then-verify, never an interrupted write)
before the run actually stops.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import signal
import sys
from pathlib import Path

from many_ingest.core.ingest_service import IngestReport, ProgressUpdate
from many_ingest.core.report import summarize
from many_ingest.metadata_extractor import FfprobeNotFoundError
from many_ingest.service_factory import build_ingest_service

EXIT_SUCCESS = 0
EXIT_FAILED = 1
EXIT_CANCELLED = 2

_FFPROBE_MISSING_MESSAGE = (
    "Deze Mac mist een onderdeel dat nodig is om bestanden te analyseren. "
    "Neem contact op met de beheerder."
)
_CONFIG_INVALID_MESSAGE = (
    "De basisinstellingen voor Many Ingest zijn nog niet klaar op deze Mac. "
    "Neem contact op met de beheerder."
)
_SOURCE_UNREADABLE_MESSAGE = (
    "Kon deze locatie niet meer lezen. Controleer of de schijf nog is "
    "aangesloten en probeer het opnieuw."
)
_UNEXPECTED_ERROR_MESSAGE_PREVIEW = "Er ging iets mis tijdens het analyseren. Probeer het opnieuw."
_UNEXPECTED_ERROR_MESSAGE_COPY = "Er ging iets mis tijdens het kopiëren. Probeer het opnieuw."


def _unexpected_error_message(dry_run: bool) -> str:
    return _UNEXPECTED_ERROR_MESSAGE_PREVIEW if dry_run else _UNEXPECTED_ERROR_MESSAGE_COPY


def _corrected_summary(report: IngestReport):
    """`summarize()` from core/report.py, with one correction for dry-run
    previews — not a reimplementation, a targeted fix of a real gap (moved
    here unchanged from the QThread-era desktop/controller.py, which this
    round removes).

    `IngestSummary.duplicates` counts assets whose *outcome* is
    `DUPLICATE_SKIPPED` — but `_process_asset` in ingest_service.py always
    sets `outcome = PREVIEW` during a dry run (the `dry_run` branch is
    checked before the `is_duplicate` branch), so `summary.duplicates` is
    always 0 for a dry run, even though each `AssetResult.is_duplicate` is
    still set correctly. This is a pre-existing gap in core/report.py's own
    dry-run reporting (the CLI has the same blind spot) — not introduced
    here and not fixed there (that touches shared engine code, out of scope
    for this phase). This only re-aggregates a field the engine already
    computes and exposes on every asset, for the one screen that needs an
    accurate dry-run duplicate count.
    """
    summary = summarize(report)
    if not summary.dry_run:
        return summary
    actual_duplicates = sum(1 for asset in report.assets if asset.is_duplicate)
    return dataclasses.replace(summary, duplicates=actual_duplicates)


_cancel_requested = False


class _IngestCancelled(Exception):
    """Internal-only: never printed, never crosses this process's boundary
    except as the `ingest_cancelled` event."""


def _handle_sigterm(signum, frame) -> None:
    global _cancel_requested
    _cancel_requested = True


def _emit(event: str, **fields: object) -> None:
    # Eén print-aanroep per event, altijd direct geflusht — de GUI-kant leest
    # via readyReadStandardOutput en mag nooit op een OS-buffer hoeven wachten.
    # Elke regel is zelfstandig geldige JSON (zie moduledocstring).
    print(json.dumps({"event": event, **fields}, default=str, ensure_ascii=False), flush=True)


def _progress_callback(update: ProgressUpdate) -> None:
    _emit(
        "progress",
        processed=update.processed,
        total=update.total,
        current_file=update.current_file,
        bytes_processed=update.bytes_processed,
    )
    if _cancel_requested:
        raise _IngestCancelled()


def _emit_assets(report: IngestReport) -> None:
    for asset in report.assets:
        _emit(
            "asset_processed",
            source_path=str(asset.source_path),
            destination_path=str(asset.destination_path),
            camera_profile=asset.camera_profile,
            is_duplicate=asset.is_duplicate,
            name_conflict_resolved=asset.name_conflict_resolved,
            outcome=asset.outcome.value,
            error=asset.error,
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="many-ingest-worker")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--client", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--config", dest="config_path", required=True, type=Path)
    parser.add_argument(
        "--camera-profiles", dest="camera_profiles_path", required=True, type=Path
    )
    # Enige geldige waarde vandaag — expliciet als keuze gemodelleerd (i.p.v.
    # een kale copy-aanname) zodat een latere move-mode een nieuwe waarde is,
    # geen nieuwe vlag (zie CLAUDE.md: move volgt later als Storage-uitbreiding).
    parser.add_argument("--mode", default="copy", choices=["copy"])
    # Orthogonaal aan --mode (copy vs. een latere move) — dit schakelt tussen
    # een preview (dry_run=True, niets geschreven) en een echte run, dezelfde
    # as als IngestService.run()'s eigen dry_run-parameter.
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except (ValueError, OSError):
        # Bekende platformbeperking (Windows heeft geen echte POSIX-signalen)
        # — geaccepteerd voor nu, zie de architectuuraantekeningen bij Fase 3.
        pass

    args = _parse_args(sys.argv[1:] if argv is None else argv)

    _emit("ingest_started", source=str(args.source), client=args.client, project=args.project)

    try:
        service = build_ingest_service(args.config_path, args.camera_profiles_path)
    except (OSError, ValueError):
        _emit("ingest_failed", message=_CONFIG_INVALID_MESSAGE)
        return EXIT_FAILED
    except Exception:  # nooit een stacktrace op stdout — altijd vertaald
        _emit("ingest_failed", message=_unexpected_error_message(args.dry_run))
        return EXIT_FAILED

    try:
        report = service.run(
            source=args.source,
            client=args.client,
            project=args.project,
            dry_run=args.dry_run,
            progress_callback=_progress_callback,
        )
    except _IngestCancelled:
        _emit("ingest_cancelled")
        return EXIT_CANCELLED
    except FfprobeNotFoundError:
        _emit("ingest_failed", message=_FFPROBE_MISSING_MESSAGE)
        return EXIT_FAILED
    except OSError:
        _emit("ingest_failed", message=_SOURCE_UNREADABLE_MESSAGE)
        return EXIT_FAILED
    except Exception:  # nooit een stacktrace op stdout — altijd vertaald
        _emit("ingest_failed", message=_unexpected_error_message(args.dry_run))
        return EXIT_FAILED

    _emit_assets(report)
    summary = _corrected_summary(report)
    _emit("ingest_completed", **dataclasses.asdict(summary))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())

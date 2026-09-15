"""Tests for many_ingest.ingest_worker — the worker entrypoint for BOTH a
real ingest and a preview/dry-run (see that module's docstring).

Deliberately Qt-free: this worker must never import PySide6, so these tests
never do either — the QProcess side of the boundary (launching, cancelling,
crash handling) is tested separately in test_desktop_ingest_process.py.

Every test here runs the worker as a REAL subprocess
(`sys.executable -m many_ingest.ingest_worker ...`) — the actual boundary the
desktop app crosses via QProcess (see desktop/ingest_process.py) — never by
calling `ingest_worker.main()` in-process, so these tests prove the real
stdout contract (JSON-lines framing, exit codes), not just the internal
Python logic.

All tests use small, synthetic, temporary files (a few bytes/KB of fake
"video" data) — never real production footage, per this round's testing
rules.

Fase 3.5 (Dynamic Destination Selection): `tmp_path` itself is used as the
`--destination` for every test here — config.yaml only holds the relative
layout (`footage_subpath`/`manifest_subpath`/`log_subpath`, see config.py),
resolved against that destination_root. This keeps every existing path
assertion below unchanged (`tmp_path / "storage" / ...` etc.) — only the
config's own content and the worker command changed shape.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

# Test-only: pytest's tmp_path is always one physical device (no portable way
# to fake a second one without real external hardware — see
# ingest_worker.py). Every test here uses tmp_path for both source and
# destination, so this must be set for every test EXCEPT the ones that
# specifically prove the same-device rejection itself.
_ALLOW_SAME_DEVICE_ENV = {"MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS": "1"}

CAMERA_PROFILES_PATH = (
    Path(__file__).resolve().parents[2]
    / "modules"
    / "many_ingest"
    / "config"
    / "camera_profiles.yaml"
)


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: storage\nmanifest_subpath: asset_schema.json\nlog_subpath: logs\n"
    )
    return config_path


def _worker_command(
    source: Path,
    client: str,
    project: str,
    config_path: Path,
    *,
    destination_root: Path,
    dry_run: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "many_ingest.ingest_worker",
        "--source",
        str(source),
        "--client",
        client,
        "--project",
        project,
        "--destination",
        str(destination_root),
        "--config",
        str(config_path),
        "--camera-profiles",
        str(CAMERA_PROFILES_PATH),
        "--mode",
        "copy",
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def _run_worker(
    tmp_path: Path,
    source: Path,
    *,
    client: str = "Nike",
    project: str = "Zomer",
    config_path: Path | None = None,
    destination_root: Path | None = None,
    dry_run: bool = False,
    timeout: int = 30,
    allow_same_device: bool = True,
):
    config_path = config_path or _write_config(tmp_path)
    destination_root = destination_root or tmp_path
    # Expliciet verwijderen, niet alleen "niet toevoegen": andere testmodules
    # in dezelfde pytest-sessie kunnen dit via os.environ.setdefault(...) al
    # in het GEDEELDE procesomgeving hebben gezet (env-vars zijn proces-breed,
    # niet per testmodule) — alleen weglaten uit een nieuw dict zou die
    # eerder gezette waarde niet ongedaan maken.
    env = dict(os.environ)
    if allow_same_device:
        env.update(_ALLOW_SAME_DEVICE_ENV)
    else:
        env.pop("MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS", None)
    result = subprocess.run(
        _worker_command(
            source, client, project, config_path, destination_root=destination_root, dry_run=dry_run
        ),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return result, lines


def _today() -> str:
    return dt.date.today().isoformat()


def test_worker_emits_ingest_started_as_the_first_line(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"x" * 100)

    result, lines = _run_worker(tmp_path, input_dir)

    assert lines, "worker gaf geen enkele JSON-regel terug"
    assert lines[0] == {
        "event": "ingest_started",
        "source": str(input_dir),
        "client": "Nike",
        "project": "Zomer",
    }
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


def test_progress_events_are_valid_json_with_expected_fields(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(3):
        (input_dir / f"C{i:04d}.MP4").write_bytes(b"x" * 1000)

    _, lines = _run_worker(tmp_path, input_dir)

    progress_events = [line for line in lines if line["event"] == "progress"]
    assert len(progress_events) == 3
    for i, event in enumerate(progress_events, start=1):
        assert set(event) == {"event", "processed", "total", "current_file", "bytes_processed"}
        assert event["processed"] == i
        assert event["total"] == 3


def test_asset_processed_is_streamed_per_file_not_batched_after_completion(tmp_path):
    """Regression test for the Fase 3 realtime-streaming change: each
    asset_processed event must appear on the stream BEFORE ingest_completed
    (interleaved with progress, per file), not all at once afterwards — and
    exactly once per file, matching what desktop/main_window.py's
    safety-stop counter needs to be able to react while the run is still
    going."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(3):
        (input_dir / f"C{i:04d}.MP4").write_bytes(b"x" * 1000)

    _, lines = _run_worker(tmp_path, input_dir)

    asset_events = [line for line in lines if line["event"] == "asset_processed"]
    assert len(asset_events) == 3, "elk bestand moet precies één keer streamen"
    assert {e["source_path"] for e in asset_events} == {
        str(input_dir / f"C{i:04d}.MP4") for i in range(3)
    }

    completed_index = next(i for i, line in enumerate(lines) if line["event"] == "ingest_completed")
    asset_indices = [i for i, line in enumerate(lines) if line["event"] == "asset_processed"]
    assert all(i < completed_index for i in asset_indices), (
        "asset_processed moet vóór ingest_completed binnenkomen, niet erna in bulk"
    )


def test_successful_ingest_copies_real_files_and_reports_a_matching_summary(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    result, lines = _run_worker(tmp_path, input_dir)

    assert result.returncode == 0
    destination = tmp_path / "storage" / "Klanten" / "Nike" / "Zomer"
    copied_files = list(destination.rglob("DJI_0001.MP4"))
    assert len(copied_files) == 1
    assert copied_files[0].read_bytes() == b"fake video bytes"

    completed = next(line for line in lines if line["event"] == "ingest_completed")
    assert completed["total_files"] == 1
    assert completed["errors"] == 0
    assert completed["safe_to_delete_source"] is True

    asset_events = [line for line in lines if line["event"] == "asset_processed"]
    assert len(asset_events) == 1
    assert asset_events[0]["outcome"] == "copied"
    assert asset_events[0]["is_duplicate"] is False


def test_duplicate_handling_is_reported_on_a_second_ingest(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    _run_worker(tmp_path, input_dir, config_path=config_path)  # eerste, echte run
    _, lines = _run_worker(tmp_path, input_dir, config_path=config_path)  # zelfde bestand nogmaals

    completed = next(line for line in lines if line["event"] == "ingest_completed")
    assert completed["duplicates"] == 1

    asset_events = [line for line in lines if line["event"] == "asset_processed"]
    assert asset_events[0]["is_duplicate"] is True
    assert asset_events[0]["outcome"] == "duplicate_skipped"


def test_name_conflict_gets_an_automatic_suffix_and_never_overwrites(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    # Zet handmatig een ANDER bestand klaar op precies de plek waar de engine
    # dit bestand naartoe zou kopiëren — de exacte botsing die de
    # collision-protection in IngestService (ongewijzigd, zie
    # core/ingest_service.py) moet opvangen.
    destination_dir = (
        tmp_path / "storage" / "Klanten" / "Nike" / "Zomer" / f"{_today()}_Raw" / "Drone"
    )
    destination_dir.mkdir(parents=True)
    conflicting_path = destination_dir / "DJI_0001.MP4"
    conflicting_path.write_bytes(b"heel andere inhoud")

    _, lines = _run_worker(tmp_path, input_dir, config_path=config_path)

    completed = next(line for line in lines if line["event"] == "ingest_completed")
    assert completed["name_conflicts_resolved"] == 1

    # Het bestaande bestand is nooit overschreven:
    assert conflicting_path.read_bytes() == b"heel andere inhoud"
    # Het nieuwe bestand kreeg een automatische _001-suffix:
    renamed = destination_dir / "DJI_0001_001.MP4"
    assert renamed.exists()
    assert renamed.read_bytes() == b"fake video bytes"


def test_unreadable_source_file_aborts_with_a_friendly_message_not_a_stacktrace(tmp_path):
    """"Bron losgekoppeld tijdens ingest", gesimuleerd deterministisch als
    een bestand dat halverwege de scan onleesbaar blijkt — de engine leest
    de checksum van elk bestand vóór het kopiëren, dus dit reproduceert
    hetzelfde OSError-pad als een schijf die halverwege wegvalt."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"readable")
    unreadable = input_dir / "DJI_0002.MP4"
    unreadable.write_bytes(b"unreadable")
    unreadable.chmod(0o000)
    try:
        result, lines = _run_worker(tmp_path, input_dir)
    finally:
        unreadable.chmod(0o644)  # opruimen, anders kan tmp_path niet weggegooid worden

    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    failed = next(line for line in lines if line["event"] == "ingest_failed")
    assert failed["message"]
    assert result.returncode == 1


def test_unwritable_destination_marks_the_asset_as_failed_but_finishes_the_run(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    storage_root.chmod(0o555)
    config_path = _write_config(tmp_path)
    try:
        result, lines = _run_worker(tmp_path, input_dir, config_path=config_path)
    finally:
        storage_root.chmod(0o755)

    assert "Traceback" not in result.stdout
    completed = next(line for line in lines if line["event"] == "ingest_completed")
    assert completed["errors"] == 1
    assert completed["safe_to_delete_source"] is False

    asset_events = [line for line in lines if line["event"] == "asset_processed"]
    assert asset_events[0]["outcome"] == "failed_verification"
    assert asset_events[0]["error"]


def test_cancel_via_sigterm_stops_gracefully_and_reports_ingest_cancelled(tmp_path):
    """Annuleren (SIGTERM, zoals QProcess.terminate() op macOS/Linux stuurt —
    zie desktop/ingest_process.py) moet het huidige bestand laten afronden en
    daarna netjes stoppen, nooit een crash of een onafgemaakte run zonder
    eindevent."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(50):
        (input_dir / f"C{i:04d}.MP4").write_bytes(b"x" * 2_000_000)
    config_path = _write_config(tmp_path)

    process = subprocess.Popen(
        _worker_command(input_dir, "Nike", "Zomer", config_path, destination_root=tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ, **_ALLOW_SAME_DEVICE_ENV),
    )
    lines: list[dict] = []
    try:
        lines.append(json.loads(process.stdout.readline()))
        assert lines[0]["event"] == "ingest_started"

        # asset_processed en progress worden nu per bestand geïnterleaved
        # (zie ingest_worker.py's asset_callback) — lees door tot de eerste
        # progress-regel, ongeacht of asset_processed er nog vóór staat.
        while lines[-1]["event"] != "progress":
            lines.append(json.loads(process.stdout.readline()))

        process.send_signal(signal.SIGTERM)
        remaining_stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    for line in remaining_stdout.splitlines():
        if line.strip():
            lines.append(json.loads(line))

    assert "Traceback" not in stderr
    assert lines[-1]["event"] == "ingest_cancelled"
    assert process.returncode == 2


# -- Fase 3.5: destination-schijf --------------------------------------------------


def test_worker_rejects_source_and_destination_on_the_same_physical_device(tmp_path):
    """De harde safety rule (device_identity.py): bron en bestemming mogen
    nooit dezelfde fysieke schijf zijn. `tmp_path` is hier bewust zowel de
    ouder van de bron als de --destination — op één lokaal filesystem dus
    hetzelfde st_dev, wat exact is wat dit moet blokkeren."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    result, lines = _run_worker(
        tmp_path,
        input_dir,
        config_path=config_path,
        destination_root=tmp_path,
        allow_same_device=False,
    )

    assert result.returncode == 1
    failed = next(line for line in lines if line["event"] == "ingest_failed")
    assert "dezelfde fysieke schijf" in failed["message"]
    assert not (tmp_path / "storage").exists()


def test_worker_rejects_same_device_for_dry_run_too(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    result, lines = _run_worker(
        tmp_path,
        input_dir,
        config_path=config_path,
        destination_root=tmp_path,
        dry_run=True,
        allow_same_device=False,
    )

    assert result.returncode == 1
    failed = next(line for line in lines if line["event"] == "ingest_failed")
    assert "dezelfde fysieke schijf" in failed["message"]


def test_destination_unavailable_gives_a_destination_specific_message_for_a_real_run(tmp_path):
    """Fase 3.5's DestinationUnavailableError moet een melding geven die de
    BESTEMMING noemt, niet de bron — het exacte, eerder gevonden mislabeling-
    probleem, nu structureel gefixt via een aparte except-tak in
    ingest_worker.py."""
    real_source_parent = tmp_path / "source_disk"
    real_source_parent.mkdir()
    input_dir = real_source_parent / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    destination_root = tmp_path / "destination_disk"
    destination_root.mkdir()
    destination_root.chmod(0o555)  # onbereikbaar voor schrijven
    config_path = _write_config(tmp_path)
    try:
        result, lines = _run_worker(
            tmp_path, input_dir, config_path=config_path, destination_root=destination_root
        )
    finally:
        destination_root.chmod(0o755)

    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    failed = next(line for line in lines if line["event"] == "ingest_failed")
    assert "bestemmingsschijf" in failed["message"]
    assert result.returncode == 1


def test_destination_unavailable_does_not_block_a_preview(tmp_path):
    real_source_parent = tmp_path / "source_disk"
    real_source_parent.mkdir()
    input_dir = real_source_parent / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    destination_root = tmp_path / "destination_disk"
    destination_root.mkdir()
    destination_root.chmod(0o555)
    config_path = _write_config(tmp_path)
    try:
        result, lines = _run_worker(
            tmp_path,
            input_dir,
            config_path=config_path,
            destination_root=destination_root,
            dry_run=True,
        )
    finally:
        destination_root.chmod(0o755)

    assert result.returncode == 0, result.stderr
    completed = next(line for line in lines if line["event"] == "ingest_completed")
    assert completed["dry_run"] is True
    assert completed["total_files"] == 1


# -- preview / --dry-run: same worker, same protocol, nothing written -------------


def test_dry_run_worker_completes_without_copying_anything(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    result, lines = _run_worker(tmp_path, input_dir, dry_run=True)

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    completed = next(line for line in lines if line["event"] == "ingest_completed")
    assert completed["dry_run"] is True
    assert completed["total_files"] == 1
    # Het hele punt van een preview: niets wordt geschreven.
    assert not (tmp_path / "storage").exists()


def test_dry_run_succeeds_over_the_real_worker_when_log_dir_is_unreachable(tmp_path):
    """End-to-end regression test (real subprocess, real JSON-lines) for the
    manual-testing bug: a readable source must produce a successful preview
    even when the destination's log_dir can't be reached — see
    test_ingest_service.py's equivalent for the engine-level proof; this
    proves the same thing through the actual worker boundary. Here
    `storage_subpath`/`manifest_subpath` stay reachable but `log_subpath`
    resolves into a directory whose parent is read-only."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    unreachable_parent = tmp_path / "unreachable"
    unreachable_parent.mkdir()
    unreachable_parent.chmod(0o555)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: storage\n"
        "manifest_subpath: asset_schema.json\n"
        "log_subpath: unreachable/logs\n"
    )
    try:
        result, lines = _run_worker(tmp_path, input_dir, config_path=config_path, dry_run=True)
    finally:
        unreachable_parent.chmod(0o755)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stdout
    completed = next(line for line in lines if line["event"] == "ingest_completed")
    assert completed["dry_run"] is True
    assert completed["total_files"] == 1
    assert not (unreachable_parent / "logs").exists()  # dry-run schreef niets naar log_dir


def test_real_ingest_shows_a_friendly_message_not_a_crash_when_log_dir_is_unreachable(tmp_path):
    """A real ingest still genuinely needs to write to log_dir, so it must
    still fail — cleanly, never a crash or a raw stacktrace — when the
    destination isn't reachable. Only the dry-run/preview path was fixed.
    (storage_subpath is reachable here — this exercises ActionLogger's own
    mkdir specifically, not the new destination-root pre-flight check.)"""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    unreachable_parent = tmp_path / "unreachable"
    unreachable_parent.mkdir()
    unreachable_parent.chmod(0o555)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "footage_subpath: storage\n"
        "manifest_subpath: asset_schema.json\n"
        "log_subpath: unreachable/logs\n"
    )
    try:
        result, lines = _run_worker(tmp_path, input_dir, config_path=config_path)  # dry_run=False
    finally:
        unreachable_parent.chmod(0o755)

    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    failed = next(line for line in lines if line["event"] == "ingest_failed")
    assert failed["message"]
    assert result.returncode == 1


def test_dry_run_worker_reports_the_corrected_duplicate_count(tmp_path):
    """Regression test for the fix moved into `_corrected_summary()` (see
    ingest_worker.py's docstring, moved unchanged from the old QThread-era
    desktop/controller.py) — `summarize()` itself always reports 0
    duplicates for a dry run, because `_process_asset` sets `outcome =
    PREVIEW` before ever checking `is_duplicate`. This proves the worker's
    `ingest_completed` event carries the corrected count, not the raw
    (always-0) one."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    _run_worker(tmp_path, input_dir, config_path=config_path)  # eerste, echte run
    _, lines = _run_worker(tmp_path, input_dir, config_path=config_path, dry_run=True)

    completed = next(line for line in lines if line["event"] == "ingest_completed")
    assert completed["duplicates"] == 1

    asset_events = [line for line in lines if line["event"] == "asset_processed"]
    assert asset_events[0]["is_duplicate"] is True


def test_dry_run_unreadable_source_aborts_with_a_friendly_message_not_a_stacktrace(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    unreadable = input_dir / "DJI_0002.MP4"
    unreadable.write_bytes(b"unreadable")
    unreadable.chmod(0o000)
    try:
        result, lines = _run_worker(tmp_path, input_dir, dry_run=True)
    finally:
        unreadable.chmod(0o644)

    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    failed = next(line for line in lines if line["event"] == "ingest_failed")
    assert failed["message"]
    assert result.returncode == 1
    assert not (tmp_path / "storage").exists()


def test_dry_run_cancel_via_sigterm_stops_gracefully_and_reports_ingest_cancelled(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(50):
        (input_dir / f"C{i:04d}.MP4").write_bytes(b"x" * 2_000_000)
    config_path = _write_config(tmp_path)

    process = subprocess.Popen(
        _worker_command(
            input_dir, "Nike", "Zomer", config_path, destination_root=tmp_path, dry_run=True
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ, **_ALLOW_SAME_DEVICE_ENV),
    )
    lines: list[dict] = []
    try:
        lines.append(json.loads(process.stdout.readline()))
        assert lines[0]["event"] == "ingest_started"

        while lines[-1]["event"] != "progress":
            lines.append(json.loads(process.stdout.readline()))

        process.send_signal(signal.SIGTERM)
        remaining_stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    for line in remaining_stdout.splitlines():
        if line.strip():
            lines.append(json.loads(line))

    assert "Traceback" not in stderr
    assert lines[-1]["event"] == "ingest_cancelled"
    assert process.returncode == 2
    assert not (tmp_path / "storage").exists()


# -- Fase 4: human-readable .txt report, same format cli.py has always written ----


def _report_path_from(completed: dict) -> Path:
    log_path = Path(completed["log_path"])
    return log_path.with_name(f"{log_path.stem}_report.txt")


def test_real_ingest_writes_the_same_human_readable_report_cli_has_always_written(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    result, lines = _run_worker(tmp_path, input_dir, client="Nike", project="Zomer")
    assert result.returncode == 0

    completed = next(line for line in lines if line["event"] == "ingest_completed")
    report_path = _report_path_from(completed)

    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "✅ INGEST VOLTOOID" in text
    assert "Zomer" in text
    assert "Veilig om bronmedia te verwijderen:\nJA" in text


def test_destination_full_is_never_labeled_source_unreadable_and_never_completes(
    tmp_path, monkeypatch, capsys
):
    """Fase 4.1 (2026-09-15 forensic audit): the whole point of introducing
    `DestinationFullError` is that a full destination must never be
    presented as "Kon deze locatie niet meer lezen" — and, since the whole
    run aborts, must never emit `ingest_completed` either (which is what
    ultimately gates `safe_to_delete_source`/the eject button in the GUI —
    see desktop/main_window.py's `_resolve_eject_targets`). A real ENOSPC
    can't be forced through a real subprocess without actually filling a
    real disk (forbidden by this round's testing rules), so — unlike every
    other test in this file — this one calls `ingest_worker.main()`
    in-process, with `build_ingest_service` monkeypatched to return a stub
    whose `.run()` raises `DestinationFullError` directly. This still proves
    the real thing that matters: `main()`'s own except-clause dispatch."""
    from many_ingest import ingest_worker
    from many_ingest.core.ingest_service import DestinationFullError

    class _StubService:
        def run(self, **kwargs):
            raise DestinationFullError(
                "Onvoldoende ruimte op de bestemmingsschijf.\n"
                "Benodigd: 1.0 GB\n"
                "Beschikbaar: 200.0 MB"
            )

    monkeypatch.setattr(ingest_worker, "build_ingest_service", lambda *a, **k: _StubService())
    monkeypatch.setenv("MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS", "1")

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    config_path = _write_config(tmp_path)

    exit_code = ingest_worker.main(
        [
            "--source",
            str(input_dir),
            "--client",
            "Nike",
            "--project",
            "Zomer",
            "--destination",
            str(tmp_path),
            "--config",
            str(config_path),
            "--camera-profiles",
            str(CAMERA_PROFILES_PATH),
            "--mode",
            "copy",
        ]
    )

    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    failed = next(l for l in lines if l["event"] == "ingest_failed")

    assert exit_code == ingest_worker.EXIT_FAILED
    assert "Onvoldoende ruimte" in failed["message"]
    assert "Kon deze locatie niet meer lezen" not in failed["message"]
    assert not any(l["event"] == "ingest_completed" for l in lines)


def test_dry_run_never_writes_a_report_file(tmp_path):
    """A preview must stay strictly read-only (same guarantee ActionLogger
    already gives the JSONL log for a dry-run) — writing the .txt report
    unconditionally, like cli.py does, would reintroduce the exact
    'destination unreachable' failure mode Fase 3.5's dry-run logging guard
    exists to prevent for the desktop app's preview flow."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")

    result, lines = _run_worker(tmp_path, input_dir, dry_run=True)
    assert result.returncode == 0

    completed = next(line for line in lines if line["event"] == "ingest_completed")
    report_path = _report_path_from(completed)

    assert not report_path.exists()
    assert not (tmp_path / "storage").exists()

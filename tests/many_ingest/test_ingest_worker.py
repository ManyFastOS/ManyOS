"""Tests for many_ingest.ingest_worker — the Fase 3 real-ingest worker
entrypoint (see that module's docstring).

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
"""

from __future__ import annotations

import datetime as dt
import json
import signal
import subprocess
import sys
from pathlib import Path

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
        f"storage_root: {tmp_path / 'storage'}\n"
        f"manifest_path: {tmp_path / 'asset_schema.json'}\n"
        f"log_dir: {tmp_path / 'logs'}\n"
    )
    return config_path


def _worker_command(
    source: Path, client: str, project: str, config_path: Path
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "many_ingest.ingest_worker",
        "--source",
        str(source),
        "--client",
        client,
        "--project",
        project,
        "--config",
        str(config_path),
        "--camera-profiles",
        str(CAMERA_PROFILES_PATH),
        "--mode",
        "copy",
    ]


def _run_worker(
    tmp_path: Path,
    source: Path,
    *,
    client: str = "Nike",
    project: str = "Zomer",
    config_path: Path | None = None,
    timeout: int = 30,
):
    config_path = config_path or _write_config(tmp_path)
    result = subprocess.run(
        _worker_command(source, client, project, config_path),
        capture_output=True,
        text=True,
        timeout=timeout,
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
        _worker_command(input_dir, "Nike", "Zomer", config_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines: list[dict] = []
    try:
        lines.append(json.loads(process.stdout.readline()))
        assert lines[0]["event"] == "ingest_started"

        lines.append(json.loads(process.stdout.readline()))
        assert lines[1]["event"] == "progress", (
            "test-aanname: de run moet nog aantoonbaar bezig zijn om te kunnen annuleren"
        )

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

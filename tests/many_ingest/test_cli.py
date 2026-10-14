"""End-to-end tests for the Many Ingest CLI."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from many_ingest.cli import main

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


def _bypass_same_device_check(monkeypatch) -> None:
    """Fase 3.5's hard safety rule (source and destination may never be the
    same physical disk) is real and correctly enforced — but these tests use
    `tmp_path` for both, which is necessarily one physical device on any
    single-disk test machine (see device_identity.py's module docstring).
    Tests that aren't about that rule specifically bypass it here; the rule
    itself is tested directly below, in isolation, using the real check.

    Two separate checks, both bypassed here: cli.py's own, shallow check
    against the raw `destination_root` (monkeypatched directly, since it's
    called before `build_ingest_service()` even runs), and
    `service_factory.build_ingest_service()`'s own, deeper check against the
    fully resolved `storage_root` (Fase 5.3.2 follow-up review, point 3) —
    bypassed via the same test-only env var `ingest_worker.py`'s tests
    already use (device_identity.py's `ALLOW_SAME_DEVICE_ENV_VAR`), since
    that check lives in a different module's own `same_physical_device`
    reference, not reachable via the monkeypatch above."""
    monkeypatch.setattr("many_ingest.cli.same_physical_device", lambda a, b: False)
    monkeypatch.setenv("MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS", "1")


def test_dry_run_then_real_run_then_duplicate_skip(tmp_path, monkeypatch):
    _bypass_same_device_check(monkeypatch)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    common_args = [
        "run",
        "--source", str(input_dir),
        "--destination", str(tmp_path),
        "--client", "Nike",
        "--project", "Zomer",
        "--config", str(config_path),
        "--camera-profiles", str(CAMERA_PROFILES_PATH),
    ]

    dry_run_result = runner.invoke(main, common_args + ["--dry-run"])
    assert dry_run_result.exit_code == 0, dry_run_result.output
    assert "Preview" in dry_run_result.output
    assert not (tmp_path / "storage").exists()

    real_result = runner.invoke(main, common_args)
    assert real_result.exit_code == 0, real_result.output
    assert "1 gekopieerd" in real_result.output

    second_result = runner.invoke(main, common_args)
    assert second_result.exit_code == 0, second_result.output
    assert "1 duplicaten overgeslagen" in second_result.output
    assert "INGEST VOLTOOID" in second_result.output
    assert "Veilig om bronmedia te verwijderen:" in second_result.output


def test_run_exits_cleanly_with_clear_message_when_ffprobe_missing(tmp_path, monkeypatch):
    _bypass_same_device_check(monkeypatch)
    monkeypatch.setattr("many_ingest.core.ingest_service.is_ffprobe_available", lambda: False)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--source", str(input_dir),
            "--destination", str(tmp_path),
            "--client", "Nike",
            "--project", "Zomer",
            "--dry-run",
            "--config", str(config_path),
            "--camera-profiles", str(CAMERA_PROFILES_PATH),
        ],
    )

    assert result.exit_code == 1
    assert "ffprobe" in result.output
    assert "brew install ffmpeg" in result.output
    assert not (tmp_path / "storage").exists()


def test_real_run_persists_a_readable_report_file_next_to_the_log(tmp_path, monkeypatch):
    _bypass_same_device_check(monkeypatch)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--source", str(input_dir),
            "--destination", str(tmp_path),
            "--client", "Nike",
            "--project", "Zomer",
            "--config", str(config_path),
            "--camera-profiles", str(CAMERA_PROFILES_PATH),
        ],
    )

    assert result.exit_code == 0, result.output
    report_files = list((tmp_path / "logs").glob("*_report.txt"))
    assert len(report_files) == 1
    assert "INGEST VOLTOOID" in report_files[0].read_text(encoding="utf-8")


def test_run_requires_destination(tmp_path):
    """Fase 3.5: --destination is verplicht, geen legacy-terugval naar een
    oude, absolute storage_root-config-sleutel — één architectuur."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--source", str(input_dir),
            "--client", "Nike",
            "--project", "Zomer",
            "--dry-run",
            "--config", str(config_path),
            "--camera-profiles", str(CAMERA_PROFILES_PATH),
        ],
    )

    assert result.exit_code != 0
    assert "destination" in result.output.lower()


def test_run_rejects_source_and_destination_on_the_same_physical_device(tmp_path):
    """De echte safety-net-check (geen monkeypatch hier) — bron en gekozen
    bestemming vallen in deze test allebei onder tmp_path, dus letterlijk
    hetzelfde fysieke device, precies wat dit moet blokkeren."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--source", str(input_dir),
            "--destination", str(tmp_path),
            "--client", "Nike",
            "--project", "Zomer",
            "--dry-run",
            "--config", str(config_path),
            "--camera-profiles", str(CAMERA_PROFILES_PATH),
        ],
    )

    assert result.exit_code != 0
    assert "dezelfde fysieke schijf" in result.output
    assert not (tmp_path / "storage").exists()


def test_run_rejects_unsafe_client_name_with_clear_message_and_nonzero_exit(tmp_path, monkeypatch):
    """Fase 5.3.2: InvalidWorkspaceNameError (raised by
    IngestService.run()'s own name validation) must be translated to a
    clear, non-traceback message and a non-zero exit code — same pattern
    cli.py already uses for FfprobeNotFoundError/DestinationFullError/
    DestinationUnavailableError. Checked for a dry-run, which already
    reaches this validation before anything else."""
    _bypass_same_device_check(monkeypatch)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--source", str(input_dir),
            "--destination", str(tmp_path),
            "--client", "../../../../tmp/pwn",
            "--project", "Zomer",
            "--dry-run",
            "--config", str(config_path),
            "--camera-profiles", str(CAMERA_PROFILES_PATH),
        ],
    )

    assert result.exit_code != 0
    assert "Klantnaam" in result.output
    assert "Traceback" not in result.output
    assert not (tmp_path / "storage").exists()


def test_run_requires_non_empty_client_and_project(tmp_path, monkeypatch):
    """Click's required=True only proves the flag was passed, not that its
    value is non-empty — --client "" must still be rejected by the service's
    own validation, with a clear message, not silently accepted."""
    _bypass_same_device_check(monkeypatch)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--source", str(input_dir),
            "--destination", str(tmp_path),
            "--client", "",
            "--project", "Zomer",
            "--dry-run",
            "--config", str(config_path),
            "--camera-profiles", str(CAMERA_PROFILES_PATH),
        ],
    )

    assert result.exit_code != 0
    assert "Klantnaam" in result.output
    assert not (tmp_path / "storage").exists()

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
        f"storage_root: {tmp_path / 'storage'}\n"
        f"manifest_path: {tmp_path / 'asset_schema.json'}\n"
        f"log_dir: {tmp_path / 'logs'}\n"
    )
    return config_path


def test_dry_run_then_real_run_then_duplicate_skip(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "DJI_0001.MP4").write_bytes(b"fake video bytes")
    config_path = _write_config(tmp_path)

    runner = CliRunner()
    common_args = [
        "run",
        "--source", str(input_dir),
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


def test_real_run_persists_a_readable_report_file_next_to_the_log(tmp_path):
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
            "--config", str(config_path),
            "--camera-profiles", str(CAMERA_PROFILES_PATH),
        ],
    )

    assert result.exit_code == 0, result.output
    report_files = list((tmp_path / "logs").glob("*_report.txt"))
    assert len(report_files) == 1
    assert "INGEST VOLTOOID" in report_files[0].read_text(encoding="utf-8")

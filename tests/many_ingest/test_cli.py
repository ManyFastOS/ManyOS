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

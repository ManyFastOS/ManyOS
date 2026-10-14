"""Tests for many_ingest.service_factory.build_ingest_service() — the
shared composition root cli.py and ingest_worker.py both use (see that
module's docstring).

Fase 5.3.2 follow-up review, point 3: build_ingest_service() independently
re-checks the Fase 3.5 hard safety rule (source and destination may never
be the same physical disk) against the FULLY RESOLVED storage_root, not
just the raw destination_root a caller already checked beforehand — closing
a gap where a symlink inside footage_subpath (config.yaml's relative
layout) could redirect storage_root back onto the source disk even though
destination_root itself is genuinely a different device.

Tests here monkeypatch `many_ingest.service_factory.same_physical_device`
directly (rather than planting real cross-device symlinks, which an
automated test has no portable way to do without real external hardware —
same reasoning device_identity.py's module docstring already gives for the
`MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS` escape hatch) to deterministically
simulate "the resolved storage_root turned out to be the same physical
device as source."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from many_ingest import service_factory as service_factory_module
from many_ingest.core.ingest_service import DestinationUnavailableError
from many_ingest.service_factory import build_ingest_service

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


def test_rejects_when_resolved_storage_root_shares_the_source_device(tmp_path, monkeypatch):
    """Simulates a symlink inside footage_subpath redirecting storage_root
    back onto the source disk — same_physical_device(source, storage_root)
    being True must raise DestinationUnavailableError, even though nothing
    here ever checks (or needs to check) destination_root directly.

    Explicitly clears the test-only override env var first: several desktop
    integration test modules (test_desktop_ingest_process.py,
    test_desktop_preview_process.py, test_desktop_main_window_ingest.py) set
    it at IMPORT time via `os.environ.setdefault(...)`, not via `monkeypatch`
    — so it is never automatically undone, and persists for the rest of the
    pytest session once any of those modules has been collected. This test
    must not depend on collection order to see the real rejection."""
    monkeypatch.delenv("MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS", raising=False)
    config_path = _write_config(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    destination_root = tmp_path / "destination"
    destination_root.mkdir()

    monkeypatch.setattr(service_factory_module, "same_physical_device", lambda a, b: True)

    with pytest.raises(DestinationUnavailableError):
        build_ingest_service(config_path, CAMERA_PROFILES_PATH, destination_root, source)


def test_dry_run_callers_get_the_same_rejection_as_real_run_callers(tmp_path, monkeypatch):
    """build_ingest_service() has no dry_run parameter at all — cli.py and
    ingest_worker.py call it identically for both modes (the same
    "no second implementation" pattern used everywhere else), so this one
    check necessarily applies uniformly to both. Documented here explicitly
    since the check is unconditional, unlike destination-availability
    checks such as _ensure_destination_is_writable()."""
    monkeypatch.delenv("MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS", raising=False)
    config_path = _write_config(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    destination_root = tmp_path / "destination"
    destination_root.mkdir()

    monkeypatch.setattr(service_factory_module, "same_physical_device", lambda a, b: True)

    # Nothing about dry-run is passed to build_ingest_service() at all —
    # this proves the rejection does not depend on, or vary by, a caller's
    # eventual dry_run choice for the subsequent service.run() call.
    with pytest.raises(DestinationUnavailableError):
        build_ingest_service(config_path, CAMERA_PROFILES_PATH, destination_root, source)


def test_the_test_only_override_bypasses_the_check(tmp_path, monkeypatch):
    """The same escape hatch ingest_worker.py's own, shallower check already
    uses (device_identity.ALLOW_SAME_DEVICE_ENV_VAR) must also bypass this
    deeper check — otherwise every existing CLI/worker test that relies on
    it (tmp_path is necessarily one physical device) would start failing."""
    config_path = _write_config(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    destination_root = tmp_path / "destination"
    destination_root.mkdir()

    monkeypatch.setattr(service_factory_module, "same_physical_device", lambda a, b: True)
    monkeypatch.setenv("MANY_INGEST_ALLOW_SAME_DEVICE_FOR_TESTS", "1")

    service = build_ingest_service(config_path, CAMERA_PROFILES_PATH, destination_root, source)
    assert service is not None


def test_genuinely_different_devices_succeed_normally(tmp_path, monkeypatch):
    """The ordinary, legitimate case: storage_root really is on a different
    physical device than source — build_ingest_service() must still wire up
    a working IngestService, not reject it."""
    config_path = _write_config(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    destination_root = tmp_path / "destination"
    destination_root.mkdir()

    monkeypatch.setattr(service_factory_module, "same_physical_device", lambda a, b: False)

    service = build_ingest_service(config_path, CAMERA_PROFILES_PATH, destination_root, source)
    assert service is not None

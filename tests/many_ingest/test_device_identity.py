"""Tests for device_identity.py — the shared physical-device check used by
the GUI, CLI, and worker (Fase 3.5). Real devices can't be faked on a normal
single-disk test machine, so the "different device" cases use monkeypatched
`Path.stat` results rather than real hardware — the same-device cases use
real temp directories, since that's exactly what a single test machine
naturally provides.
"""

from __future__ import annotations

import os
from pathlib import Path

from many_ingest.device_identity import device_id, same_physical_device


def test_device_id_of_an_existing_path(tmp_path):
    assert device_id(tmp_path) == tmp_path.stat().st_dev


def test_device_id_walks_up_to_an_existing_ancestor_for_a_missing_path(tmp_path):
    missing = tmp_path / "not" / "created" / "yet"
    assert device_id(missing) == tmp_path.stat().st_dev


def test_device_id_of_a_deeply_nonexistent_path_falls_back_to_the_root_device():
    # De wortel (`/`) bestaat op elk POSIX-systeem altijd — er is dus geen
    # praktisch bereikbaar pad waarvoor device_id() echt None teruggeeft;
    # dit bevestigt expliciet dat het omhoog-lopen uiteindelijk daar landt.
    assert device_id(Path("/this/path/should/never/exist/anywhere")) == Path("/").stat().st_dev


def test_same_physical_device_is_true_for_two_paths_on_the_same_disk(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b" / "c"
    a.mkdir()
    assert same_physical_device(a, b) is True


def test_same_physical_device_is_false_when_devices_differ(tmp_path, monkeypatch):
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if str(path).endswith("other_disk"):
            return os.stat_result(
                (result.st_mode, result.st_ino, 999_999, *tuple(result)[3:])
            )
        return result

    monkeypatch.setattr(os, "stat", fake_stat)

    same = tmp_path / "same"
    other = tmp_path / "other_disk"
    same.mkdir()
    other.mkdir()

    assert same_physical_device(same, other) is False


def test_same_physical_device_is_false_when_either_device_id_is_unknown(monkeypatch):
    """`device_id()` returning None (no reachable ancestor at all) is not
    achievable on a normal POSIX system — `/` always exists — but
    `same_physical_device()`'s own handling of that case is still worth
    proving directly, in isolation from device_id()'s real filesystem walk."""
    import many_ingest.device_identity as device_identity_module

    monkeypatch.setattr(device_identity_module, "device_id", lambda path: None)
    assert same_physical_device(Path("/a"), Path("/b")) is False

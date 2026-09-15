"""Tests for desktop/eject.py — Fase 4's safe-eject abstraction.

Runs headless (QT_QPA_PLATFORM=offscreen). Skips cleanly when PySide6 isn't
installed. Never really ejects a disk: the default `diskutil eject <source>`
invocation is only inspected via `EjectRunner.command()`, without ever
calling `.start()` on it (see `test_the_default_command_targets_diskutil_...`
below) — every test that actually runs a process uses `_command_override` to
point at a small, disposable Python one-liner instead, the same seam
test_desktop_ingest_process.py already uses for `IngestRunner`.

Device-identity checks (source-vs-destination, source-vs-boot) are tested
via an injected `is_same_physical_device` fake — an automated test has no
portable way to fake a second real physical disk, so this mirrors the exact
dependency-injection convention desktop/volumes.py already uses for
`is_boot_volume`/`is_destination_volume`, for the same reason.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from many_ingest.desktop import eject


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait_until(predicate, qapp, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        qapp.processEvents()
        time.sleep(0.01)
    return predicate()


class _Collector(QObject):
    done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.succeeded = False
        self.failed_message: str | None = None

    def on_succeeded(self) -> None:
        self.succeeded = True
        self.done.emit()

    def on_failed(self, message: str) -> None:
        self.failed_message = message
        self.done.emit()


def _never_same_device(_a: Path, _b: Path) -> bool:
    return False


def _device_map_comparator(same_device_groups: list[set[Path]]):
    """Builds an `is_same_physical_device`-shaped fake from explicit groups
    of paths that share one physical device — clearer for these tests than
    ad-hoc if/else comparisons, and mirrors what the real `st_dev`-based
    check conceptually does (paths compare equal only if they resolve to
    the same underlying device)."""

    def _same(a: Path, b: Path) -> bool:
        a, b = Path(a), Path(b)
        return any(a in group and b in group for group in same_device_groups)

    return _same


# -- resolve_volume_root: derives the real volume-root a chosen (sub)folder is on --
#
# Regression coverage for the 2026-09-15 follow-up audit: a fully successful,
# verified real ingest from a manually chosen source submap (e.g.
# /Volumes/Sharpwaves/Test) never offered eject at all, because `source`
# itself wasn't a volume-root. `resolve_volume_root` fixes this by deriving
# the real volume-root via device identity — never by string-prefix/name
# matching, and never by simply returning `source.parent`.


def test_resolve_volume_root_finds_the_real_root_for_a_chosen_submap(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    sharpwaves = volumes_root / "Sharpwaves"
    sharpwaves.mkdir()
    (volumes_root / "Chris").mkdir()  # een andere, niet-matchende schijf ernaast
    submap = sharpwaves / "Test"
    submap.mkdir()

    same_device = _device_map_comparator([{submap, sharpwaves}])

    result = eject.resolve_volume_root(submap, volumes_root, is_same_physical_device=same_device)

    assert result == sharpwaves  # de root, nooit de submap zelf


def test_resolve_volume_root_returns_the_root_itself_when_source_is_already_the_root(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    sharpwaves = volumes_root / "Sharpwaves"
    sharpwaves.mkdir()

    same_device = _device_map_comparator([{sharpwaves}])

    result = eject.resolve_volume_root(sharpwaves, volumes_root, is_same_physical_device=same_device)

    assert result == sharpwaves


def test_resolve_volume_root_returns_none_for_a_folder_outside_volumes(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    (volumes_root / "Sharpwaves").mkdir()
    local_folder = tmp_path / "Users" / "chris" / "Desktop" / "LocalFolder"
    local_folder.mkdir(parents=True)

    result = eject.resolve_volume_root(
        local_folder, volumes_root, is_same_physical_device=_never_same_device
    )

    assert result is None


def test_resolve_volume_root_returns_none_when_the_source_is_no_longer_mounted(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    (volumes_root / "OtherDisk").mkdir()  # niets dat matcht

    unmounted_source = tmp_path / "used-to-be-mounted-here"  # bestaat niet meer

    result = eject.resolve_volume_root(
        unmounted_source, volumes_root, is_same_physical_device=_never_same_device
    )

    assert result is None


def test_resolve_volume_root_returns_none_when_volumes_root_is_not_a_directory(tmp_path):
    missing_volumes_root = tmp_path / "does-not-exist"

    result = eject.resolve_volume_root(tmp_path / "anything", missing_volumes_root)

    assert result is None


def test_resolved_root_matching_the_destination_device_is_still_refused_by_can_eject(tmp_path):
    """resolve_volume_root() only finds a candidate — it never itself
    decides eject-eligibility. can_eject() must still refuse the resolved
    root when it turns out to be the destination device."""
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    sharpwaves = volumes_root / "Sharpwaves"
    sharpwaves.mkdir()
    submap = sharpwaves / "Test"
    submap.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()

    same_device = _device_map_comparator([{submap, sharpwaves, destination}])

    resolved = eject.resolve_volume_root(submap, volumes_root, is_same_physical_device=same_device)
    assert resolved == sharpwaves  # resolutie lukt

    assert (
        eject.can_eject(
            resolved, destination, volumes_root=volumes_root, is_same_physical_device=same_device
        )
        is False
    )


def test_resolved_root_matching_the_boot_device_is_still_refused_by_can_eject(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    macintosh_hd = volumes_root / "Macintosh HD"
    macintosh_hd.mkdir()
    boot_path = tmp_path / "boot"
    boot_path.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()

    same_device = _device_map_comparator([{macintosh_hd, boot_path}])

    resolved = eject.resolve_volume_root(
        macintosh_hd, volumes_root, is_same_physical_device=same_device
    )
    assert resolved == macintosh_hd

    assert (
        eject.can_eject(
            resolved,
            destination,
            volumes_root=volumes_root,
            boot_path=boot_path,
            is_same_physical_device=same_device,
        )
        is False
    )


def test_eject_runner_command_for_a_resolved_root_never_contains_the_original_submap():
    """Proves the final diskutil invocation uses exactly whatever `source`
    EjectRunner is constructed with. MainWindow's job (see
    test_desktop_main_window_ingest.py's
    test_eject_uses_the_resolved_volume_root_not_the_originally_chosen_submap)
    is to make sure that's always the resolved volume-root — this proves
    that once it is, the submap name never leaks into the actual command."""
    volumes_root = Path("/Volumes")
    resolved_root = volumes_root / "Sharpwaves"
    destination = volumes_root / "Chris"

    runner = eject.EjectRunner(resolved_root, destination, volumes_root=volumes_root)

    assert runner.command()[1:] == ["eject", str(resolved_root)]
    assert "Test" not in runner.command()[-1]


# -- is_ejectable_volume_root: real volume-root vs. a manually chosen folder ------


def test_a_volumes_root_child_is_an_ejectable_volume_root(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    source = volumes_root / "SD_CARD_1"
    assert eject.is_ejectable_volume_root(source, volumes_root) is True


def test_a_subfolder_of_a_volume_is_not_an_ejectable_volume_root(tmp_path):
    """A manually chosen source folder (via the file picker) that lives
    inside a volume, but isn't the volume itself, must never be handed to
    `diskutil eject` — see this module's docstring."""
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    source = volumes_root / "SD_CARD_1" / "DCIM"
    assert eject.is_ejectable_volume_root(source, volumes_root) is False


def test_an_arbitrary_manually_chosen_folder_is_not_an_ejectable_volume_root(tmp_path):
    source = tmp_path / "Users" / "chris" / "Desktop" / "ManualFolder"
    assert eject.is_ejectable_volume_root(source, tmp_path / "Volumes") is False


def test_volumes_root_itself_is_not_ejectable(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    assert eject.is_ejectable_volume_root(volumes_root, volumes_root) is False


# -- validate_eject / can_eject: destination and boot volume are always refused ----


def test_validate_eject_refuses_when_source_is_not_a_volume_root(tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    source = volumes_root / "SD_CARD_1" / "DCIM"
    destination = tmp_path / "destination"

    with pytest.raises(eject.EjectRefused, match="uitwerpbare schijf"):
        eject.validate_eject(
            source, destination, volumes_root=volumes_root, is_same_physical_device=_never_same_device
        )


def test_validate_eject_refuses_the_destination_device():
    volumes_root = Path("/Volumes")
    source = volumes_root / "SD_CARD_1"
    destination = Path("/Volumes/Chris")

    def same_as_destination(a: Path, b: Path) -> bool:
        return b == destination

    with pytest.raises(eject.EjectRefused, match="bestemming"):
        eject.validate_eject(
            source, destination, volumes_root=volumes_root, is_same_physical_device=same_as_destination
        )


def test_can_eject_returns_false_for_the_destination_device():
    volumes_root = Path("/Volumes")
    source = volumes_root / "SD_CARD_1"
    destination = Path("/Volumes/Chris")

    assert (
        eject.can_eject(
            source, destination, volumes_root=volumes_root, is_same_physical_device=lambda a, b: True
        )
        is False
    )


def test_validate_eject_refuses_the_boot_volume_even_when_destination_check_passes():
    """The boot check must fire independently of the destination check —
    proven here by a fake comparator that only reports 'same device' for the
    boot path, never for the (different) destination."""
    volumes_root = Path("/Volumes")
    boot_path = Path("/")
    source = volumes_root / "Macintosh HD"
    destination = Path("/Volumes/Chris")

    def only_matches_boot(a: Path, b: Path) -> bool:
        return b == boot_path

    with pytest.raises(eject.EjectRefused, match="opstartschijf"):
        eject.validate_eject(
            source,
            destination,
            volumes_root=volumes_root,
            boot_path=boot_path,
            is_same_physical_device=only_matches_boot,
        )


def test_can_eject_is_true_when_every_check_passes():
    volumes_root = Path("/Volumes")
    source = volumes_root / "SD_CARD_1"
    destination = Path("/Volumes/Chris")

    assert (
        eject.can_eject(
            source, destination, volumes_root=volumes_root, is_same_physical_device=_never_same_device
        )
        is True
    )


# -- EjectRunner: the real (non-overridden) command, never -force -----------------


def test_the_default_command_targets_diskutil_eject_with_exactly_the_source_and_never_force():
    volumes_root = Path("/Volumes")
    source = volumes_root / "SD_CARD_1"
    destination = Path("/Volumes/Chris")

    runner = eject.EjectRunner(source, destination, volumes_root=volumes_root)
    command = runner.command()

    assert command[1:] == ["eject", str(source)]
    assert "-force" not in command
    assert "--force" not in command


# -- EjectRunner via _command_override: success / failure / refusal never crash ---


def test_eject_succeeds_when_the_process_exits_zero(qapp, tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    source = volumes_root / "SD_CARD_1"
    destination = tmp_path / "destination"

    collector = _Collector()
    runner = eject.EjectRunner(
        source,
        destination,
        volumes_root=volumes_root,
        is_same_physical_device=_never_same_device,
        _command_override=[sys.executable, "-c", "import sys; sys.exit(0)"],
    )
    runner.succeeded.connect(collector.on_succeeded)
    runner.failed.connect(collector.on_failed)
    runner.start()

    assert _wait_until(lambda: collector.succeeded or collector.failed_message, qapp)
    assert collector.succeeded is True
    assert collector.failed_message is None


def test_eject_failure_eg_resource_busy_gives_a_friendly_message_and_never_a_stacktrace(qapp, tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    source = volumes_root / "SD_CARD_1"
    destination = tmp_path / "destination"

    collector = _Collector()
    runner = eject.EjectRunner(
        source,
        destination,
        volumes_root=volumes_root,
        is_same_physical_device=_never_same_device,
        _command_override=[sys.executable, "-c", "import sys; sys.exit(1)"],
    )
    runner.succeeded.connect(collector.on_succeeded)
    runner.failed.connect(collector.on_failed)
    runner.start()

    assert _wait_until(lambda: collector.succeeded or collector.failed_message, qapp)
    assert collector.succeeded is False
    assert collector.failed_message
    assert "Traceback" not in collector.failed_message
    assert "diskutil" not in collector.failed_message.lower()


def test_eject_crash_emits_failed_and_never_raises(qapp, tmp_path):
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    source = volumes_root / "SD_CARD_1"
    destination = tmp_path / "destination"

    collector = _Collector()
    runner = eject.EjectRunner(
        source,
        destination,
        volumes_root=volumes_root,
        is_same_physical_device=_never_same_device,
        _command_override=[
            sys.executable,
            "-c",
            "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)",
        ],
    )
    runner.succeeded.connect(collector.on_succeeded)
    runner.failed.connect(collector.on_failed)
    runner.start()

    assert _wait_until(lambda: collector.succeeded or collector.failed_message, qapp)
    assert collector.succeeded is False
    assert collector.failed_message
    assert "Traceback" not in collector.failed_message
    assert "SIGSEGV" not in collector.failed_message


def test_eject_runner_that_never_starts_emits_failed(qapp):
    collector = _Collector()
    runner = eject.EjectRunner(
        Path("/Volumes/SD_CARD_1"),
        Path("/Volumes/Chris"),
        is_same_physical_device=_never_same_device,
        _command_override=["/pad/dat/gegarandeerd/niet/bestaat/diskutil"],
    )
    runner.succeeded.connect(collector.on_succeeded)
    runner.failed.connect(collector.on_failed)
    runner.start()

    assert _wait_until(lambda: collector.succeeded or collector.failed_message, qapp)
    assert collector.succeeded is False
    assert collector.failed_message


def test_start_refuses_the_destination_device_before_ever_running_a_process(qapp, tmp_path):
    """The runner-level defense-in-depth check (never trust that a caller's
    own gating was correct) — even with a command override that would
    otherwise succeed instantly, `start()` must never run it if `validate_eject`
    itself refuses."""
    volumes_root = tmp_path / "Volumes"
    volumes_root.mkdir()
    source = volumes_root / "SD_CARD_1"
    destination = tmp_path / "destination"

    collector = _Collector()
    runner = eject.EjectRunner(
        source,
        destination,
        volumes_root=volumes_root,
        is_same_physical_device=lambda a, b: True,  # alles is "dezelfde schijf"
        _command_override=[sys.executable, "-c", "import sys; sys.exit(0)"],
    )
    runner.succeeded.connect(collector.on_succeeded)
    runner.failed.connect(collector.on_failed)
    runner.start()

    assert _wait_until(lambda: collector.succeeded or collector.failed_message, qapp)
    assert collector.succeeded is False
    assert "bestemming" in collector.failed_message


# -- start_eject(): the injected-starter convenience wrapper -----------------------


def test_start_eject_wires_signals_and_starts_immediately(qapp):
    # Geen tmp_path/eigen volumes_root hier: start_eject() geeft geen
    # volumes_root door (dat is precies de standaard-bekabeling die
    # main_window.py ook gebruikt), dus de echte /Volumes-standaard geldt —
    # is_ejectable_volume_root() raakt de schijf zelf niet aan (geen
    # bestaans-check), dus dit hoeft geen echt gemount pad te zijn.
    source = Path("/Volumes/SD_CARD_1")
    destination = Path("/Volumes/Chris")

    collector = _Collector()
    runner = eject.start_eject(
        source,
        destination,
        on_succeeded=collector.on_succeeded,
        on_failed=collector.on_failed,
        is_same_physical_device=_never_same_device,
        _command_override=[sys.executable, "-c", "import sys; sys.exit(0)"],
    )

    assert _wait_until(lambda: collector.succeeded or collector.failed_message, qapp)
    assert collector.succeeded is True
    assert runner is not None  # referentie levend houden tot hier (zie QProcess-lifetime-les elders)
